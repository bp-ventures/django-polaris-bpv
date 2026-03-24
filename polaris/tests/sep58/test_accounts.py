import json
from unittest.mock import patch, Mock

import pytest

from polaris.tests.helpers import mock_check_auth_success

ACCOUNTS_ENDPOINT = "/sep58/accounts"
code_path = "polaris.sep58.accounts"

# --- Shared fixtures ---

VALID_CRYPTO_ADDRESS = {
    "id": "acc_crypto_1", "kind": "crypto_address", "status": "active",
    "stellar_asset": "USDC:GABCD", "stellar_account": "GABCD",
    "chain_id": "eip155:1", "network": "Ethereum Mainnet",
    "instructions": {"organization.crypto_address": {"value": "0xABC", "description": "Deposit address"}},
    "created_at": "2026-03-19T10:00:00Z", "updated_at": "2026-03-19T10:00:00Z",
}

VALID_VIRTUAL_ACCOUNT = {
    "id": "acc_va_1", "kind": "virtual_account", "status": "active",
    "stellar_asset": "USDC:GABCD", "stellar_account": "GABCD",
    "country_code": "US", "currency": "USD", "rail": "ACH",
    "instructions": {"organization.bank_account_number": {"value": "123", "description": "Account number"}},
    "created_at": "2026-03-19T10:00:00Z", "updated_at": "2026-03-19T10:00:00Z",
}

VALID_BANK_ACCOUNT = {
    "id": "acc_bank_1", "kind": "bank_account", "status": "active",
    "stellar_asset": "USDC:GABCD", "stellar_account": "GABCD",
    "country_code": "MX", "currency": "MXN", "rail": "SPEI",
    "instructions": {"organization.clabe_number": {"value": "646180111803859359", "description": "CLABE"}},
    "created_at": "2026-03-19T10:00:00Z", "updated_at": "2026-03-19T10:00:00Z",
}


def _post(client, data):
    return client.post(ACCOUNTS_ENDPOINT, json.dumps(data), content_type="application/json")


# ============================================================
# POST /accounts — request validation (AC1)
# ============================================================

# --- 6.4 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_missing_kind_400(client):
    response = _post(client, {})
    assert response.status_code == 400
    assert "kind" in response.json()["error"].lower()


# --- 6.5 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_invalid_kind_400(client):
    response = _post(client, {"kind": "invalid"})
    assert response.status_code == 400
    assert "must be one of" in response.json()["error"]


# --- 6.6 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_virtual_account_missing_country_code_400(client):
    response = _post(client, {"kind": "virtual_account", "currency": "USD"})
    assert response.status_code == 400
    assert "country_code" in response.json()["error"]


# --- 6.7 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_virtual_account_missing_currency_400(client):
    response = _post(client, {"kind": "virtual_account", "country_code": "US"})
    assert response.status_code == 400
    assert "currency" in response.json()["error"]


# --- 6.8 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_bank_account_missing_country_code_400(client):
    response = _post(client, {"kind": "bank_account", "currency": "MXN"})
    assert response.status_code == 400
    assert "country_code" in response.json()["error"]


# --- 6.9 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_bank_account_missing_currency_400(client):
    response = _post(client, {"kind": "bank_account", "country_code": "MX"})
    assert response.status_code == 400
    assert "currency" in response.json()["error"]


# --- 6.10 ---
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_crypto_address_missing_chain_id_400(client):
    response = _post(client, {"kind": "crypto_address"})
    assert response.status_code == 400
    assert "chain_id" in response.json()["error"]


# --- 6.11 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_non_https_callback_400(mock_eai, client):
    response = _post(client, {
        "kind": "crypto_address", "chain_id": "eip155:1",
        "on_change_callback": "http://example.com/hook",
    })
    assert response.status_code == 400
    assert "HTTPS" in response.json()["error"]


# --- 6.12 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_https_callback_passthrough(mock_eai, client):
    mock_eai.create_account = Mock(return_value=(VALID_CRYPTO_ADDRESS, True))
    response = _post(client, {
        "kind": "crypto_address", "chain_id": "eip155:1",
        "on_change_callback": "https://example.com/hook",
    })
    assert response.status_code == 201
    call_kwargs = mock_eai.create_account.call_args[1]
    assert call_kwargs["params"]["on_change_callback"] == "https://example.com/hook"


