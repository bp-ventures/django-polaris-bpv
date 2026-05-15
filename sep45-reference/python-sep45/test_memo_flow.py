#!/usr/bin/env python3
"""
Three-hop memo flow test on Stellar testnet.

    source --[XLM + memo]--> middle --[XLM + memo]--> final

Generates and friendbot-funds three fresh accounts, submits two classic
payments, then reads each transaction back from Horizon to confirm the
memo round-trips at every hop.

Run directly:
    uv run python test_memo_flow.py

Run via pytest (skipped without env flag — hits live testnet):
    LIVE_TESTNET=1 uv run pytest test_memo_flow.py -s
"""

import os
import time
from typing import NamedTuple

import pytest
import requests
from stellar_sdk import Asset, Keypair, Network, Server, TransactionBuilder


HORIZON_URL = "https://horizon-testnet.stellar.org"
FRIENDBOT_URL = "https://friendbot.stellar.org"
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE
BASE_FEE = 200
AMOUNT_XLM = "10"
MEMO_TEXT = "INV-123"
HORIZON_READ_RETRIES = 15
HORIZON_READ_SLEEP = 2.0


class Hop(NamedTuple):
    label: str
    tx_hash: str
    horizon_memo: dict


def fund(public_key: str) -> None:
    print(f"friendbot fund {public_key}")
    r = requests.get(f"{FRIENDBOT_URL}?addr={public_key}", timeout=30)
    r.raise_for_status()


def generate_accounts() -> tuple[Keypair, Keypair, Keypair]:
    source, middle, final = (Keypair.random() for _ in range(3))
    for label, kp in (("source", source), ("middle", middle), ("final", final)):
        print(f"{label}: {kp.public_key}")
    for kp in (source, middle, final):
        fund(kp.public_key)
    return source, middle, final


def send_payment(server: Server, sender: Keypair, destination: str, amount: str, memo: str) -> str:
    account = server.load_account(sender.public_key)
    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=NETWORK_PASSPHRASE,
            base_fee=BASE_FEE,
        )
        .add_text_memo(memo)
        .append_payment_op(destination=destination, asset=Asset.native(), amount=amount)
        .set_timeout(60)
        .build()
    )
    tx.sign(sender)
    resp = server.submit_transaction(tx)
    return resp["hash"]


def read_memo(server: Server, tx_hash: str) -> dict:
    for _ in range(HORIZON_READ_RETRIES):
        try:
            tx = server.transactions().transaction(tx_hash).call()
            return {"hash": tx["hash"], "memo_type": tx["memo_type"], "memo": tx.get("memo")}
        except Exception:
            time.sleep(HORIZON_READ_SLEEP)
    raise RuntimeError(f"tx {tx_hash} not on Horizon after retries")


def run_memo_flow() -> list[Hop]:
    if len(MEMO_TEXT.encode("utf-8")) > 28:
        raise ValueError("MEMO_TEXT > 28 bytes")

    server = Server(HORIZON_URL)
    source, middle, final = generate_accounts()

    print(f"\n[hop 1] source -> middle, memo={MEMO_TEXT!r}")
    h1 = send_payment(server, source, middle.public_key, AMOUNT_XLM, MEMO_TEXT)
    print(f"  tx: {h1}")
    m1 = read_memo(server, h1)
    print(f"  horizon: {m1}")

    print(f"\n[hop 2] middle -> final, memo={MEMO_TEXT!r}")
    h2 = send_payment(server, middle, final.public_key, AMOUNT_XLM, MEMO_TEXT)
    print(f"  tx: {h2}")
    m2 = read_memo(server, h2)
    print(f"  horizon: {m2}")

    return [Hop("source->middle", h1, m1), Hop("middle->final", h2, m2)]


@pytest.mark.skipif(os.getenv("LIVE_TESTNET") != "1", reason="needs LIVE_TESTNET=1")
def test_memo_flow_three_hops() -> None:
    hops = run_memo_flow()
    for hop in hops:
        assert hop.horizon_memo["memo_type"] == "text", hop
        assert hop.horizon_memo["memo"] == MEMO_TEXT, hop


def main() -> None:
    hops = run_memo_flow()
    print()
    ok = True
    for hop in hops:
        match = hop.horizon_memo.get("memo") == MEMO_TEXT
        ok = ok and match
        print(f"{hop.label:18} memo OK: {match}")
    if not ok:
        raise SystemExit(1)
    print("\nmemo flow: OK")


if __name__ == "__main__":
    main()
