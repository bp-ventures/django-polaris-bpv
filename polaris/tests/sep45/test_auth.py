import base64
import hashlib
import json
from datetime import timedelta
from io import StringIO
from urllib.parse import urlencode

import jwt
import pytest
from django.core.management import call_command
from django.utils import timezone
from stellar_sdk import Address, Keypair, scval, xdr

from polaris import settings
from polaris.models import WebAuthNonce
from polaris.sep45 import utils


pytestmark = pytest.mark.django_db

AUTH_PATH = "/sep45/auth"
JWT_SECRET = "sep45-test-secret-with-at-least-32-bytes"
NETWORK_PASSPHRASE = "Test SDF Network ; September 2015"


def _contract_address(seed: int) -> str:
    return Address.from_raw_contract(bytes([seed]) * 32).address


def _args(
    account: str,
    home_domain: str,
    web_auth_domain: str,
    server_key: str,
    nonce: str,
    **extras,
) -> dict[str, str]:
    args = {
        "account": account,
        "home_domain": home_domain,
        "nonce": nonce,
        "web_auth_domain": web_auth_domain,
        "web_auth_domain_account": server_key,
    }
    args.update({key: str(value) for key, value in extras.items()})
    return args


def _args_scval(args: dict[str, str]) -> xdr.SCVal:
    return scval.to_map({scval.to_symbol(key): scval.to_string(value) for key, value in args.items()})


def _auth_entry(
    credential_address: str,
    contract_id: str,
    args: dict[str, str],
    *,
    function_name: str = "web_auth_verify",
    signature_expiration_ledger: int = 1000,
    sub_invocations: list[xdr.SorobanAuthorizedInvocation] | None = None,
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
                function_name=xdr.SCSymbol(function_name.encode()),
                args=[_args_scval(args)],
            ),
        ),
        sub_invocations=sub_invocations or [],
    )
    return xdr.SorobanAuthorizationEntry(credentials=credentials, root_invocation=invocation)


@pytest.fixture()
def sep45_config(monkeypatch):
    server_keypair = Keypair.random()
    contract_id = _contract_address(1)
    monkeypatch.setattr(
        settings,
        "ACTIVE_SEPS",
        ["sep-1", "sep-6", "sep-10", "sep-12", "sep-24", "sep-31", "sep-38", "sep-45", "sep-58"],
    )
    monkeypatch.setattr(settings, "HOST_URL", "http://testserver")
    monkeypatch.setattr(settings, "SEP10_HOME_DOMAINS", ["testserver"])
    monkeypatch.setattr(settings, "SIGNING_SEED", server_keypair.secret)
    monkeypatch.setattr(settings, "SIGNING_KEY", server_keypair.public_key)
    monkeypatch.setattr(settings, "SOROBAN_RPC_URL", "https://rpc.invalid")
    monkeypatch.setattr(settings, "SEP45_WEB_AUTH_CONTRACT_ID", contract_id)
    monkeypatch.setattr(settings, "SEP45_JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(settings, "STELLAR_NETWORK_PASSPHRASE", NETWORK_PASSPHRASE)
    return {
        "contract_id": contract_id,
        "server_key": server_keypair.public_key,
        "home_domain": "testserver",
        "web_auth_domain": "testserver",
    }


def _entries_for_nonce(sep45_config: dict[str, str], account: str, nonce: str) -> list[xdr.SorobanAuthorizationEntry]:
    args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], sep45_config["server_key"], nonce)
    return [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], args),
    ]


def test_nonce_issue_and_consume(sep45_config):
    nonce = utils.issue_nonce(timeout_seconds=60)
    row = WebAuthNonce.objects.get(id=nonce)
    assert row.used is False
    assert row.expires_at > timezone.now()
    assert utils.consume_nonce(nonce) is True
    assert utils.consume_nonce(nonce) is False


def test_expired_nonce_fails(sep45_config):
    nonce = "expired-nonce"
    WebAuthNonce.objects.create(id=nonce, expires_at=timezone.now() - timedelta(seconds=1))
    assert utils.consume_nonce(nonce) is False


def test_cleanup_sep45_nonces_deletes_only_old_expired_rows(sep45_config):
    old_nonce = "old-expired"
    fresh_nonce = "fresh"
    WebAuthNonce.objects.create(id=old_nonce, expires_at=timezone.now() - timedelta(days=2))
    WebAuthNonce.objects.create(id=fresh_nonce, expires_at=timezone.now() + timedelta(minutes=1))

    output = StringIO()
    call_command("cleanup_sep45_nonces", stdout=output)

    assert "deleted 1 expired nonces" in output.getvalue()
    assert not WebAuthNonce.objects.filter(id=old_nonce).exists()
    assert WebAuthNonce.objects.filter(id=fresh_nonce).exists()


