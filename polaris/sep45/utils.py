"""SEP-45 protocol helpers.

The view layer stays small; this module owns the protocol mechanics so each piece
can be unit-tested in isolation. Mirrors the canonical reference implementation in
``sep45-reference/python-sep45/sep45_server.py`` but adapts to Polaris/Django:

- Durable nonce store via :class:`polaris.models.WebAuthNonce`; PK is the nonce string,
  atomic-consume is a single ``UPDATE ... WHERE id=? AND used=False AND expires_at>now``.
- Settings are read from :mod:`polaris.settings`.
- JWTs are signed with ``SEP45_JWT_SECRET`` (separate from ``SERVER_JWT_KEY``).
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from datetime import timedelta
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import jwt
import xdrlib3
from django.utils import timezone
from stellar_sdk import (
    Address,
    Keypair,
    SorobanServer,
    TransactionBuilder,
    auth,
    scval,
    xdr,
)
from stellar_sdk.soroban_rpc import AuthMode
from stellar_sdk.sep.stellar_toml import fetch_stellar_toml

from polaris import settings
from polaris.models import WebAuthNonce


logger = logging.getLogger(__name__)

# Constants — see Anchor Platform Sep45Service.java:97/183-185
MAX_AUTHORIZATION_ENTRIES_SIZE = 100 * 1024
SIGNATURE_EXPIRATION_LEDGER_OFFSET = 10
AUTH_TIMEOUT_SECONDS = 900
JWT_TIMEOUT_SECONDS = 24 * 60 * 60


class SEP45ValidationError(ValueError):
    """Raised when a SEP-45 request fails validation. Maps to HTTP 400."""


# ---------- Hostname / home-domain validation ----------------------------------


def validate_hostname(value: str, field_name: str) -> None:
    if urlparse(f"https://{value}").netloc != value:
        raise SEP45ValidationError(f"'{field_name}' must be a valid hostname")


def domain_matches(pattern: str, actual: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return actual.endswith(suffix) and actual != pattern[2:]
    return pattern == actual


def select_home_domain(home_domain: Optional[str], allowed: list[str]) -> str:
    if not home_domain:
        raise SEP45ValidationError("'home_domain' is required")
    for pattern in allowed:
        if domain_matches(pattern, home_domain):
            return home_domain
    raise SEP45ValidationError(
        f"invalid 'home_domain' value. Accepted values: {allowed}"
    )


def resolve_web_auth_domain(request_host: str) -> str:
    return urlparse(settings.HOST_URL).netloc or request_host


def validate_contract_account(account: str) -> None:
    if not account or not account.startswith("C"):
        raise SEP45ValidationError(
            "invalid 'account': expected contract account (C...)"
        )
    try:
        Address(account)
    except Exception as exc:
        raise SEP45ValidationError(f"invalid 'account': {account}") from exc


# ---------- Nonce store --------------------------------------------------------


def issue_nonce(timeout_seconds: int = AUTH_TIMEOUT_SECONDS) -> str:
    nonce = secrets.token_hex(16)
    WebAuthNonce.objects.create(
        id=nonce,
        expires_at=timezone.now() + timedelta(seconds=timeout_seconds),
    )
    return nonce


def consume_nonce(nonce: str) -> bool:
    now = timezone.now()
    updated = WebAuthNonce.objects.filter(
        id=nonce, used=False, expires_at__gt=now
    ).update(used=True, used_at=now)
    return updated == 1


# ---------- Client-domain TOML fetch -------------------------------------------


def get_client_domain_signing_key(client_domain: str) -> str:
    try:
        use_http = client_domain.startswith("localhost")
        toml_data = fetch_stellar_toml(client_domain, use_http=use_http)
    except Exception as exc:
        raise SEP45ValidationError(
            "unable to fetch 'client_domain' SIGNING_KEY"
        ) from exc

    signing_key = toml_data.get("SIGNING_KEY")
    if not signing_key:
        raise SEP45ValidationError("SIGNING_KEY not present on 'client_domain' TOML")
    try:
        Keypair.from_public_key(signing_key)
    except Exception as exc:
        raise SEP45ValidationError(
            "invalid SIGNING_KEY value on 'client_domain' TOML"
        ) from exc
    return signing_key


# ---------- Authorization-entry encoding ---------------------------------------


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
        raise SEP45ValidationError(
            "invalid base64 in 'authorization_entries'"
        ) from exc

    unpacker = xdrlib3.Unpacker(raw)
    try:
        count = unpacker.unpack_uint()
        if count <= 0:
            raise SEP45ValidationError("authorization_entries cannot be empty")
        items = [xdr.SorobanAuthorizationEntry.unpack(unpacker) for _ in range(count)]
        if unpacker.get_position() != len(raw):
            raise SEP45ValidationError("invalid authorization_entries payload")
    except SEP45ValidationError:
        raise
    except Exception as exc:
        raise SEP45ValidationError(
            "unable to parse authorization_entries XDR"
        ) from exc
    return items


# ---------- Args map construction ----------------------------------------------


def build_args_map(
    *,
    account: str,
    home_domain: str,
    web_auth_domain: str,
    web_auth_domain_account: str,
    nonce: str,
    client_domain: Optional[str] = None,
    client_domain_account: Optional[str] = None,
) -> xdr.SCVal:
    entries: dict[xdr.SCVal, xdr.SCVal] = {
        scval.to_symbol("account"): scval.to_string(account),
        scval.to_symbol("home_domain"): scval.to_string(home_domain),
        scval.to_symbol("nonce"): scval.to_string(nonce),
        scval.to_symbol("web_auth_domain"): scval.to_string(web_auth_domain),
        scval.to_symbol("web_auth_domain_account"): scval.to_string(
            web_auth_domain_account
        ),
    }
    if client_domain and client_domain_account:
        entries[scval.to_symbol("client_domain")] = scval.to_string(client_domain)
        entries[scval.to_symbol("client_domain_account")] = scval.to_string(
            client_domain_account
        )
    return scval.to_map(entries)


# ---------- Challenge construction (GET) ---------------------------------------


def build_challenge_entries(
    args_map: xdr.SCVal,
) -> list[xdr.SorobanAuthorizationEntry]:
    rpc = SorobanServer(settings.SOROBAN_RPC_URL)
    signing_keypair = Keypair.from_secret(settings.SIGNING_SEED)
    source = rpc.load_account(signing_keypair.public_key)
    tx = (
        TransactionBuilder(
            source,
            network_passphrase=settings.STELLAR_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_invoke_contract_function_op(
            contract_id=settings.SEP45_WEB_AUTH_CONTRACT_ID,
            function_name="web_auth_verify",
            parameters=[args_map],
        )
        .set_timeout(AUTH_TIMEOUT_SECONDS)
        .build()
    )

    simulation = rpc.simulate_transaction(tx)
    if simulation.error:
        raise SEP45ValidationError(
            f"transaction simulation failed: {simulation.error}"
        )
    if not simulation.results or not simulation.results[0].auth:
        raise SEP45ValidationError(
            "transaction simulation returned no authorization entries"
        )

    entries = [
        xdr.SorobanAuthorizationEntry.from_xdr(item)
        for item in simulation.results[0].auth
    ]
    latest = rpc.get_latest_ledger()
    valid_until = latest.sequence + SIGNATURE_EXPIRATION_LEDGER_OFFSET
    signed: list[xdr.SorobanAuthorizationEntry] = []
    for entry in entries:
        if (
            entry.credentials.type
            != xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS
        ):
            signed.append(entry)
            continue
        address = Address.from_xdr_sc_address(
            entry.credentials.address.address
        ).address
        if address != signing_keypair.public_key:
            signed.append(entry)
            continue
        signed.append(
            auth.authorize_entry(
                entry=entry,
                signer=signing_keypair,
                valid_until_ledger_sequence=valid_until,
                network_passphrase=settings.STELLAR_NETWORK_PASSPHRASE,
            )
        )
    return signed


# ---------- Entry validation (POST) --------------------------------------------


def validate_auth_entries(
    entries: list[xdr.SorobanAuthorizationEntry],
    expected_web_auth_domain: str,
    current_ledger_sequence: int,
) -> Tuple[dict[str, Any], xdr.SCVal]:
    if not entries:
        raise SEP45ValidationError("authorization_entries cannot be empty")

    arg_maps: list[dict[str, Any]] = []
    arg_scvals: list[xdr.SCVal] = []
    auth_addresses: set[str] = set()

    for entry in entries:
        if (
            entry.credentials.type
            == xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS
        ):
            auth_addresses.add(
                Address.from_xdr_sc_address(entry.credentials.address.address).address
            )
            signature_expiration = (
                entry.credentials.address.signature_expiration_ledger.uint32
            )
            if signature_expiration <= current_ledger_sequence:
                raise SEP45ValidationError(
                    "authorization entry signature has expired"
                )

        if entry.root_invocation.sub_invocations:
            raise SEP45ValidationError(
                "authorization entry contains sub-invocations"
            )
        if (
            entry.root_invocation.function.type
            != xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN
        ):
            raise SEP45ValidationError(
                "authorization entry function type is not contract_fn"
            )

        fn = entry.root_invocation.function.contract_fn
        contract_address = Address.from_xdr_sc_address(fn.contract_address).address
        if contract_address != settings.SEP45_WEB_AUTH_CONTRACT_ID:
            raise SEP45ValidationError(
                "authorization entry contract_address mismatch"
            )
        function_name = fn.function_name.sc_symbol
        if isinstance(function_name, bytes):
            function_name = function_name.decode()
        if function_name != "web_auth_verify":
            raise SEP45ValidationError("authorization entry function_name mismatch")
        if len(fn.args) != 1:
            raise SEP45ValidationError(
                "authorization entry args must contain one map argument"
            )

        arg_scvals.append(fn.args[0])
        arg_native = scval.to_native(fn.args[0])
        if not isinstance(arg_native, dict):
            raise SEP45ValidationError("authorization entry args must be a map")
        arg_maps.append(arg_native)

    first = json.dumps(arg_maps[0], sort_keys=True)
    for current in arg_maps[1:]:
        if json.dumps(current, sort_keys=True) != first:
            raise SEP45ValidationError("authorization entry args mismatch")

    args = arg_maps[0]
    required = {
        "account",
        "home_domain",
        "nonce",
        "web_auth_domain",
        "web_auth_domain_account",
    }
    missing = required.difference(args.keys())
    if missing:
        missing_sorted = ", ".join(sorted(missing))
        raise SEP45ValidationError(f"missing required args: {missing_sorted}")

    account = str(args["account"])
    validate_contract_account(account)
    home_domain = str(args["home_domain"])
    if not any(
        domain_matches(pattern, home_domain)
        for pattern in settings.SEP10_HOME_DOMAINS
    ):
        raise SEP45ValidationError("home_domain argument is not accepted")

    if str(args["web_auth_domain"]) != expected_web_auth_domain:
        raise SEP45ValidationError("web_auth_domain argument mismatch")
    if str(args["web_auth_domain_account"]) != settings.SIGNING_KEY:
        raise SEP45ValidationError("web_auth_domain_account argument mismatch")

    has_client_domain = "client_domain" in args
    has_client_domain_account = "client_domain_account" in args
    if has_client_domain != has_client_domain_account:
        raise SEP45ValidationError(
            "client_domain and client_domain_account must appear together"
        )

    if settings.SIGNING_KEY not in auth_addresses:
        raise SEP45ValidationError("missing server authorization entry")
    if account not in auth_addresses:
        raise SEP45ValidationError("missing client authorization entry")
    if (
        has_client_domain
        and str(args["client_domain_account"]) not in auth_addresses
    ):
        raise SEP45ValidationError("missing client_domain authorization entry")

    return args, arg_scvals[0]


# ---------- Simulation in ENFORCE mode -----------------------------------------


def verify_entries_with_simulation(
    args_map: xdr.SCVal,
    entries: list[xdr.SorobanAuthorizationEntry],
) -> None:
    rpc = SorobanServer(settings.SOROBAN_RPC_URL)
    signing_keypair = Keypair.from_secret(settings.SIGNING_SEED)
    source = rpc.load_account(signing_keypair.public_key)
    tx = (
        TransactionBuilder(
            source,
            network_passphrase=settings.STELLAR_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_invoke_contract_function_op(
            contract_id=settings.SEP45_WEB_AUTH_CONTRACT_ID,
            function_name="web_auth_verify",
            parameters=[args_map],
            auth=entries,
        )
        .set_timeout(AUTH_TIMEOUT_SECONDS)
        .build()
    )
    simulation = rpc.simulate_transaction(tx, auth_mode=AuthMode.ENFORCE)
    if simulation.error:
        raise SEP45ValidationError(
            f"transaction simulation failed: {simulation.error}"
        )


def get_latest_ledger_sequence() -> int:
    rpc = SorobanServer(settings.SOROBAN_RPC_URL)
    return rpc.get_latest_ledger().sequence


# ---------- JWT issuance -------------------------------------------------------


def issue_sep45_jwt(args: dict[str, Any], entries_b64: str) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": os.path.join(settings.HOST_URL, "sep45/auth"),
        "sub": str(args["account"]),
        "iat": now,
        "exp": now + JWT_TIMEOUT_SECONDS,
        "jti": hashlib.sha256(entries_b64.encode()).hexdigest(),
        "home_domain": str(args["home_domain"]),
    }
    if "client_domain" in args:
        claims["client_domain"] = str(args["client_domain"])
    return jwt.encode(claims, settings.SEP45_JWT_SECRET, algorithm="HS256")
