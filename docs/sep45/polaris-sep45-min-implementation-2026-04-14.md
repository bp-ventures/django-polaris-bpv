# SEP-45 Minimum Implementation — Django Polaris BPV

**Date:** 2026-04-14  
**Status:** Proposed minimum scope  
**Goal:** add SEP-45 without breaking existing SEP-10 behavior or widening the refactor surface more than necessary

## 1. Decision

The minimum implementation should be **compatibility-first**, not a broad auth-layer rewrite.

That means:

- add SEP-45 discovery and `/sep45/auth`
- keep the existing `SEP10Token` and `validate_sep10_token()` entry points for now
- teach that compatibility layer to accept SEP-45 JWTs and `C...` accounts
- patch only the current request validators that still hard-reject contract addresses
- defer the larger `WebAuthToken` rename/generalization work

This is the smallest useful cut that can support contract-account auth while keeping the existing Polaris endpoint and integration surface stable.

## 2. Two Minimums

### 2.1 Absolute minimum

This is the smallest change set that should not break the app:

- add SEP-45 settings, routing, TOML fields, and CORS
- implement `GET /sep45/auth` and `POST /sep45/auth`
- keep SEP-45 behind a feature flag
- do **not** allow SEP-45 bearer tokens into existing SEP-6/12/24/31/38/58 endpoints yet

This is the safest rollout, but it is only partial support.

### 2.2 Minimum useful implementation

This is the recommended minimum:

- everything in 2.1
- the SEP-45 endpoint issues JWTs signed with a **separate** `SEP45_JWT_SECRET`. The Stellar Ed25519 signing key (`SIGNING_SEED` / `SIGNING_KEY`) used for server auth-entry signatures is shared with SEP-10 — same anchor identity. Only the HMAC JWT secret is split.
- the shared compatibility token (`SEP10Token`) accepts both SEP-10 and SEP-45 JWTs. Same downstream API, different signing keys.
- **one central policy: every endpoint already protected by `validate_sep10_token()` accepts SEP-45 JWTs**. No per-endpoint allowlist, no scattered flags. Scope: SEP-6, SEP-12, SEP-24, SEP-38, SEP-58. SEP-31 routes use the same decorator and will receive SEP-45 tokens at runtime, but v1 makes no promise that the BPV `SEP31ReceiverIntegration` tolerates `token.account=C...` — see §7.4.

Scope boundary, non-negotiable:

- SEP-45 authenticates the **caller**. `token.account` may be `C...`.
- On-chain payment **destinations/sources** in SEP-6/24 requests (`account` request parameter → `to_address` / `from_address`) must still be `G...` or `M...` in v1. The classic deposit/withdraw pipeline (`process_pending_deposits.py`, `polaris/utils.py`, `polaris/integrations/custody.py`) is **out of scope** and continues to construct classic payments, claimable balances, and create-account operations.
- A Soroban/SAC deposit path for `C...` destinations is explicitly deferred. SEP-6/24 request validators continue to reject `C...` until that rail lands.

This is the smallest scope that is both useful and still low-risk.

## 3. What Must Be In Scope

### 3.1 Settings, routing, discovery

Modify:

- `polaris/settings.py:44-74`
- `polaris/settings.py:98-150`
- `polaris/urls.py:21-44`
- `polaris/sep1/views.py:73-88`
- `polaris/cors.py:4-13`

Required changes:

- add `"sep-45"` to `accepted_seps`
- add three genuinely new SEP-45 settings:
  - `SOROBAN_RPC_URL` — Soroban RPC endpoint for `simulateTransaction`
  - `SEP45_WEB_AUTH_CONTRACT_ID` — deployed `web_auth_verify` contract address
  - `SEP45_JWT_SECRET` — HMAC secret for session JWTs issued by `/sep45/auth`. Separate from `SERVER_JWT_KEY` so SEP-10 and SEP-45 trust material can be rotated independently. Matches Anchor Platform commit `9c07fca0` hardening lesson.
