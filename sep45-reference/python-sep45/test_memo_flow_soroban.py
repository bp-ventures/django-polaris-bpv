#!/usr/bin/env python3
"""
Hybrid memo-id flow test on Stellar testnet.

Protocol constraint: Soroban transactions cannot carry a classic tx memo
(simulate returns "Transaction contains a memo. Soroban transactions do not
support memos."). The protocol-native substitute is a *muxed address*
(M...): an account_id + 64-bit id pair. Soroban's SAC accepts an Address
that is a muxed account, so the 64-bit id rides through the SAC.transfer.

Flow:

    source --[Soroban SAC.transfer to M_hotwallet(id=X), no memo]--> hotwallet
           --[classic Payment to recipient, MEMO_ID=X]--> recipient

Both hops carry the same 64-bit correlation id. Hop 2's MEMO_ID lands on
Horizon as a standard text-rendered memo of type "id".

Run directly:
    uv run python test_memo_flow_soroban.py

Run via pytest (skipped without env flag — hits live testnet):
    LIVE_TESTNET=1 uv run pytest test_memo_flow_soroban.py -s
"""

import os
import time
from typing import NamedTuple

import pytest
import requests
from stellar_sdk import (
    Asset,
    Keypair,
    MuxedAccount,
    Network,
    Server,
    SorobanServer,
    TransactionBuilder,
    scval,
    xdr,
)


HORIZON_URL = "https://horizon-testnet.stellar.org"
SOROBAN_RPC_URL = "https://soroban-testnet.stellar.org"
FRIENDBOT_URL = "https://friendbot.stellar.org"
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE
BASE_FEE = 200
AMOUNT_XLM = "10"
AMOUNT_STROOPS = 10 * 10_000_000
MEMO_ID = 1234567890
HORIZON_READ_RETRIES = 15
HORIZON_READ_SLEEP = 2.0


class Hop(NamedTuple):
    label: str
    tx_hash: str
    horizon_tx: dict


def fund(public_key: str) -> None:
    print(f"friendbot fund {public_key}")
    r = requests.get(f"{FRIENDBOT_URL}?addr={public_key}", timeout=30)
    r.raise_for_status()


def generate_accounts() -> tuple[Keypair, Keypair, Keypair]:
    source, hotwallet, recipient = (Keypair.random() for _ in range(3))
    for label, kp in (("source", source), ("hotwallet", hotwallet), ("recipient", recipient)):
        print(f"{label}: {kp.public_key}")
    for kp in (source, hotwallet, recipient):
        fund(kp.public_key)
    return source, hotwallet, recipient


def soroban_sac_transfer_muxed(
    soroban: SorobanServer,
    sender: Keypair,
    destination_g: str,
    memo_id: int,
    amount_stroops: int,
) -> tuple[str, str, list[list[str]]]:
    """Hop 1: Soroban native-SAC.transfer to a muxed destination.

    Returns (tx_hash, M-address, contract_events_xdr).
    """
    sac_id = Asset.native().contract_id(NETWORK_PASSPHRASE)
    m_dest = MuxedAccount(account_id=destination_g, account_muxed_id=memo_id).account_muxed
    account = soroban.load_account(sender.public_key)
    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=NETWORK_PASSPHRASE,
            base_fee=BASE_FEE,
        )
        .append_invoke_contract_function_op(
            contract_id=sac_id,
            function_name="transfer",
            parameters=[
                scval.to_address(sender.public_key),
                scval.to_address(m_dest),
                scval.to_int128(amount_stroops),
            ],
        )
        .set_timeout(60)
        .build()
    )
    sim = soroban.simulate_transaction(tx)
    if sim.error:
        raise RuntimeError(f"simulate failed: {sim.error}")
    prepared = soroban.prepare_transaction(tx, sim)
    prepared.sign(sender)
    send_resp = soroban.send_transaction(prepared)
    poll = soroban.poll_transaction(send_resp.hash)
    if poll.status.name != "SUCCESS":
        raise RuntimeError(f"soroban tx failed: status={poll.status} resp={poll}")
    events_xdr = poll.events.contract_events_xdr if poll.events else []
    return send_resp.hash, m_dest, events_xdr


