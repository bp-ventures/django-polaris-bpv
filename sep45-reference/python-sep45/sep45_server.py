import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
import xdrlib3
from flask import Flask, jsonify, request
from stellar_sdk import Address, Keypair, Network, SorobanServer, TransactionBuilder, auth, scval, xdr
from stellar_sdk.soroban_rpc import AuthMode
from stellar_sdk.sep.stellar_toml import fetch_stellar_toml

LOG = logging.getLogger("sep45")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class ApiError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Settings:
    rpc_url: str
    home_domains: list[str]
    web_auth_contract_id: str
    sep10_signing_seed: str
    source_signing_seed: str | None
    jwt_secret: str
    web_auth_domain: str | None
    auth_timeout: int
    jwt_timeout: int
    network_passphrase: str
    port: int

    @property
    def signing_keypair(self) -> Keypair:
        return Keypair.from_secret(self.sep10_signing_seed)

    @property
    def signing_public_key(self) -> str:
        return self.signing_keypair.public_key

    @property
    def source_keypair(self) -> Keypair:
        seed = self.source_signing_seed or self.sep10_signing_seed
        return Keypair.from_secret(seed)

    @property
    def source_public_key(self) -> str:
        return self.source_keypair.public_key


class NonceStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def issue(self, account: str, timeout_seconds: int) -> str:
        nonce = secrets.token_hex(16)
        expires_at = time.time() + timeout_seconds
        with self._lock:
            self._items[(account, nonce)] = expires_at
        return nonce

    def consume(self, account: str, nonce: str) -> bool:
        now = time.time()
        key = (account, nonce)
        with self._lock:
            expired = [k for k, exp in self._items.items() if exp <= now]
            for exp_key in expired:
                self._items.pop(exp_key, None)
            expires_at = self._items.get(key)
            if expires_at is None or expires_at <= now:
                return False
            self._items.pop(key, None)
        return True


NONCES = NonceStore()


def load_settings() -> Settings:
    rpc_url = os.getenv("STELLAR_NETWORK_RPC_URL")
    home_domains_raw = os.getenv("SEP45_HOME_DOMAINS")
    contract_id = os.getenv("SEP45_WEB_AUTH_CONTRACT_ID")
    signing_seed = os.getenv("SECRET_SEP10_SIGNING_SEED")
    source_seed = os.getenv("SEP45_SOURCE_SIGNING_SEED")
    jwt_secret = os.getenv("SECRET_SEP45_JWT_SECRET")

    missing = []
    if not rpc_url:
        missing.append("STELLAR_NETWORK_RPC_URL")
    if not home_domains_raw:
        missing.append("SEP45_HOME_DOMAINS")
    if not contract_id:
        missing.append("SEP45_WEB_AUTH_CONTRACT_ID")
    if not signing_seed:
        missing.append("SECRET_SEP10_SIGNING_SEED")
    if not jwt_secret:
        missing.append("SECRET_SEP45_JWT_SECRET")
    if missing:
        raise RuntimeError(f"missing required env vars: {', '.join(missing)}")

    home_domains = [item.strip() for item in home_domains_raw.split(",") if item.strip()]
    if not home_domains:
        raise RuntimeError("SEP45_HOME_DOMAINS cannot be empty")

    Address(contract_id)
    Keypair.from_secret(signing_seed)
    if source_seed:
        Keypair.from_secret(source_seed)

    return Settings(
        rpc_url=rpc_url,
        home_domains=home_domains,
        web_auth_contract_id=contract_id,
        sep10_signing_seed=signing_seed,
        source_signing_seed=source_seed,
        jwt_secret=jwt_secret,
        web_auth_domain=os.getenv("SEP45_WEB_AUTH_DOMAIN"),
        auth_timeout=int(os.getenv("SEP45_AUTH_TIMEOUT", "900")),
        jwt_timeout=int(os.getenv("SEP45_JWT_TIMEOUT", "86400")),
        network_passphrase=os.getenv("STELLAR_NETWORK_PASSPHRASE", Network.TESTNET_NETWORK_PASSPHRASE),
        port=int(os.getenv("PORT", "8080")),
    )


def create_app(settings: Settings | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SEP45_SETTINGS"] = settings or load_settings()

    @app.after_request
    def add_cors_headers(response):  # type: ignore[no-untyped-def]
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):  # type: ignore[no-untyped-def]
        LOG.info("event=request_failed status=%s error=%s", error.status_code, error.message)
        return jsonify({"error": error.message}), error.status_code

    @app.route("/sep45/auth", methods=["GET", "POST", "OPTIONS"])
    @app.route("/challenge", methods=["GET", "POST", "OPTIONS"])
    def auth_endpoint():  # type: ignore[no-untyped-def]
        if request.method == "OPTIONS":
            return ("", 204)
        if request.method == "GET":
            return handle_get_auth(app.config["SEP45_SETTINGS"])
        return handle_post_auth(app.config["SEP45_SETTINGS"])

    return app