- **do not deploy a custom web_auth contract**. Reuse the canonical SEP-0045 reference deployments:
  - Pubnet: `CALI6JC3MSNDGFRP7Z2OKUEPREHOJRRXKMJEWQDEFZPFGXALA45RAUTH`
  - Testnet: `CD3LA6RKF5D2FN2R2L57MWXLBRSEWWENE74YBEFZSSGNJRJGICFGQXMX`
  - The reference contract has no anchor-specific state — multiple anchors share one deployment. Deploy a custom one only if v2+ needs anchor-side auth logic (e.g. account allow-lists).
- **reuse existing SEP-10 settings** for everything else. Same anchor, same trust boundary, same protocol semantics. No SEP-45-specific knobs for these:
  - Stellar signing key (server auth-entry signature) — reuse `SIGNING_SEED` / `SIGNING_KEY`
  - accepted home domains — reuse `SEP10_HOME_DOMAINS`
  - web auth domain — reuse the existing `urlparse(HOST_URL).netloc` derivation
  - challenge auth timeout — reuse the existing SEP-10 auth timeout
  - session JWT timeout — reuse the existing SEP-10 JWT timeout
- SEP-10 and SEP-45 JWTs are signed with different HMAC secrets. The decoder tries `SERVER_JWT_KEY` first, falls back to `SEP45_JWT_SECRET` on `InvalidTokenError`. The resulting compatibility `SEP10Token` is the same shape for both.
- require `SIGNING_SEED` when SEP-45 is active
- register `polaris.sep45.urls` at `/sep45/`
- publish in `stellar.toml`:
  - `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`
  - `WEB_AUTH_CONTRACT_ID`
  - `SIGNING_KEY`
- CORS for `/sep45/auth`:
  - `Access-Control-Allow-Origin: *` on every response, including error responses
  - explicit `OPTIONS /sep45/auth` preflight handler

### 3.1.1 Startup validation

Fail fast at Django app load when `"sep-45" in ACTIVE_SEPS`:

- `SEP45_WEB_AUTH_CONTRACT_ID` is a syntactically valid `C...` address (`stellar_sdk.StrKey.is_valid_contract`)
- `SOROBAN_RPC_URL` is set
- `SEP45_JWT_SECRET` is set and non-empty

Anything beyond this is over-engineered for the minimum cut.

## 3.2 New SEP-45 endpoint

Add:

- `polaris/sep45/urls.py`
- `polaris/sep45/views.py`
- `polaris/sep45/utils.py`

Add one new model in:

- `polaris/models.py`

Reason: creating a separate nested `models.py` package is extra work in this codebase. The current app uses a single `polaris/models.py`. The minimum path is to add `WebAuthNonce` there and create a normal migration.

### 3.3 Nonce model

Add `WebAuthNonce` to `polaris/models.py`.

Minimum fields:

- `id` — the nonce string itself, primary key (`CharField(primary_key=True, max_length=64)`)
- `used`
- `expires_at`
- `created_at`
- `used_at`

Reason for keeping the table this narrow: the nonce string is unguessable (`secrets.token_hex(16)`), the args map and signatures are bound by the contract simulation, and the account binding adds nothing security-wise — it would only carry operational debug info. Matches Anchor Platform's `JdbcNonce` which is keyed by `id` alone.

Required behavior:

- atomic consume using a single `UPDATE ... WHERE id=:id AND used=false AND expires_at > now()` returning row count
- never check-then-update; always one query

Do **not** use process-local memory or Django cache for production nonce validation.

Required cleanup (same section, no separate subsystem):

- add one management command `polaris/management/commands/cleanup_sep45_nonces.py`
- it deletes rows where `expires_at < now() - INTERVAL '1 day'` (a one-day grace beyond expiry is enough; the auth window is seconds, not hours)
- intended to be run from cron / systemd timer alongside the other Polaris cleanup commands
- no in-process timer, no Django signal, no Celery beat — just a plain `BaseCommand`

`GET /sep45/auth` is public and creates a row on every call. Without cleanup the table grows unboundedly. This is not optional — Anchor Platform ships the same pairing of atomic-consume and cleanup-job (`JdbcNonceRepo`).

