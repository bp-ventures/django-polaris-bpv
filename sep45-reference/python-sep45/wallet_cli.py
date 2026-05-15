#!/usr/bin/env python3
import argparse
import json
import sys
from typing import Any

import requests
from stellar_sdk import Address, Keypair, Network, SorobanServer, auth, xdr

import sep45_server as server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple SEP-45 wallet CLI for local server testing.")
    parser.add_argument("--server-url", default="http://127.0.0.1:8080", help="Base URL of the SEP-45 server.")
    parser.add_argument("--auth-path", default="/sep45/auth", help="SEP-45 auth endpoint path.")
    parser.add_argument("--account", required=True, help="Contract account address (C...).")
    parser.add_argument("--signer-secret", required=True, help="Secret key used to authorize the contract account.")
    parser.add_argument("--home-domain", required=True, help="home_domain query value.")
    parser.add_argument("--client-domain", help="Optional client_domain value.")
    parser.add_argument("--client-domain-signer-secret", help="Optional signer secret for client_domain_account auth.")
    parser.add_argument("--rpc-url", default="https://soroban-testnet.stellar.org", help="RPC URL used to set signature expiration ledger.")
    parser.add_argument("--network-passphrase", help="Override network passphrase. Default uses challenge response value.")
    parser.add_argument("--ledger-delta", type=int, default=10, help="signature_expiration_ledger = latest_ledger + delta.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--form-post", action="store_true", help="Use form-encoded POST instead of JSON.")
    parser.add_argument("--jwt-secret", help="Optional JWT secret to decode token for display.")
    return parser.parse_args()


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def get_challenge(url: str, args: argparse.Namespace) -> dict[str, Any]:
    params = {"account": args.account, "home_domain": args.home_domain}
    if args.client_domain:
        params["client_domain"] = args.client_domain
    response = requests.get(url, params=params, timeout=args.timeout)
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"GET challenge failed ({response.status_code}): {payload}")
    return payload


def sign_entries(
    entries: list[xdr.SorobanAuthorizationEntry],
    account: str,
    wallet_signer: Keypair,
    client_domain_signer: Keypair | None,
    network_passphrase: str,
    rpc_url: str,
    ledger_delta: int,
) -> tuple[list[xdr.SorobanAuthorizationEntry], int]:
    latest_ledger = SorobanServer(rpc_url).get_latest_ledger().sequence
    valid_until = latest_ledger + ledger_delta
    signed_count = 0
    signed_entries: list[xdr.SorobanAuthorizationEntry] = []

    for entry in entries:
        if entry.credentials.type != xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS:
            signed_entries.append(entry)
            continue

        credential_address = Address.from_xdr_sc_address(entry.credentials.address.address).address
        if credential_address == account:
            entry = auth.authorize_entry(
                entry=entry,
                signer=wallet_signer,
                valid_until_ledger_sequence=valid_until,
                network_passphrase=network_passphrase,
            )
            signed_count += 1
        elif client_domain_signer and credential_address == client_domain_signer.public_key:
            entry = auth.authorize_entry(
                entry=entry,
                signer=client_domain_signer,
                valid_until_ledger_sequence=valid_until,
                network_passphrase=network_passphrase,
            )
            signed_count += 1
        signed_entries.append(entry)
    return signed_entries, signed_count


def post_token(url: str, authorization_entries: str, use_form: bool, timeout: int) -> dict[str, Any]:
    if use_form:
        response = requests.post(
            url,
            data={"authorization_entries": authorization_entries},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
    else:
        response = requests.post(url, json={"authorization_entries": authorization_entries}, timeout=timeout)
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"POST token failed ({response.status_code}): {payload}")
    return payload


def main() -> int:
    args = parse_args()
    Address(args.account)
    wallet_signer = Keypair.from_secret(args.signer_secret)
    client_domain_signer = Keypair.from_secret(args.client_domain_signer_secret) if args.client_domain_signer_secret else None
    url = endpoint(args.server_url, args.auth_path)

    challenge = get_challenge(url, args)
    authorization_entries_b64 = challenge["authorization_entries"]
    entries = server.decode_auth_entries(authorization_entries_b64)

    network_passphrase = args.network_passphrase or challenge.get("network_passphrase") or Network.TESTNET_NETWORK_PASSPHRASE
    signed_entries, signed_count = sign_entries(
        entries=entries,
        account=args.account,
        wallet_signer=wallet_signer,
        client_domain_signer=client_domain_signer,
        network_passphrase=network_passphrase,
        rpc_url=args.rpc_url,
        ledger_delta=args.ledger_delta,
    )
    signed_b64 = server.encode_auth_entries(signed_entries)
    token_response = post_token(url, signed_b64, args.form_post, args.timeout)

    print(f"signed_entries={signed_count}")
    print(json.dumps(token_response, indent=2))

    if args.jwt_secret and "token" in token_response:
        import jwt

        claims = jwt.decode(token_response["token"], args.jwt_secret, algorithms=["HS256"])
        print("decoded_claims=")
        print(json.dumps(claims, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
