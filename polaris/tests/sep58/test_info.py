from unittest.mock import patch, Mock

import pytest

from polaris.tests.helpers import mock_check_auth_success

INFO_ENDPOINT = "/sep58/info"
code_path = "polaris.sep58.info"


# --- 6.2 test_info_returns_offerings ---
@patch(f"{code_path}.eai")
def test_info_returns_offerings(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "crypto_address", "chain_id": "eip155:1", "network": "Ethereum Mainnet"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 200, response.content
    body = response.json()
    assert "offerings" in body
    assert len(body["offerings"]) == 1
    assert body["offerings"][0]["kind"] == "crypto_address"
    mock_eai.get_offerings.assert_called_once()


# --- 6.3 test_info_no_auth_required ---
@patch(f"{code_path}.eai")
def test_info_no_auth_required(mock_eai, client):
    """No token provided — should still return 200, not 403."""
    mock_eai.get_offerings = Mock(return_value=[])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 200


# --- 6.39 test_info_crypto_address_offering_valid ---
@patch(f"{code_path}.eai")
def test_info_crypto_address_offering_valid(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "crypto_address", "chain_id": "eip155:1", "network": "Ethereum"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 200


# --- 6.40 test_info_virtual_account_offering_valid ---
@patch(f"{code_path}.eai")
def test_info_virtual_account_offering_valid(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "virtual_account", "country_code": "US", "currency": "USD", "rail": "ACH"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 200


# --- 6.41 test_info_bank_account_offering_valid ---
@patch(f"{code_path}.eai")
def test_info_bank_account_offering_valid(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "bank_account", "country_code": "MX", "currency": "MXN", "rail": "SPEI"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 200


# --- 6.42 test_info_offering_missing_kind_500 ---
@patch(f"{code_path}.eai")
def test_info_offering_missing_kind_500(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[{"country_code": "US"}])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 500


# --- 6.43 test_info_crypto_offering_missing_chain_id_500 ---
@patch(f"{code_path}.eai")
def test_info_crypto_offering_missing_chain_id_500(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "crypto_address", "network": "Ethereum"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 500


# --- 6.44 test_info_virtual_account_offering_missing_currency_500 ---
@patch(f"{code_path}.eai")
def test_info_virtual_account_offering_missing_currency_500(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "virtual_account", "country_code": "US", "rail": "ACH"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 500


# --- 6.45 test_info_bank_account_offering_missing_rail_500 ---
@patch(f"{code_path}.eai")
def test_info_bank_account_offering_missing_rail_500(mock_eai, client):
    mock_eai.get_offerings = Mock(return_value=[
        {"kind": "bank_account", "country_code": "MX", "currency": "MXN"},
    ])
    response = client.get(INFO_ENDPOINT)
    assert response.status_code == 500