## 4. SEP-45 Protocol Boundary: What Is Non-Negotiable

The endpoint can be minimal, but it cannot be loose.

**Module layout:** all protocol validation lives in `polaris/sep45/utils.py`. `views.py` stays boring — parse request, call `build_challenge()` or `verify_entries_and_issue_token()`, return response. Small surface, easier to unit-test, easier to review.

```
polaris/sep45/
  urls.py
  views.py        # request/response only; no XDR, no domain, no nonce, no simulation
  utils.py        # build_challenge(), verify_entries_and_issue_token(), helpers
```

Required checks (all implemented in `polaris/sep45/utils.py`, exercised through the helpers above):

1. Validate request shape before doing any work.
2. Enforce a pre-decode size limit on `authorization_entries` (cap at 100 KB, matching Anchor Platform `Sep45Service.java:183-185`).
3. Accept both `application/json` and `application/x-www-form-urlencoded` POST bodies (SEP-0045 mandate).
4. Decode XDR strictly and reject trailing bytes.
5. Reject sub-invocations.
6. Require `contract_address == SEP45_WEB_AUTH_CONTRACT_ID`.
7. Require `function_name == "web_auth_verify"`.
8. Require identical args across all entries.
9. Require all of `account`, `home_domain`, `nonce`, `web_auth_domain`, `web_auth_domain_account` to be **present and to match expected values**:
   - `home_domain ∈ SEP10_HOME_DOMAINS`
   - `web_auth_domain == urlparse(HOST_URL).netloc`
   - `web_auth_domain_account == SIGNING_KEY`
   - `account` is a syntactically valid `C...` address
10. Require presence of a server auth entry **and** a client auth entry.
11. Atomically consume the nonce.
12. **Replay window** (SEP-0045 mandate — nonce alone is insufficient):
    - server-built challenge entry must set `signatureExpirationLedger = current_ledger + 10` (matches Anchor Platform `Sep45Service.java:97`)
    - reject any submitted client auth entry whose `signatureExpirationLedger <= current_ledger`
13. Simulate in `AuthMode.ENFORCE` against `SOROBAN_RPC_URL`.
14. Issue a JWT signed with `SEP45_JWT_SECRET`. `iss = os.path.join(HOST_URL, "sep45/auth")`. `sub = C...`. SEP-10 and SEP-45 JWTs are distinguished primarily by signing key; `iss`/`sub` shape is defence in depth.

GET response must include:

- `authorization_entries` (XDR)
- `network_passphrase` (SEP-0045 recommended; trivial to add, helps client misconfig detection)

These are minimum-safe requirements, not optional hardening.

## 5. Compatibility Layer: Keep The Old Names

Modify:

- `polaris/sep10/token.py:27-124`
- `polaris/sep10/utils.py:9-62`

### 5.1 Keep `SEP10Token`

Do not start with the broad `WebAuthToken` rename.

For minimum scope:

- keep `SEP10Token` as the public compatibility class
- extend it to accept `C...` in `sub`
- keep `token.account`, `token.muxed_account`, `token.memo`, `token.client_domain`

Required behavior for contract-account tokens:

- `token.account` returns the `C...` address
- `token.muxed_account` returns `None`
- `token.memo` returns `None`

This preserves the current consumer API and avoids touching dozens of files for type-hint and import churn.

### 5.2 Keep `validate_sep10_token()` — central policy, no per-endpoint flag

Keep the existing decorator name and surface. **Do not** add a per-endpoint allowlist kwarg.

For minimum scope:

- `validate_jwt_request()` tries `SERVER_JWT_KEY` first; on `InvalidTokenError`, retries with `SEP45_JWT_SECRET`. Matches Anchor Platform `WebAuthJwtFilter.check()`.
- the decoder returns the same compatibility `SEP10Token` object regardless of which protocol signed the JWT
- if `sub` starts with `G`/`M` → classic SEP-10 token; `account/muxed_account/memo` are populated as today
- if `sub` starts with `C` → SEP-45 token; `account = C...`, `muxed_account = None`, `memo = None`