def handle_get_auth(settings: Settings):
    account = request.args.get("account", "").strip()
    if not account:
        raise ApiError("no 'account' provided", 400)
    validate_contract_account(account)

    home_domain = select_home_domain(request.args.get("home_domain"), settings.home_domains)
    web_auth_domain = resolve_web_auth_domain(settings, request.host)

    client_domain = request.args.get("client_domain")
    client_domain_account = None
    if client_domain:
        validate_hostname(client_domain, "client_domain")
        client_domain_account = get_client_domain_signing_key(client_domain)

    nonce = NONCES.issue(account, settings.auth_timeout)
    args_map = build_args_map(
        account=account,
        home_domain=home_domain,
        web_auth_domain=web_auth_domain,
        web_auth_domain_account=settings.signing_public_key,
        nonce=nonce,
        client_domain=client_domain,
        client_domain_account=client_domain_account,
    )

    entries = build_challenge_entries(settings, args_map)
    payload = {
        "authorization_entries": encode_auth_entries(entries),
        "network_passphrase": settings.network_passphrase,
    }
    LOG.info("event=challenge_issued account=%s home_domain=%s client_domain=%s", account, home_domain, client_domain)
    return jsonify(payload), 200


def handle_post_auth(settings: Settings):
    entries_b64 = get_authorization_entries_from_request()
    if not entries_b64:
        raise ApiError("'authorization_entries' is required", 400)

    entries = decode_auth_entries(entries_b64)
    expected_web_auth_domain = resolve_web_auth_domain(settings, request.host)
    current_ledger = get_latest_ledger_sequence(settings)
    args_native, args_map = validate_auth_entries(entries, settings, expected_web_auth_domain, current_ledger)

    nonce = str(args_native["nonce"])
    account = str(args_native["account"])
    if not NONCES.consume(account, nonce):
        raise ApiError("invalid or expired nonce", 400)

    verify_entries_with_simulation(settings, args_map, entries)
    token = issue_jwt(settings, args_native, entries_b64)
    LOG.info(
        "event=token_issued account=%s home_domain=%s client_domain=%s",
        account,
        args_native.get("home_domain"),
        args_native.get("client_domain"),
    )
    return jsonify({"token": token}), 200


def validate_contract_account(account: str) -> None:
    if not account.startswith("C"):
        raise ApiError("invalid 'account': expected contract account (C...)", 400)
    try:
        Address(account)
    except Exception as exc:
        raise ApiError(f"invalid 'account': {account}") from exc


def validate_hostname(value: str, field_name: str) -> None:
    if urlparse(f"https://{value}").netloc != value:
        raise ApiError(f"'{field_name}' must be a valid hostname", 400)