def test_get_missing_account(client, sep45_config):
    response = client.get(AUTH_PATH)
    assert response.status_code == 400
    assert response.json() == {"error": "'account' is required"}


def test_get_missing_home_domain(client, sep45_config):
    response = client.get(AUTH_PATH, {"account": _contract_address(2)})
    assert response.status_code == 400
    assert response.json() == {"error": "'home_domain' is required"}


def test_get_invalid_home_domain(client, sep45_config):
    response = client.get(AUTH_PATH, {"account": _contract_address(2), "home_domain": "bad.example.com"})
    assert response.status_code == 400
    assert "invalid 'home_domain'" in response.json()["error"]


def test_get_success(client, sep45_config, monkeypatch):
    account = _contract_address(2)
    entries = _entries_for_nonce(sep45_config, account, "get-nonce")
    monkeypatch.setattr(utils, "build_challenge_entries", lambda *_args, **_kwargs: entries)

    response = client.get(AUTH_PATH, {"account": account, "home_domain": sep45_config["home_domain"]})
    payload = response.json()

    assert response.status_code == 200
    assert payload["network_passphrase"] == NETWORK_PASSPHRASE
    assert len(utils.decode_auth_entries(payload["authorization_entries"])) == 2


def test_get_success_with_client_domain(client, sep45_config, monkeypatch):
    account = _contract_address(3)
    client_domain_key = Keypair.random().public_key
    args = _args(
        account,
        sep45_config["home_domain"],
        sep45_config["web_auth_domain"],
        sep45_config["server_key"],
        "client-domain-nonce",
        client_domain="wallet.example.com",
        client_domain_account=client_domain_key,
    )
    entries = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], args),
        _auth_entry(client_domain_key, sep45_config["contract_id"], args),
    ]
    monkeypatch.setattr(utils, "get_client_domain_signing_key", lambda *_args, **_kwargs: client_domain_key)
    monkeypatch.setattr(utils, "build_challenge_entries", lambda *_args, **_kwargs: entries)

    response = client.get(
        AUTH_PATH,
        {"account": account, "home_domain": sep45_config["home_domain"], "client_domain": "wallet.example.com"},
    )
    assert response.status_code == 200
    assert response.json()["authorization_entries"]


def test_options_returns_cors_headers(client, sep45_config):
    response = client.options(
        AUTH_PATH,
        HTTP_ORIGIN="https://wallet.example.com",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_post_missing_authorization_entries(client, sep45_config):
    response = client.post(AUTH_PATH, data=json.dumps({}), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {"error": "'authorization_entries' is required"}


def test_post_rejects_oversized_payload(client, sep45_config):
    payload = "A" * (utils.MAX_AUTHORIZATION_ENTRIES_SIZE + 1)
    response = client.post(AUTH_PATH, data=json.dumps({"authorization_entries": payload}), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {"error": "'authorization_entries' exceeds maximum size"}


def test_post_rejects_invalid_base64(client, sep45_config):
    response = client.post(AUTH_PATH, data=json.dumps({"authorization_entries": "not base64!"}), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid base64 in 'authorization_entries'"}


def test_post_rejects_empty_payload(client, sep45_config):
    response = client.post(AUTH_PATH, data=json.dumps({"authorization_entries": utils.encode_auth_entries([])}), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {"error": "authorization_entries cannot be empty"}


def test_post_success_and_replay_rejected(client, sep45_config, monkeypatch):
    account = _contract_address(4)
    nonce = "post-success"
    WebAuthNonce.objects.create(id=nonce, expires_at=timezone.now() + timedelta(minutes=5))
    encoded = utils.encode_auth_entries(_entries_for_nonce(sep45_config, account, nonce))
    monkeypatch.setattr(utils, "get_latest_ledger_sequence", lambda: 10)
    monkeypatch.setattr(utils, "verify_entries_with_simulation", lambda *_args, **_kwargs: None)

    first = client.post(AUTH_PATH, data=json.dumps({"authorization_entries": encoded}), content_type="application/json")
    assert first.status_code == 200
    claims = jwt.decode(first.json()["token"], JWT_SECRET, algorithms=["HS256"])
    assert claims["iss"] == "http://testserver/sep45/auth"
    assert claims["sub"] == account
    assert claims["home_domain"] == sep45_config["home_domain"]
    assert claims["jti"] == hashlib.sha256(encoded.encode()).hexdigest()

    second = client.post(AUTH_PATH, data=json.dumps({"authorization_entries": encoded}), content_type="application/json")
    assert second.status_code == 400
    assert second.json() == {"error": "invalid or expired nonce"}


def test_post_form_urlencoded_supported(client, sep45_config, monkeypatch):
    account = _contract_address(5)
    nonce = "form-success"
    WebAuthNonce.objects.create(id=nonce, expires_at=timezone.now() + timedelta(minutes=5))
    encoded = utils.encode_auth_entries(_entries_for_nonce(sep45_config, account, nonce))
    monkeypatch.setattr(utils, "get_latest_ledger_sequence", lambda: 10)
    monkeypatch.setattr(utils, "verify_entries_with_simulation", lambda *_args, **_kwargs: None)

    response = client.post(
        AUTH_PATH,
        data=urlencode({"authorization_entries": encoded}),
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    assert response.json()["token"]


def test_validate_auth_entries_rejects_empty_list(sep45_config):
    with pytest.raises(utils.SEP45ValidationError, match="authorization_entries cannot be empty"):
        utils.validate_auth_entries([], sep45_config["web_auth_domain"], 10)


def test_validate_auth_entries_rejects_structural_mismatches(sep45_config):
    account = _contract_address(6)
    args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], sep45_config["server_key"], "mismatch")
    bad_contract = [
        _auth_entry(sep45_config["server_key"], _contract_address(99), args),
        _auth_entry(account, _contract_address(99), args),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="contract_address mismatch"):
        utils.validate_auth_entries(bad_contract, sep45_config["web_auth_domain"], 10)

    bad_function = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args, function_name="wrong"),
        _auth_entry(account, sep45_config["contract_id"], args, function_name="wrong"),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="function_name mismatch"):
        utils.validate_auth_entries(bad_function, sep45_config["web_auth_domain"], 10)

    other_args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], sep45_config["server_key"], "different")
    bad_args = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], other_args),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="args mismatch"):
        utils.validate_auth_entries(bad_args, sep45_config["web_auth_domain"], 10)