That gives **one obvious control path** at the call site: one decorator, one downstream token object, two signing keys with a fixed try-order. Every endpoint that uses the decorator gets SEP-45 acceptance transparently.

Anchor-integration prep (out of Polaris-core scope) is covered in §7.

## 6. Downstream Changes Actually Required

### 6.1 SEP-6 request validators stay unchanged

Do **not** modify:

- `polaris/sep6/deposit.py:218-228`
- `polaris/sep6/withdraw.py:161-171`

Reason: the `account` request parameter is the on-chain destination/source. `polaris/sep6/deposit.py:85` assigns `to_address=args["account"]`, which is consumed downstream by the classic payment pipeline (`process_pending_deposits.py`, `polaris/integrations/custody.py`). Accepting `C...` here would build a classic `PaymentOp` to a contract address — invalid on the protocol layer.

v1 behavior:

- `token.account` may be `C...` (SEP-45-authenticated caller)
- request `account` must remain `G...` or `M...` (classic rail destination)
- a SEP-45-authenticated wallet may still call SEP-6 with its own `G...` destination — auth identity (`C`) and payment destination (`G`/`M`) are separately addressed

When the SAC/Soroban payment rail lands, this section re-opens. Until then the request validators stay strict, which gives an honest 400 instead of a stuck transaction.

### 6.2 SEP-24 request validators stay unchanged

Do **not** modify:

- `polaris/sep24/deposit.py:474-484`
- `polaris/sep24/withdraw.py:468-477`

Same reason as §6.1. `polaris/sep24/deposit.py:424-426` falls back to `token.muxed_account or token.account` when the request omits an `account` — for SEP-45 callers this fallback would produce `C...` as the destination address, breaking the classic rail. To stay safe in v1, the request validators must still reject `C...`, and SEP-45 callers must supply an explicit `G/M` destination in the request body.

### 6.3 SEP-24 interactive session code does not need a first-pass rewrite

No minimum change required in:

- `polaris/sep24/utils.py:121-151`
- `polaris/sep24/utils.py:290-305`

Reason:

- the short-lived interactive JWT is Polaris-internal
- it is still signed with `SERVER_JWT_KEY`
- its `sub` handling already falls through cleanly for non-`M...`, non-memo values, which means `C...` works as an opaque account identifier

So for the minimum implementation, do **not** redesign the SEP-24 interactive JWT/session layer.

## 7. Downstream Areas That Can Stay As-Is For Now

These should be audited, but Polaris core does not need a first-pass refactor if the compatibility token keeps the current API.

### 7.1 SEP-12

Current code in `polaris/sep12/customer.py` uses:

- `token.account`
- `token.muxed_account`
- `token.memo`
- `token.client_domain`

Relevant lines:

- `polaris/sep12/customer.py:56-60`
- `polaris/sep12/customer.py:100-104`
- `polaris/sep12/customer.py:152-156`
- `polaris/sep12/customer.py:302-319`

Minimum conclusion:

- no Polaris-core refactor required
- anchor integration must tolerate `memo=None` and `account=C...`

### 7.2 SEP-38

Current code stores quote ownership in text fields already:

- `polaris/sep38/quote.py:180-194`
- `polaris/utils.py:473-479`
- `polaris/utils.py:548-554`
- `polaris/models.py:817-836`

Minimum conclusion:

- no schema change required
- `Quote.stellar_account` is already a `TextField`, so it can hold `C...`

### 7.3 SEP-58

Current code just consumes the token through the decorator:

- `polaris/sep58/accounts.py:18-31`

Minimum conclusion:

- no Polaris-core refactor required; the compatibility auth layer already accepts SEP-45 JWTs

### 7.4 SEP-31

Current code uses the shared decorator at:

- `polaris/sep31/transactions.py:43`
- `polaris/sep31/transactions.py:76`
- `polaris/sep31/transactions.py:118`

It also uses `token.account` as both the lookup key (`stellar_account=token.account`, a `TextField`) and the argument to `valid_sending_anchor(public_key=token.account)`.

v1 stance:

- no Polaris-core refactor in v1
- SEP-31 routes will receive SEP-45 tokens at runtime through the shared decorator. This is unavoidable given the central-policy decision in §2.2 — the decorator does not (and should not) discriminate by protocol.
- v1 makes **no promise** that the BPV `SEP31ReceiverIntegration` tolerates `token.account=C...` and `token.memo=None`. If SEP-31 is in your day-one deployment, you must either wire and test the receiver integration for contract-account callers before enabling SEP-45 in production, or expose SEP-31 only to clients that authenticate with SEP-10.
- the v1 test suite (§11) does not exercise SEP-31 with `C...` tokens. SEP-31 is a deferred follow-up.

## 8. What Must Stay Deferred

Do **not** include this in the minimum implementation:

- repo-wide rename from `SEP10Token` to `WebAuthToken`
- repo-wide rename from `validate_sep10_token()` to `validate_web_auth_token()`
- broad transaction/account field renames
- new auth microservice
- persistence of raw authorization entries
- redesign of SEP-24 interactive session JWTs
- new customer/quote integration signatures
- Soroban/SAC payment **send** rail for `C...` destinations (classic `G/M` destinations remain)
- Soroban/SAC payment **receive** rail (CAP-0067 event observer). Without this, contract-account users cannot deposit through Soroban. This is the planned immediate follow-up. Reference flow: `sep45-reference/python-sep45/test_memo_flow_soroban.py`. See §14.5.
- CAP-0068 executable-type discrimination (optional policy, see §14.5)

These may still be good changes later. They are not minimum changes.

## 9. One Real Product Constraint

Contract-account support removes memo-based user multiplexing.

Current Polaris data model assumes:

- one `stellar_account`
- optional `muxed_account`
- optional `account_memo`

Relevant fields:

- `polaris/models.py:478-497`
- `polaris/models.py:817-836`

Minimum implementation consequence:

- `stellar_account` may now be `C...`
- `muxed_account` and `account_memo` will be null for SEP-45 sessions

This does **not** require a schema migration because those columns already support it.

But business logic that relied on memo-based sub-user routing will need a separate review.

## 10. JWT Issuer Semantics

The SEP-45 JWT `iss` must be a URI, not a bare hostname.

For the minimum implementation, use:

- `os.path.join(settings.HOST_URL, "sep45/auth")`

That matches the current SEP-10 style in:

- `polaris/sep10/views.py:309-317`

Do **not** issue a bare `web_auth_domain` hostname as the JWT issuer.

## 11. Tests Required For The Minimum

Add:

- `polaris/tests/sep45/test_auth.py`

Update:

- `polaris/tests/auth_test.py`
- targeted SEP-6 and SEP-24 tests that cover `C...` account parameters

Minimum test cases:

1. `GET /sep45/auth` success — response includes `authorization_entries` and `network_passphrase`.
2. `OPTIONS /sep45/auth` returns CORS headers (preflight).
3. CORS header is present on `4xx` error responses too.
4. `POST /sep45/auth` success — JSON body.
5. `POST /sep45/auth` success — `application/x-www-form-urlencoded` body (SEP-0045 mandate).
6. nonce replay rejection.
7. oversized payload rejection (>100 KB).
8. contract mismatch rejection.
9. function mismatch rejection.
10. sub-invocation rejection.
11. expected-value mismatch rejection (one test each for wrong `home_domain`, `web_auth_domain`, `web_auth_domain_account`).
12. expired client `signatureExpirationLedger` rejection.
13. startup validation rejects an invalid `SEP45_WEB_AUTH_CONTRACT_ID` and a missing `SEP45_JWT_SECRET`.
14. `validate_jwt_request()` accepts a SEP-45 JWT (signed with `SEP45_JWT_SECRET`) and returns a token with:
    - `account == C...`
    - `muxed_account is None`
    - `memo is None`