def decode_sac_transfer_event(contract_events_xdr: list[list[str]]) -> dict:
    """Find the SAC `transfer` contract event and decode its CAP-0067 fields.

    Topic shape:  (Symbol("transfer"), Address from, Address to, String asset)
    Data shape:   either i128 amount, or map { amount: i128, to_muxed_id: u64 }.

    Returns {"from", "to", "asset", "amount", "memo"} where memo is the u64 if
    the destination was muxed, else None.
    """
    for op_events in contract_events_xdr or []:
        for ev_b64 in op_events:
            ev = xdr.ContractEvent.from_xdr(ev_b64)
            topics = ev.body.v0.topics
            if not topics or topics[0].type != xdr.SCValType.SCV_SYMBOL:
                continue
            if topics[0].sym.sc_symbol.decode() != "transfer":
                continue
            data = ev.body.v0.data
            out: dict = {
                "from": scval.from_address(topics[1]).address,
                "to": scval.from_address(topics[2]).address,
                "asset": scval.from_string(topics[3]).decode() if len(topics) > 3 else None,
                "memo": None,
            }
            if data.type == xdr.SCValType.SCV_I128:
                out["amount"] = int(scval.from_int128(data))
            elif data.type == xdr.SCValType.SCV_MAP:
                entries = data.map.sc_map
                out["amount"] = int(scval.from_int128(entries[0].val))
                memo_val = entries[1].val
                if memo_val.type == xdr.SCValType.SCV_U64:
                    out["memo"] = int(memo_val.u64.uint64)
                elif memo_val.type == xdr.SCValType.SCV_STRING:
                    out["memo"] = memo_val.str.sc_string.decode()
                elif memo_val.type == xdr.SCValType.SCV_BYTES:
                    out["memo"] = bytes(memo_val.bytes.sc_bytes)
            return out
    return {}


def classic_payment_with_id_memo(
    horizon: Server,
    sender: Keypair,
    destination: str,
    amount: str,
    memo_id: int,
) -> str:
    """Hop 2: classic Payment carrying MEMO_ID."""
    account = horizon.load_account(sender.public_key)
    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=NETWORK_PASSPHRASE,
            base_fee=BASE_FEE,
        )
        .add_id_memo(memo_id)
        .append_payment_op(destination=destination, asset=Asset.native(), amount=amount)
        .set_timeout(60)
        .build()
    )
    tx.sign(sender)
    resp = horizon.submit_transaction(tx)
    return resp["hash"]


def read_tx(horizon: Server, tx_hash: str) -> dict:
    for _ in range(HORIZON_READ_RETRIES):
        try:
            return horizon.transactions().transaction(tx_hash).call()
        except Exception:
            time.sleep(HORIZON_READ_SLEEP)
    raise RuntimeError(f"tx {tx_hash} not on Horizon after retries")


def run_memo_flow() -> tuple[list[Hop], str, dict]:
    horizon = Server(HORIZON_URL)
    soroban = SorobanServer(SOROBAN_RPC_URL)

    source, hotwallet, recipient = generate_accounts()

    print(f"\n[hop 1] soroban SAC.transfer  source -> M(hotwallet, id={MEMO_ID})")
    h1, m_dest, events_xdr = soroban_sac_transfer_muxed(
        soroban, source, hotwallet.public_key, MEMO_ID, AMOUNT_STROOPS
    )
    print(f"  M-dest: {m_dest}")
    print(f"  tx:     {h1}")
    t1 = read_tx(horizon, h1)
    print(f"  horizon memo_type={t1.get('memo_type')!r} memo={t1.get('memo')!r}")
    sac_event = decode_sac_transfer_event(events_xdr)
    print(f"  SAC transfer event: {sac_event}")

    print(f"\n[hop 2] classic Payment       hotwallet -> recipient   MEMO_ID={MEMO_ID}")
    h2 = classic_payment_with_id_memo(
        horizon, hotwallet, recipient.public_key, AMOUNT_XLM, MEMO_ID
    )
    print(f"  tx: {h2}")
    t2 = read_tx(horizon, h2)
    print(f"  horizon memo_type={t2.get('memo_type')!r} memo={t2.get('memo')!r}")

    return (
        [Hop("soroban->hotwallet", h1, t1), Hop("hotwallet->recipient", h2, t2)],
        m_dest,
        sac_event,
    )


@pytest.mark.skipif(os.getenv("LIVE_TESTNET") != "1", reason="needs LIVE_TESTNET=1")
def test_memo_id_flow_soroban_to_classic() -> None:
    hops, m_dest, sac_event = run_memo_flow()
    h1, h2 = hops

    assert h1.horizon_tx.get("memo_type") in (None, "none"), h1
    assert m_dest.startswith("M"), m_dest
    assert sac_event.get("memo") == MEMO_ID, sac_event
    assert h2.horizon_tx["memo_type"] == "id", h2
    assert h2.horizon_tx["memo"] == str(MEMO_ID), h2


def main() -> None:
    hops, m_dest, sac_event = run_memo_flow()
    h1, h2 = hops
    print()
    hop1_ok = h1.horizon_tx.get("memo_type") in (None, "none")
    event_ok = sac_event.get("memo") == MEMO_ID
    hop2_ok = (
        h2.horizon_tx.get("memo_type") == "id"
        and h2.horizon_tx.get("memo") == str(MEMO_ID)
    )
    print(f"hop1 (soroban):     no tx memo (protocol requires)        OK: {hop1_ok}")
    print(f"hop1 muxed dest:    {m_dest}")
    print(f"hop1 SAC event memo={sac_event.get('memo')}  (CAP-0067 data map)    OK: {event_ok}")
    print(f"hop2 (classic):     memo_type=id memo={MEMO_ID}             OK: {hop2_ok}")
    if not (hop1_ok and event_ok and hop2_ok):
        raise SystemExit(1)
    print("\nmemo-id flow: OK")


if __name__ == "__main__":
    main()