def domain_matches(pattern: str, actual: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return actual.endswith(suffix) and actual != pattern[2:]
    return pattern == actual


def select_home_domain(home_domain: str | None, allowed: list[str]) -> str:
    if not home_domain:
        raise ApiError("'home_domain' is required", 400)
    for pattern in allowed:
        if domain_matches(pattern, home_domain):
            return home_domain
    raise ApiError(f"invalid 'home_domain' value. Accepted values: {allowed}", 400)


def get_authorization_entries_from_request() -> str:
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        return str(request.form.get("authorization_entries", "")).strip()
    if content_type == "application/json":
        body = request.get_json(silent=True) or {}
        return str(body.get("authorization_entries", "")).strip()

    # Fallback for clients that omit/alter content type.
    body = request.get_json(silent=True) or {}
    value = str(body.get("authorization_entries", "")).strip()
    if value:
        return value
    return str(request.form.get("authorization_entries", "")).strip()


def get_latest_ledger_sequence(settings: Settings) -> int:
    rpc = SorobanServer(settings.rpc_url)
    return rpc.get_latest_ledger().sequence


def resolve_web_auth_domain(settings: Settings, request_host: str) -> str:
    if settings.web_auth_domain:
        return settings.web_auth_domain
    if len(settings.home_domains) == 1 and "*" not in settings.home_domains[0]:
        return settings.home_domains[0]
    return request_host


def get_client_domain_signing_key(client_domain: str) -> str:
    try:
        toml_data = fetch_stellar_toml(client_domain, use_http=client_domain.startswith("localhost"))
    except Exception as exc:
        raise ApiError("unable to fetch 'client_domain' SIGNING_KEY", 400) from exc

    signing_key = toml_data.get("SIGNING_KEY")
    if not signing_key:
        raise ApiError("SIGNING_KEY not present on 'client_domain' TOML", 400)
    try:
        Keypair.from_public_key(signing_key)
    except Exception as exc:
        raise ApiError("invalid SIGNING_KEY value on 'client_domain' TOML", 400) from exc
    return signing_key


def build_args_map(
    *,
    account: str,
    home_domain: str,
    web_auth_domain: str,
    web_auth_domain_account: str,
    nonce: str,
    client_domain: str | None,
    client_domain_account: str | None,
) -> xdr.SCVal:
    entries: dict[xdr.SCVal, xdr.SCVal] = {
        scval.to_symbol("account"): scval.to_string(account),
        scval.to_symbol("home_domain"): scval.to_string(home_domain),
        scval.to_symbol("nonce"): scval.to_string(nonce),
        scval.to_symbol("web_auth_domain"): scval.to_string(web_auth_domain),
        scval.to_symbol("web_auth_domain_account"): scval.to_string(web_auth_domain_account),
    }
    if client_domain and client_domain_account:
        entries[scval.to_symbol("client_domain")] = scval.to_string(client_domain)
        entries[scval.to_symbol("client_domain_account")] = scval.to_string(client_domain_account)
    return scval.to_map(entries)


def build_challenge_entries(settings: Settings, args_map: xdr.SCVal) -> list[xdr.SorobanAuthorizationEntry]:
    rpc = SorobanServer(settings.rpc_url)
    source = rpc.load_account(settings.source_public_key)
    tx = (
        TransactionBuilder(source, network_passphrase=settings.network_passphrase, base_fee=100)
        .append_invoke_contract_function_op(
            contract_id=settings.web_auth_contract_id,
            function_name="web_auth_verify",
            parameters=[args_map],
        )
        .set_timeout(settings.auth_timeout)
        .build()
    )

    simulation = rpc.simulate_transaction(tx)
    if simulation.error:
        raise ApiError(f"transaction simulation failed: {simulation.error}", 400)
    if not simulation.results or not simulation.results[0].auth:
        raise ApiError("transaction simulation returned no authorization entries", 400)

    entries = [xdr.SorobanAuthorizationEntry.from_xdr(item) for item in simulation.results[0].auth]
    return sign_server_entry(settings, rpc, entries)


def sign_server_entry(
    settings: Settings,
    rpc: SorobanServer,
    entries: list[xdr.SorobanAuthorizationEntry],
) -> list[xdr.SorobanAuthorizationEntry]:
    latest = rpc.get_latest_ledger()
    valid_until = latest.sequence + 10
    signed: list[xdr.SorobanAuthorizationEntry] = []

    for entry in entries:
        if entry.credentials.type != xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS:
            signed.append(entry)
            continue
        address = Address.from_xdr_sc_address(entry.credentials.address.address).address
        if address != settings.signing_public_key:
            signed.append(entry)
            continue
        signed.append(
            auth.authorize_entry(
                entry=entry,
                signer=settings.signing_keypair,
                valid_until_ledger_sequence=valid_until,
                network_passphrase=settings.network_passphrase,
            )
        )
    return signed


def encode_auth_entries(entries: list[xdr.SorobanAuthorizationEntry]) -> str:
    packer = xdrlib3.Packer()
    packer.pack_uint(len(entries))
    for entry in entries:
        entry.pack(packer)
    return base64.b64encode(packer.get_buffer()).decode()


def decode_auth_entries(payload_b64: str) -> list[xdr.SorobanAuthorizationEntry]:
    try:
        raw = base64.b64decode(payload_b64, validate=True)
    except Exception as exc:
        raise ApiError("invalid base64 in 'authorization_entries'", 400) from exc

    unpacker = xdrlib3.Unpacker(raw)
    try:
        count = unpacker.unpack_uint()
        if count <= 0:
            raise ApiError("authorization_entries cannot be empty", 400)
        items = [xdr.SorobanAuthorizationEntry.unpack(unpacker) for _ in range(count)]
        if unpacker.get_position() != len(raw):
            raise ApiError("invalid authorization_entries payload", 400)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("unable to parse authorization_entries XDR", 400) from exc
    return items


def validate_auth_entries(
    entries: list[xdr.SorobanAuthorizationEntry],
    settings: Settings,
    expected_web_auth_domain: str,
    current_ledger_sequence: int,
) -> tuple[dict[str, Any], xdr.SCVal]:
    arg_maps: list[dict[str, Any]] = []
    arg_scvals: list[xdr.SCVal] = []
    auth_addresses: set[str] = set()

    for entry in entries:
        if entry.credentials.type == xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS:
            auth_addresses.add(Address.from_xdr_sc_address(entry.credentials.address.address).address)
            signature_expiration = entry.credentials.address.signature_expiration_ledger.uint32
            if signature_expiration <= current_ledger_sequence:
                raise ApiError("authorization entry signature has expired", 400)

        if entry.root_invocation.sub_invocations:
            raise ApiError("authorization entry contains sub-invocations", 400)
        if entry.root_invocation.function.type != xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN:
            raise ApiError("authorization entry function type is not contract_fn", 400)

        fn = entry.root_invocation.function.contract_fn
        contract_address = Address.from_xdr_sc_address(fn.contract_address).address
        if contract_address != settings.web_auth_contract_id:
            raise ApiError("authorization entry contract_address mismatch", 400)
        function_name = fn.function_name.sc_symbol
        if isinstance(function_name, bytes):
            function_name = function_name.decode()
        if function_name != "web_auth_verify":
            raise ApiError("authorization entry function_name mismatch", 400)
        if len(fn.args) != 1:
            raise ApiError("authorization entry args must contain one map argument", 400)

        arg_scvals.append(fn.args[0])
        arg_native = scval.to_native(fn.args[0])
        if not isinstance(arg_native, dict):
            raise ApiError("authorization entry args must be a map", 400)
        arg_maps.append(arg_native)

    first = json.dumps(arg_maps[0], sort_keys=True)
    for current in arg_maps[1:]:
        if json.dumps(current, sort_keys=True) != first:
            raise ApiError("authorization entry args mismatch", 400)

    args = arg_maps[0]
    required = {"account", "home_domain", "nonce", "web_auth_domain", "web_auth_domain_account"}
    missing = required.difference(args.keys())
    if missing:
        missing_sorted = ", ".join(sorted(missing))
        raise ApiError(f"missing required args: {missing_sorted}", 400)

    account = str(args["account"])
    validate_contract_account(account)
    home_domain = str(args["home_domain"])
    if not any(domain_matches(pattern, home_domain) for pattern in settings.home_domains):
        raise ApiError("home_domain argument is not accepted", 400)

    if str(args["web_auth_domain"]) != expected_web_auth_domain:
        raise ApiError("web_auth_domain argument mismatch", 400)
    if str(args["web_auth_domain_account"]) != settings.signing_public_key:
        raise ApiError("web_auth_domain_account argument mismatch", 400)

    has_client_domain = "client_domain" in args
    has_client_domain_account = "client_domain_account" in args
    if has_client_domain != has_client_domain_account:
        raise ApiError("client_domain and client_domain_account must appear together", 400)

    if settings.signing_public_key not in auth_addresses:
        raise ApiError("missing server authorization entry", 400)
    if account not in auth_addresses:
        raise ApiError("missing client authorization entry", 400)
    if has_client_domain and str(args["client_domain_account"]) not in auth_addresses:
        raise ApiError("missing client_domain authorization entry", 400)

    return args, arg_scvals[0]


def verify_entries_with_simulation(
    settings: Settings,
    args_map: xdr.SCVal,
    entries: list[xdr.SorobanAuthorizationEntry],
) -> None:
    rpc = SorobanServer(settings.rpc_url)
    source = rpc.load_account(settings.source_public_key)
    tx = (
        TransactionBuilder(source, network_passphrase=settings.network_passphrase, base_fee=100)
        .append_invoke_contract_function_op(
            contract_id=settings.web_auth_contract_id,
            function_name="web_auth_verify",
            parameters=[args_map],
            auth=entries,
        )
        .set_timeout(settings.auth_timeout)
        .build()
    )
    simulation = rpc.simulate_transaction(tx, auth_mode=AuthMode.ENFORCE)
    if simulation.error:
        raise ApiError(f"transaction simulation failed: {simulation.error}", 400)


def issue_jwt(settings: Settings, args: dict[str, Any], entries_b64: str) -> str:
    now = int(time.time())
    claims = {
        "iss": str(args["web_auth_domain"]),
        "sub": str(args["account"]),
        "iat": now,
        "exp": now + settings.jwt_timeout,
        "jti": hashlib.sha256(entries_b64.encode()).hexdigest(),
        "home_domain": str(args["home_domain"]),
    }
    if "client_domain" in args:
        claims["client_domain"] = str(args["client_domain"])
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


try:
    app = create_app()
except Exception:  # pragma: no cover - env may be incomplete in test import contexts.
    app = None


if __name__ == "__main__":
    runtime_app = create_app()
    cfg: Settings = runtime_app.config["SEP45_SETTINGS"]
    runtime_app.run(host="0.0.0.0", port=cfg.port)