15. `validate_jwt_request()` still accepts an existing SEP-10 JWT (signed with `SERVER_JWT_KEY`) unchanged — confirms the try-SEP10-first / try-SEP45-fallback order.
16. SEP-6 deposit/withdraw with `account=C...` in the request body returns 400 (contract destinations are not on the v1 payment rail).
17. SEP-24 deposit/withdraw with `account=C...` in the request body returns 400.
18. SEP-6/24 deposit/withdraw with `account=G...` and a SEP-45-authenticated caller succeeds — auth `C`, destination `G`.
19. SEP-12/38/58 endpoints accept a SEP-45 JWT through the shared decorator (smoke test — confirms the central policy, no per-endpoint allowlist). **SEP-31 is intentionally not exercised in v1; see §7.4.**
20. existing SEP-10 auth tests still pass unchanged.

Anchor-integration cleanup command:

21. `cleanup_sep45_nonces` deletes rows where `expires_at < now() - INTERVAL '1 day'` and leaves fresh/unexpired rows untouched.

## 12. Implementation Order

Recommended order:

1. settings (three new: `SOROBAN_RPC_URL`, `SEP45_WEB_AUTH_CONTRACT_ID`, `SEP45_JWT_SECRET`) + startup validation, routing, TOML, CORS + OPTIONS
2. `WebAuthNonce` model (PK = nonce string) + migration + `cleanup_sep45_nonces` management command
3. `polaris/sep45/utils.py` helpers (`build_challenge`, `verify_entries_and_issue_token`) and their unit tests
4. `polaris/sep45/views.py` (thin) and end-to-end protocol tests
5. compatibility changes in `polaris/sep10/token.py` and `polaris/sep10/utils.py` — accept `C...` in `sub`, try `SERVER_JWT_KEY` then `SEP45_JWT_SECRET`, no per-endpoint flag
6. regression tests for existing SEP-10 behavior + smoke tests confirming SEP-12/38/58 inherit SEP-45 acceptance through the shared decorator (SEP-31 intentionally excluded — see §7.4)
7. assertion tests that SEP-6/24 still reject `C...` request destinations

## 13. Bottom Line

The minimum implementation is **not** the broad auth rename described in the earlier architecture docs.

The minimum implementation is:

- one new SEP-45 endpoint surface (`views.py` thin, `utils.py` does the work)
- one nonce model (PK = nonce string) + one atomic-consume path + one cleanup management command
- three new settings: `SOROBAN_RPC_URL`, `SEP45_WEB_AUTH_CONTRACT_ID`, `SEP45_JWT_SECRET`. Stellar signing key (`SIGNING_SEED`/`SIGNING_KEY`), home domains, and timeouts are reused from SEP-10. Only the HMAC JWT secret is split.
- one canonical policy: every decorator-protected endpoint accepts SEP-45 JWTs — no per-endpoint allowlist. SEP-31 is intentionally outside v1's promised surface (§7.4).
- zero changes to SEP-6/24 request validators — contract-account destinations stay rejected until the SAC payment rail lands

In one sentence: copy Anchor Platform's security lessons (separate JWT secret, atomic nonce, cleanup job, 100 KB cap), not its architecture. One endpoint, one helper module, one nonce model, one atomic consume path, one shared token compatibility path.

Everything else should be deferred until this smaller cut is working and tested.

## 14. Demo Wallet Interoperability

The Stellar demo wallet is the target smoke client after unit/API tests pass. Polaris must return standard snake_case SEP-45 fields. The wallet may need a local compatibility patch if it still expects camelCase. SEP-6/24 contract-account flows are only in scope if BPV accepts `C...` as the request `account` and the integration can handle settlement semantics.

### 14.1 Field naming — Polaris stays standard

Polaris returns the SEP-0045 standard snake_case fields:

- `authorization_entries`
- `network_passphrase`
- `token`
- `error`

The current demo wallet (`stellar-demo-wallet/packages/demo-wallet-shared/methods/sep45Auth/start.ts:36`) reads camelCase keys (`authorizationEntries`, `networkPassphrase`). This is a demo-wallet bug, not a Polaris concession. The wallet must be patched locally before smoke testing — Polaris must not bend to a non-spec client.

### 14.2 Demo wallet SEP-6/24 flows — out of scope in v1

