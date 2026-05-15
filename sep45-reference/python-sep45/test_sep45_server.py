import hashlib

import jwt
import pytest
from stellar_sdk import Address, Keypair, scval, xdr

import sep45_server
from sep45_server import ApiError, NonceStore, Settings, create_app


def _contract_address(seed: int) -> str:
    return Address.from_raw_contract(bytes([seed]) * 32).address


def _make_args(account: str, home_domain: str, web_auth_domain: str, server_key: str, nonce: str, **extras) -> dict[str, str]:
    args = {
        "account": account,
        "home_domain": home_domain,
        "nonce": nonce,
        "web_auth_domain": web_auth_domain,
        "web_auth_domain_account": server_key,
    }
    args.update({k: str(v) for k, v in extras.items()})
    return args


def _args_scval(args: dict[str, str]) -> xdr.SCVal:
    entries = {scval.to_symbol(k): scval.to_string(v) for k, v in args.items()}
    return scval.to_map(entries)


def _auth_entry(
    credential_address: str,
    contract_id: str,
    args: dict[str, str],
    function: str = "web_auth_verify",
    signature_expiration_ledger: int = 1000,
) -> xdr.SorobanAuthorizationEntry:
    credentials = xdr.SorobanCredentials(
        type=xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS,
        address=xdr.SorobanAddressCredentials(
            address=Address(credential_address).to_xdr_sc_address(),
            nonce=xdr.Int64(0),
            signature_expiration_ledger=xdr.Uint32(signature_expiration_ledger),
            signature=scval.to_void(),
        ),
    )
    invocation = xdr.SorobanAuthorizedInvocation(
        function=xdr.SorobanAuthorizedFunction(
            type=xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN,
            contract_fn=xdr.InvokeContractArgs(
                contract_address=Address(contract_id).to_xdr_sc_address(),
                function_name=xdr.SCSymbol(function.encode()),
                args=[_args_scval(args)],
            ),
        ),
        sub_invocations=[],
    )
    return xdr.SorobanAuthorizationEntry(credentials=credentials, root_invocation=invocation)


@pytest.fixture()
def configured_app(monkeypatch):
    server = Keypair.random()
    cfg = Settings(
        rpc_url="https://rpc.invalid",
        home_domains=["localhost:8080"],
        web_auth_contract_id=_contract_address(1),
        sep10_signing_seed=server.secret,
        source_signing_seed=None,
        jwt_secret="test-secret-key-with-at-least-32-bytes",
        web_auth_domain="localhost:8080",
        auth_timeout=900,
        jwt_timeout=3600,
        network_passphrase="Test SDF Network ; September 2015",
        port=8080,
    )
    monkeypatch.setattr(sep45_server, "NONCES", NonceStore())
    app = create_app(cfg)
    return app, cfg


def test_get_success(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(2)
    nonce = "n-1"
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, nonce)
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    monkeypatch.setattr(sep45_server, "build_challenge_entries", lambda *_: entries)
    monkeypatch.setattr(sep45_server.NONCES, "issue", lambda *_: nonce)

    response = client.get("/sep45/auth", query_string={"account": account})
    assert response.status_code == 400
    assert response.get_json()["error"] == "'home_domain' is required"

    response = client.get("/sep45/auth", query_string={"account": account, "home_domain": "localhost:8080"})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["network_passphrase"] == cfg.network_passphrase
    decoded = sep45_server.decode_auth_entries(payload["authorization_entries"])
    assert len(decoded) == 2