# ============================================================
# POST /accounts — integration error conventions (AC2)
# ============================================================

# --- 6.13 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_value_error_400(mock_eai, client):
    mock_eai.create_account = Mock(side_effect=ValueError("bad request"))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 400


# --- 6.14 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_runtime_error_503(mock_eai, client):
    mock_eai.create_account = Mock(side_effect=RuntimeError("service down"))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 503


# --- 6.15 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_kyc_403(mock_eai, client):
    kyc_response = {"type": "non_interactive_customer_info_needed", "fields": ["birth_date"]}
    mock_eai.create_account = Mock(return_value=kyc_response)
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "non_interactive_customer_info_needed"
    assert "fields" in body


# ============================================================
# POST /accounts — per-kind valid response shapes (AC3)
# ============================================================

# --- 6.16 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_crypto_address_success_201(mock_eai, client):
    mock_eai.create_account = Mock(return_value=(VALID_CRYPTO_ADDRESS, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 201
    account = response.json()["account"]
    assert account["kind"] == "crypto_address"
    assert "chain_id" in account
    assert "network" in account


# --- 6.17 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_virtual_account_success_201(mock_eai, client):
    mock_eai.create_account = Mock(return_value=(VALID_VIRTUAL_ACCOUNT, True))
    response = _post(client, {"kind": "virtual_account", "country_code": "US", "currency": "USD"})
    assert response.status_code == 201
    account = response.json()["account"]
    assert account["kind"] == "virtual_account"
    assert "country_code" in account
    assert "currency" in account
    assert "rail" in account


# --- 6.18 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_bank_account_success_201(mock_eai, client):
    mock_eai.create_account = Mock(return_value=(VALID_BANK_ACCOUNT, True))
    response = _post(client, {"kind": "bank_account", "country_code": "MX", "currency": "MXN"})
    assert response.status_code == 201
    account = response.json()["account"]
    assert account["kind"] == "bank_account"
    assert "country_code" in account
    assert "currency" in account
    assert "rail" in account


# --- 6.19 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_existing_200(mock_eai, client):
    mock_eai.create_account = Mock(return_value=(VALID_CRYPTO_ADDRESS, False))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 200


# ============================================================
# POST /accounts — per-kind invalid response shapes (AC3)
# ============================================================

def _make_invalid_account(**overrides):
    """Start from a valid crypto_address and apply overrides / removals."""
    base = dict(VALID_CRYPTO_ADDRESS)
    for key, val in overrides.items():
        if val is None:
            base.pop(key, None)
        else:
            base[key] = val
    return base


# --- 6.20 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_crypto_address_missing_chain_id_500(mock_eai, client):
    bad = _make_invalid_account(chain_id=None)
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 500


# --- 6.21 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_crypto_address_missing_network_500(mock_eai, client):
    bad = _make_invalid_account(network=None)
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 500


# --- 6.22 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_virtual_account_missing_country_code_500(mock_eai, client):
    bad = dict(VALID_VIRTUAL_ACCOUNT)
    del bad["country_code"]
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "virtual_account", "country_code": "US", "currency": "USD"})
    assert response.status_code == 500


# --- 6.23 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_virtual_account_missing_currency_500(mock_eai, client):
    bad = dict(VALID_VIRTUAL_ACCOUNT)
    del bad["currency"]
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "virtual_account", "country_code": "US", "currency": "USD"})
    assert response.status_code == 500


# --- 6.24 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_virtual_account_missing_rail_500(mock_eai, client):
    bad = dict(VALID_VIRTUAL_ACCOUNT)
    del bad["rail"]
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "virtual_account", "country_code": "US", "currency": "USD"})
    assert response.status_code == 500


# --- 6.25 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_bank_account_missing_rail_500(mock_eai, client):
    bad = dict(VALID_BANK_ACCOUNT)
    del bad["rail"]
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "bank_account", "country_code": "MX", "currency": "MXN"})
    assert response.status_code == 500


# --- 6.26 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_active_account_missing_instructions_500(mock_eai, client):
    bad = _make_invalid_account(instructions=None)
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 500