The demo wallet sends the SEP-45 contract ID as the `account` parameter on SEP-6/24 deposit and withdraw:

- `stellar-demo-wallet/packages/demo-wallet-shared/methods/sep24/interactiveDepositFlow.ts:40`
- `stellar-demo-wallet/packages/demo-wallet-shared/methods/sep24/interactiveWithdrawFlow.ts:22`
- `stellar-demo-wallet/packages/demo-wallet-shared/methods/sep6/programmaticDepositFlow.ts:32`

Per §6.1 / §6.2 / §2.2, v1 rejects `account=C...` at the SEP-6/24 request validator. **Therefore the demo wallet's SEP-6/24 contract-account flows will return 400 against v1 Polaris, by design.** Smoke testing for these flows is deferred until the Soroban/SAC payment rail lands. The demo wallet's SEP-45 challenge/token round-trip remains in scope.

If a deployment ever decides to bypass §6.1/§6.2 and accept `C...` as the SEP request `account`, the integration must own the settlement path — this doc does not authorize that change.

### 14.3 Demo-wallet-compatible challenge — interop checklist

Reference checkout: `/home/antb2/dev/rsync/stellar-demo-wallet/`.

The demo wallet's client-side challenge validator (`packages/demo-wallet-shared/methods/sep45Auth/sign.ts`) verifies:

- no sub-invocations (`sign.ts:156`)
- contract ID matches the TOML-advertised `WEB_AUTH_CONTRACT_ID` (`sign.ts:91`)
- function name is `web_auth_verify`
- expected args (`account`, `home_domain`, `web_auth_domain`, `web_auth_domain_account`, `nonce`, optional `client_domain`/`client_domain_account`) (`sign.ts:201`)
- server signature on the server's auth entry
- footprint contains only the expected `ledger_key_nonce` `contract_data` entries

After the unit/API tests in §11 pass, run one end-to-end check against a patched demo wallet (snake_case patched) on testnet. If the wallet's client-side validation passes and POST `/sep45/auth` returns a token, the challenge is interop-correct. Track this as a manual step; do not block CI on it.

### 14.4 What the demo wallet covers — and what it does not

Reference checkout: `/home/antb2/dev/rsync/stellar-demo-wallet/`.

What the demo wallet **does** handle (useful for v1 SEP-45 smoke-testing):

- SEP-45 auth flow end-to-end.
- Contract accounts with passkey-based `__check_auth`.
- SAC-style asset interaction through SDK helpers:
  - `packages/demo-wallet-shared/services/SmartWalletService.ts:191`
  - `packages/demo-wallet-shared/services/SmartWalletService.ts:216`
- Contract-account signing for Soroban auth entries:
  - `packages/demo-wallet-shared/services/SmartWalletService.ts:319`

What the demo wallet **does not** do (relevant boundary):

- Does not inspect whether an address executable is `Account`, `StellarAsset`, or `Wasm`.
- Its contract-account implementation ignores `_auth_contexts`: `packages/demo-wallet-soroban/contracts/contract-account/src/lib.rs:101`.
- Does not enforce a policy like "only authorize SAC transfers" on-chain.
- Does not prove CAP-68 behavior against Polaris.

Conclusion: the demo wallet is a good interop client for the SEP-45 challenge/token surface in v1. It is **not** a vehicle for proving stricter contract-account authorization or executable-type discrimination — those belong with the deferred Soroban/SAC payment rail (§14.5), not the SEP-45 minimum.

### 14.5 CAP-67 and CAP-68 — different specs, both out of v1

These two specs come up in the contract-account anchor conversation but solve different problems. v1 SEP-45 needs neither.

#### CAP-0067 — SAC `transfer` event memo data map

CAP-0067 (stellar/stellar-protocol/blob/master/core/cap-0067.md) extends SAC `transfer` events with a memo field in the event data map:

```
event topic: (Symbol("transfer"), Address from, Address to, String asset)
event data:  SCV_I128 amount
          OR SCV_MAP { amount: i128, memo: SCV_U64 | SCV_STRING | SCV_BYTES }
```

