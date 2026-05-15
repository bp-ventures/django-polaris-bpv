# Anchor Platform Soroban Payment Flow — Notes

Date: 20260514
Source: `~/dev/rsync/anchor-platform` (Java/Kotlin reference)

## Single send path: always Soroban SAC.transfer

The reference server has **one** outgoing-payment path. It does not use
classic `PaymentOp` for sending. Every deposit goes through
`InvokeHostFunctionOperation` calling the SAC's `transfer`.

`kotlin-reference-server/src/main/kotlin/org/stellar/reference/client/PaymentClient.kt:31-41`:

```kotlin
fun send(destination: String, asset: Asset, amount: String, memo: String? = null): String {
  if (destination.isEmpty()) throw Exception("Destination account is required")
  return when (destination[0]) {
    'C' -> sendToAccount(destination, asset, amount, null)   // memo dropped
    'G',
    'M' -> sendToAccount(destination, asset, amount, memo)
    else -> throw Exception("Unsupported destination account type")
  }
}
```

The destination's first character decides what happens to the memo:

| Destination | Memo behavior                                                    |
|-------------|------------------------------------------------------------------|
| `C…`        | Dropped on the floor. Comment in source: *"Currently ignored for contract accounts."* The contract id is the routing key. |
| `G…`        | Numeric-only. Rewrapped into `MuxedAccount(G, memo.toLong()).address` (i.e. an `M…`) so the u64 rides in the SAC event data map per CAP-0067. |
| `M…`        | Forwarded as-is; memo overlay applied if non-null.               |

`sendToAccount` itself (`PaymentClient.kt:51-132`) hard-encodes the muxed
rewrap when memo is non-null:

```kotlin
if (memo != null) {
  // memo must be a number for MuxedAccount
  destAddress = MuxedAccount(destination, BigInteger.valueOf(memo.toLong())).address
}
```

So at the protocol layer the anchor only ever emits a **u64** correlation
id, never a text memo, even when the SEP-24 transaction memo field is
nominally a string.

## Receive path: dual-tracked

Anchor-platform observes both classic and Soroban payments simultaneously:

```
                    ┌──────────────────────────────────────┐
   classic txs ───▶ │ HorizonPaymentObserver               │
                    │   (Horizon payments_for_account)     │
                    └──────────────────────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │ Common PaymentTransferEvent          │
                    │   (from, to, amount, memo, asset)    │
                    └──────────────────────────────────────┘
                                       ▲
                    ┌──────────────────────────────────────┐
   Soroban txs ───▶ │ StellarRpcPaymentObserver            │
                    │   (RPC events: SAC "transfer" topic) │
                    └──────────────────────────────────────┘
```

The Soroban observer
(`platform/src/main/java/org/stellar/anchor/platform/observer/stellar/StellarRpcPaymentObserver.java:200-254`)
decodes each `transfer` event:

- Topics: `(Symbol("transfer"), Address from, Address to, String asset)`
- Data: either `SCV_I128 amount`, or `SCV_MAP { amount: i128, memo: ... }`
  (CAP-0067)

The memo slot in the data map is type-switched:

```java
eventMemo = switch (memoVal.getDiscriminant()) {
    case SCV_STRING -> memoVal.getStr().getSCString().toString();
    case SCV_U64    -> memoVal.getU64().toString();
    case SCV_BYTES  -> base64(memoVal.getBytes().getSCBytes());
    default         -> null;
};
if (memoVal.getDiscriminant() == SCV_U64) {
    toAddr = new MuxedAccount(toAddr, fromUint64(memoVal)).getAddress();
}
```

So the observer can ingest text, u64, or bytes memos — but the reference
**send** path never emits text or bytes. The asymmetry exists because
third-party wrapper contracts may emit string memos in their own
`transfer` events; anchor-platform tolerates them on the receive side
even though it never produces them.

## Implications for SEP-45 deposit flows

1. **Deposit to a contract-account user (`C…`)**: no memo. The contract
   id is the per-user routing key. Send is `SAC.transfer(distribution, C_user, amount)`,
   nothing else.

2. **Deposit to a classic user with correlation (`G…` + memo)**: numeric
   memo only. Send is `SAC.transfer(distribution, M(G_user, memo_id), amount)`.
   Observer recovers `memo_id` from the SAC event data map.

3. **User deposit into anchor hotwallet (`Soroban → G_hotwallet`)**:
   wallet sends `SAC.transfer(user_source, M(G_hotwallet, sep24_memo_id), amount)`.
   `StellarRpcPaymentObserver` matches the `sep24_memo_id` against the
   anchor's per-deposit observed sub-accounts and dispatches the
   corresponding SEP-24 transaction.

4. **Text memos through Soroban**: not supported by the reference send
   path. Would require a wrapper contract that calls `SAC.transfer` and
   emits its own `transfer` event with `memo: SCV_STRING(...)`. The
   observer would decode it correctly, but the reference server itself
   never produces such an event.

## Reference: validated end-to-end on testnet

`python-sep45/test_memo_flow_soroban.py` exercises the canonical pattern:

```
user --[SAC.transfer to M(hotwallet, u64)]--> hotwallet
     --[classic Payment, MEMO_ID=u64]------->  bridge
```

It verifies the same `u64` appears in three places:

1. The muxed destination `M…` encoded on hop 1.
2. The SAC `transfer` event data map (`memo: 1234567890`) emitted on hop 1.
3. The classic hop 2 transaction's `memo_type=id memo=1234567890` on Horizon.