# --- 6.27 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_missing_common_fields_500(mock_eai, client):
    bad = {"kind": "crypto_address"}
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 500


# --- 6.28 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_create_account_invalid_status_500(mock_eai, client):
    bad = _make_invalid_account(status="bogus")
    mock_eai.create_account = Mock(return_value=(bad, True))
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 500


# ============================================================
# GET /accounts/:id (AC1 + AC2)
# ============================================================

# --- 6.29 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_get_account_success(mock_eai, client):
    mock_eai.get_account = Mock(return_value=VALID_CRYPTO_ADDRESS)
    response = client.get(f"{ACCOUNTS_ENDPOINT}/acc_crypto_1")
    assert response.status_code == 200
    assert response.json()["account"]["id"] == "acc_crypto_1"


# --- 6.30 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_get_account_not_found_404(mock_eai, client):
    mock_eai.get_account = Mock(side_effect=ValueError("not found"))
    response = client.get(f"{ACCOUNTS_ENDPOINT}/nonexistent")
    assert response.status_code == 404


# --- 6.31 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_get_account_runtime_error_503(mock_eai, client):
    mock_eai.get_account = Mock(side_effect=RuntimeError("service down"))
    response = client.get(f"{ACCOUNTS_ENDPOINT}/acc_crypto_1")
    assert response.status_code == 503


# --- 6.32 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_get_account_validation_failure_500(mock_eai, client):
    mock_eai.get_account = Mock(return_value={"kind": "crypto_address"})
    response = client.get(f"{ACCOUNTS_ENDPOINT}/acc_crypto_1")
    assert response.status_code == 500


# ============================================================
# GET /accounts — success + errors (AC1 + AC2)
# ============================================================

# --- 6.33 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_success(mock_eai, client):
    mock_eai.list_accounts = Mock(return_value=[VALID_CRYPTO_ADDRESS, VALID_VIRTUAL_ACCOUNT])
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 200
    body = response.json()
    assert len(body["accounts"]) == 2


# --- 6.34 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_with_filters(mock_eai, client):
    mock_eai.list_accounts = Mock(return_value=[VALID_CRYPTO_ADDRESS])
    response = client.get(ACCOUNTS_ENDPOINT, {"kind": "crypto_address", "status": "active"})
    assert response.status_code == 200
    call_kwargs = mock_eai.list_accounts.call_args[1]
    assert call_kwargs["filters"] == {"kind": "crypto_address", "status": "active"}


# --- 6.35 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_value_error_400(mock_eai, client):
    mock_eai.list_accounts = Mock(side_effect=ValueError("bad filter"))
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 400


# --- 6.36 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_runtime_error_503(mock_eai, client):
    mock_eai.list_accounts = Mock(side_effect=RuntimeError("service down"))
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 503


# ============================================================
# GET /accounts — list-level response validation (AC2 + AC3)
# ============================================================

# --- 6.37 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_malformed_item_500(mock_eai, client):
    mock_eai.list_accounts = Mock(return_value=[{"kind": "crypto_address"}])
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 500


# --- 6.38 ---
@patch(f"{code_path}.eai")
@patch("polaris.sep10.utils.check_auth", mock_check_auth_success)
def test_list_accounts_mixed_kinds_valid(mock_eai, client):
    mock_eai.list_accounts = Mock(return_value=[VALID_CRYPTO_ADDRESS, VALID_VIRTUAL_ACCOUNT, VALID_BANK_ACCOUNT])
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 200
    assert len(response.json()["accounts"]) == 3


# ============================================================
# Auth (AC1)
# ============================================================

# --- 6.46 ---
def test_accounts_requires_auth(client):
    """POST, GET /accounts, and GET /accounts/:id without token should 403."""
    # POST /accounts
    response = _post(client, {"kind": "crypto_address", "chain_id": "eip155:1"})
    assert response.status_code == 403

    # GET /accounts
    response = client.get(ACCOUNTS_ENDPOINT)
    assert response.status_code == 403

    # GET /accounts/:id
    response = client.get(f"{ACCOUNTS_ENDPOINT}/some_id")
    assert response.status_code == 403