def test_get_with_client_domain(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(3)
    client_kp = Keypair.random()
    nonce = "n-2"
    args = _make_args(
        account,
        "localhost:8080",
        "localhost:8080",
        cfg.signing_public_key,
        nonce,
        client_domain="wallet.example.com",
        client_domain_account=client_kp.public_key,
    )
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
        _auth_entry(client_kp.public_key, cfg.web_auth_contract_id, args),
    ]
    monkeypatch.setattr(sep45_server, "build_challenge_entries", lambda *_: entries)
    monkeypatch.setattr(sep45_server, "get_client_domain_signing_key", lambda *_: client_kp.public_key)
    monkeypatch.setattr(sep45_server.NONCES, "issue", lambda *_: nonce)

    response = client.get(
        "/sep45/auth",
        query_string={"account": account, "home_domain": "localhost:8080", "client_domain": "wallet.example.com"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["authorization_entries"]


def test_get_missing_account(configured_app):
    app, _cfg = configured_app
    client = app.test_client()
    response = client.get("/sep45/auth")
    assert response.status_code == 400
    assert response.get_json()["error"] == "no 'account' provided"


def test_get_invalid_home_domain(configured_app):
    app, _cfg = configured_app
    client = app.test_client()
    response = client.get("/sep45/auth", query_string={"account": _contract_address(2), "home_domain": "bad.example.com"})
    assert response.status_code == 400
    assert "invalid 'home_domain'" in response.get_json()["error"]


def test_get_client_domain_fetch_failure(configured_app, monkeypatch):
    app, _cfg = configured_app
    client = app.test_client()
    monkeypatch.setattr(sep45_server, "get_client_domain_signing_key", lambda *_: (_ for _ in ()).throw(ApiError("boom", 400)))
    response = client.get(
        "/sep45/auth",
        query_string={"account": _contract_address(2), "home_domain": "localhost:8080", "client_domain": "wallet.example.com"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "boom"


def test_post_success_and_replay_rejected(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(4)
    nonce = sep45_server.NONCES.issue(account, 900)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, nonce)
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    encoded = sep45_server.encode_auth_entries(entries)
    monkeypatch.setattr(sep45_server, "verify_entries_with_simulation", lambda *_: None)
    monkeypatch.setattr(sep45_server, "get_latest_ledger_sequence", lambda *_: 10)

    first = client.post("/sep45/auth", json={"authorization_entries": encoded})
    assert first.status_code == 200
    token = first.get_json()["token"]
    claims = jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == account
    assert claims["iss"] == "localhost:8080"
    assert claims["jti"] == hashlib.sha256(encoded.encode()).hexdigest()

    second = client.post("/sep45/auth", json={"authorization_entries": encoded})
    assert second.status_code == 400
    assert second.get_json()["error"] == "invalid or expired nonce"


def test_post_simulation_failure(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(5)
    nonce = sep45_server.NONCES.issue(account, 900)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, nonce)
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    encoded = sep45_server.encode_auth_entries(entries)
    monkeypatch.setattr(
        sep45_server,
        "verify_entries_with_simulation",
        lambda *_: (_ for _ in ()).throw(ApiError("transaction simulation failed: x", 400)),
    )
    monkeypatch.setattr(sep45_server, "get_latest_ledger_sequence", lambda *_: 10)
    response = client.post("/sep45/auth", json={"authorization_entries": encoded})
    assert response.status_code == 400
    assert "transaction simulation failed" in response.get_json()["error"]


def test_alias_route_parity(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(6)
    nonce = "n-alias"
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, nonce)
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    monkeypatch.setattr(sep45_server, "build_challenge_entries", lambda *_: entries)
    monkeypatch.setattr(sep45_server.NONCES, "issue", lambda *_: nonce)
    response = client.get("/challenge", query_string={"account": account, "home_domain": "localhost:8080"})
    assert response.status_code == 200
    assert "authorization_entries" in response.get_json()


def test_validate_rejects_contract_mismatch(configured_app):
    _app, cfg = configured_app
    account = _contract_address(7)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-7")
    entries = [
        _auth_entry(cfg.signing_public_key, _contract_address(9), args),
        _auth_entry(account, _contract_address(9), args),
    ]
    with pytest.raises(ApiError, match="contract_address mismatch"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_function_mismatch(configured_app):
    _app, cfg = configured_app
    account = _contract_address(8)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-8")
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args, function="not_verify"),
        _auth_entry(account, cfg.web_auth_contract_id, args, function="not_verify"),
    ]
    with pytest.raises(ApiError, match="function_name mismatch"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_args_mismatch(configured_app):
    _app, cfg = configured_app
    account = _contract_address(10)
    args1 = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-a")
    args2 = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-b")
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args1),
        _auth_entry(account, cfg.web_auth_contract_id, args2),
    ]
    with pytest.raises(ApiError, match="args mismatch"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_missing_server_auth(configured_app):
    _app, cfg = configured_app
    account = _contract_address(11)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-11")
    entries = [_auth_entry(account, cfg.web_auth_contract_id, args)]
    with pytest.raises(ApiError, match="missing server authorization entry"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_missing_client_auth(configured_app):
    _app, cfg = configured_app
    account = _contract_address(12)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-12")
    entries = [_auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args)]
    with pytest.raises(ApiError, match="missing client authorization entry"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_missing_client_domain_auth(configured_app):
    _app, cfg = configured_app
    account = _contract_address(13)
    client = Keypair.random()
    args = _make_args(
        account,
        "localhost:8080",
        "localhost:8080",
        cfg.signing_public_key,
        "n-13",
        client_domain="wallet.example.com",
        client_domain_account=client.public_key,
    )
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    with pytest.raises(ApiError, match="missing client_domain authorization entry"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_validate_rejects_bad_web_auth_domain(configured_app):
    _app, cfg = configured_app
    account = _contract_address(14)
    args = _make_args(account, "localhost:8080", "wrong.example.com", cfg.signing_public_key, "n-14")
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    with pytest.raises(ApiError, match="web_auth_domain argument mismatch"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)


def test_post_form_urlencoded_supported(configured_app, monkeypatch):
    app, cfg = configured_app
    client = app.test_client()
    account = _contract_address(15)
    nonce = sep45_server.NONCES.issue(account, 900)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, nonce)
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args),
        _auth_entry(account, cfg.web_auth_contract_id, args),
    ]
    encoded = sep45_server.encode_auth_entries(entries)
    monkeypatch.setattr(sep45_server, "verify_entries_with_simulation", lambda *_: None)
    monkeypatch.setattr(sep45_server, "get_latest_ledger_sequence", lambda *_: 10)

    response = client.post(
        "/sep45/auth",
        data={"authorization_entries": encoded},
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    assert response.get_json()["token"]


def test_validate_rejects_expired_signature_ledger(configured_app):
    _app, cfg = configured_app
    account = _contract_address(16)
    args = _make_args(account, "localhost:8080", "localhost:8080", cfg.signing_public_key, "n-16")
    entries = [
        _auth_entry(cfg.signing_public_key, cfg.web_auth_contract_id, args, signature_expiration_ledger=5),
        _auth_entry(account, cfg.web_auth_contract_id, args, signature_expiration_ledger=5),
    ]
    with pytest.raises(ApiError, match="signature has expired"):
        sep45_server.validate_auth_entries(entries, cfg, "localhost:8080", 10)