This is what enables memoless-on-classic but routable Soroban deposits. A passkey wallet sends `SAC.transfer(C_user, M(hotwallet_G, memo_id), amount)`; the SAC emits an event whose data map carries `memo: memo_id`; the anchor's observer matches that memo against a pending SEP-24 transaction.

**What CAP-67 is for**: receiving Soroban deposits with per-user routing. Anchor Platform's `StellarRpcPaymentObserver.java:200-254` is the reference decoder.

**v1 stance**: deferred. v1 ships SEP-45 auth only. A SEP-45-authenticated contract-account user can do KYC (SEP-12), get quotes (SEP-38), and create accounts (SEP-58), but **cannot deposit through Soroban** until the CAP-67 observer module lands. Classic deposits to `G/M` destinations remain fully supported.

**Canonical reference pattern**: `sep45-reference/python-sep45/test_memo_flow_soroban.py` exercises the end-to-end correlation flow on live testnet — a Soroban `SAC.transfer` to `M(hotwallet_G, id=X)` followed by a classic `Payment` carrying `MEMO_ID=X`. The same `u64` lands in three places (the muxed destination, the SAC event data map, and the classic-hop Horizon memo), which is what the deferred CAP-67 observer must reproduce on the anchor side. Read this test first when scoping the receive rail.

#### CAP-0068 — `get_address_executable`

CAP-0068 (stellar/stellar-protocol/blob/master/core/cap-0068.md) adds `get_address_executable`, which returns the executable identity of an address: `Wasm`, `StellarAsset`, or `Account`. It lets a server discriminate "is this `C...` a SAC, a custom Wasm contract, or a wrapped classic account?" and apply policy accordingly.

**What CAP-68 is for**: discriminating contract-account types for policy decisions ("only SACs may use this anchor", "only Wasm contract accounts may withdraw").

**v1 stance**: deferred and not required by the receive rail either — the SAC `transfer` event topic structure already identifies SAC payments unambiguously. CAP-68 only matters as a policy lever, and v1 has no such policy.

#### Summary

| Concern | Spec | v1 status | Notes |
|---|---|---|---|
| SEP-45 auth | SEP-0045 | in scope | this doc |
| Receive Soroban payments with routing memo | **CAP-0067** | deferred (immediate follow-up) | event data map memo; reference flow in `sep45-reference/python-sep45/test_memo_flow_soroban.py` |
| Discriminate contract-account executable types | **CAP-0068** | deferred (optional policy) | needed only if anchor wants per-type policy |
| Send Soroban payments to `C...` | SDK / `InvokeHostFunctionOperation` | deferred (immediate follow-up) | classic payments to `G/M` remain v1 |

## 15. Operational Notes

### 15.1 Soroban RPC failure mode

`/sep45/auth` simulates against `SOROBAN_RPC_URL` on both GET (recording mode) and POST (`AuthMode.ENFORCE`). Failure handling stays simple — no money moves at the auth layer, so we do not need a verify-before-send escrow pattern:

- RPC unreachable / connection error → `502 Bad Gateway` with a short error string. Client retries.
- Simulation returns an error → `400 Bad Request` with the simulation error string (matches the reference Python and Anchor Platform).
- Polaris-internal failure (XDR parse exception we didn't anticipate, JWT signing failure) → `500`.

This is consistent with the broader Polaris principle: on deposit/withdraw the pipeline verifies before sending because the anchor holds the funds and can always retry. The SEP-45 layer never holds funds, so the same principle reduces to "fail loud, let the client retry".

### 15.2 Rate limiting — deferred

`/sep45/auth` is public and creates a `WebAuthNonce` row on every GET. v1 does **not** add application-level rate limiting. Deployments exposing the endpoint to the public internet should sit it behind a WAF or nginx rate limit (e.g. 60 challenges/min per IP). Anchor Platform's git history shows they added a "too many challenges" guard later — we should expect to do the same once usage data justifies a threshold, but not preemptively.

The daily `cleanup_sep45_nonces` is the storage-side bound; the front-side bound is deployment glue.