def test_validate_auth_entries_rejects_missing_auth_entries(sep45_config):
    account = _contract_address(7)
    args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], sep45_config["server_key"], "missing")
    with pytest.raises(utils.SEP45ValidationError, match="missing server authorization entry"):
        utils.validate_auth_entries([_auth_entry(account, sep45_config["contract_id"], args)], sep45_config["web_auth_domain"], 10)

    with pytest.raises(utils.SEP45ValidationError, match="missing client authorization entry"):
        utils.validate_auth_entries([_auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args)], sep45_config["web_auth_domain"], 10)


def test_validate_auth_entries_rejects_missing_client_domain_auth(sep45_config):
    account = _contract_address(8)
    client_domain_key = Keypair.random().public_key
    args = _args(
        account,
        sep45_config["home_domain"],
        sep45_config["web_auth_domain"],
        sep45_config["server_key"],
        "missing-client-domain",
        client_domain="wallet.example.com",
        client_domain_account=client_domain_key,
    )
    entries = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], args),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="missing client_domain authorization entry"):
        utils.validate_auth_entries(entries, sep45_config["web_auth_domain"], 10)


def test_validate_auth_entries_rejects_expected_value_mismatches(sep45_config):
    account = _contract_address(9)
    args = _args(account, sep45_config["home_domain"], "wrong.example.com", sep45_config["server_key"], "bad-domain")
    entries = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], args),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="web_auth_domain argument mismatch"):
        utils.validate_auth_entries(entries, sep45_config["web_auth_domain"], 10)

    args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], Keypair.random().public_key, "bad-server")
    entries = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args),
        _auth_entry(account, sep45_config["contract_id"], args),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="web_auth_domain_account argument mismatch"):
        utils.validate_auth_entries(entries, sep45_config["web_auth_domain"], 10)


def test_validate_auth_entries_rejects_expired_signature(sep45_config):
    account = _contract_address(10)
    args = _args(account, sep45_config["home_domain"], sep45_config["web_auth_domain"], sep45_config["server_key"], "expired")
    entries = [
        _auth_entry(sep45_config["server_key"], sep45_config["contract_id"], args, signature_expiration_ledger=5),
        _auth_entry(account, sep45_config["contract_id"], args, signature_expiration_ledger=5),
    ]
    with pytest.raises(utils.SEP45ValidationError, match="signature has expired"):
        utils.validate_auth_entries(entries, sep45_config["web_auth_domain"], 10)


def test_decode_auth_entries_rejects_trailing_bytes(sep45_config):
    encoded = utils.encode_auth_entries(_entries_for_nonce(sep45_config, _contract_address(11), "trailing"))
    raw = base64.b64decode(encoded) + b"extra"
    with pytest.raises(utils.SEP45ValidationError, match="invalid authorization_entries payload"):
        utils.decode_auth_entries(base64.b64encode(raw).decode())
