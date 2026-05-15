# SEP-45 Minimum Implementation TODO — Django Polaris BPV

**Date:** 2026-04-14  
**Status:** Implementation plan  
**Companion doc:** `docs/sep45/polaris-sep45-min-implementation-2026-04-14.md`

## 1. Scope

This TODO implements the **minimum useful** SEP-45 rollout:

- add SEP-45 discovery and `/sep45/auth`
- add durable nonce storage (PK = nonce string) + atomic consume + cleanup management command
- keep existing `SEP10Token` and `validate_sep10_token()` as the compatibility surface
- allow the compatibility auth path to accept SEP-45 JWTs signed with a **separate** `SEP45_JWT_SECRET` (try SEP-10 first, fall back to SEP-45)
- leave SEP-6 / SEP-24 request validators **unchanged** — `account=C...` continues to return 400 until the Soroban/SAC payment rail lands
- defer the broad rename/refactor work
- defer SEP-31 (the decorator will accept SEP-45 tokens at runtime, but v1 makes no promise the receiver integration tolerates `C...`)

This plan is intentionally narrow. Every step below should preserve current SEP-10 behavior.

## 2. Deliverables

By the end of this plan, the repo should contain:

- SEP-45 settings in `polaris/settings.py` (`SOROBAN_RPC_URL`, `SEP45_WEB_AUTH_CONTRACT_ID`, `SEP45_JWT_SECRET`)
- SEP-45 route registration in `polaris/urls.py`
- TOML advertisement for SEP-45 in `polaris/sep1/views.py`
- CORS coverage for `/sep45` in `polaris/cors.py`
- a new `WebAuthNonce` model (PK = nonce string) and migration
- a new `polaris/sep45/` module with:
  - `urls.py`
  - `views.py`
  - `utils.py`
- a new management command `polaris/management/commands/cleanup_sep45_nonces.py`
- compatibility support for SEP-45 JWTs in:
  - `polaris/sep10/token.py`
  - `polaris/sep10/utils.py`
- tests covering the new endpoint, the compatibility path, the cleanup command, and an assertion that SEP-6/24 still reject `C...` request destinations

## 3. Implementation Rules

Follow these constraints during implementation:

1. Do not rename `SEP10Token` yet.
2. Do not rename `validate_sep10_token()` yet.
3. Do not touch SEP-31. The shared decorator will accept SEP-45 tokens on its routes at runtime; v1 makes no promise that the BPV `SEP31ReceiverIntegration` tolerates `token.account=C...`.
4. Do not modify SEP-6/24 request validators. `account=C...` must still return 400 — the classic payment rail cannot route to a contract address in v1.
5. Do not do broad type-hint sweeps.
6. Do not change interactive SEP-24 JWT/session semantics unless a failing test proves it is needed.
7. Keep all new behavior behind `"sep-45" in ACTIVE_SEPS`.
8. Separate SEP-45 JWT secret (`SEP45_JWT_SECRET`) from the SEP-10 JWT secret (`SERVER_JWT_KEY`). Share the Stellar Ed25519 signing key (`SIGNING_SEED`/`SIGNING_KEY`) — that is the anchor's on-chain identity, not its session-token secret.
9. Use database-backed nonce validation with atomic consume keyed on the nonce PK.

## 4. Phase 0: Pre-Work

### 4.1 Create a branch

Suggested:

```bash
git checkout -b sep45-min-impl
```

### 4.2 Baseline test run

Run the narrow baseline first so regressions are obvious later.

Suggested commands:

```bash
uv run pytest polaris/tests/auth_test.py -q
uv run pytest polaris/tests/sep1/test_toml.py -q
uv run pytest polaris/tests/sep6/test_deposit.py -q
uv run pytest polaris/tests/sep6/test_withdraw.py -q
uv run pytest polaris/tests/sep24/test_deposit.py -q
uv run pytest polaris/tests/sep24/test_withdraw.py -q
```

Record failures before changing code.

## 5. Phase 1: Settings, Routing, TOML, CORS

### 5.1 Update `polaris/settings.py`

Modify:

- `polaris/settings.py:44-54`
- `polaris/settings.py:63-74`
- `polaris/settings.py:98-150`

#### Step 5.1.1 Add SEP-45 to accepted SEPs

Current block:

- `accepted_seps` at `polaris/settings.py:44-54`

Changes:

- add `"sep-45"`
- remove the duplicate `"sep-6"` while touching the list

Expected result:

```python
accepted_seps = [
    "sep-1",
    "sep-6",
    "sep-10",
    "sep-12",
    "sep-24",
    "sep-31",
    "sep-38",
    "sep-45",
    "sep-58",
]
```

#### Step 5.1.2 Load signing key when SEP-45 is enabled

Current block:

- `SIGNING_SEED, SIGNING_KEY = None, None`
- `if "sep-10" in ACTIVE_SEPS: ...`

Change:

- update the condition so `SIGNING_SEED` and `SIGNING_KEY` are loaded when either SEP-10 or SEP-45 is active

Required behavior:

- if `"sep-45"` is active without `"sep-10"`, `SIGNING_SEED` must still be loaded
- invalid seed should still raise `ImproperlyConfigured("Invalid SIGNING_SEED")`

#### Step 5.1.3 Add SEP-45 settings

Insert a new block after the SEP-10 home-domain configuration and before general shared settings.

Add only three new settings:

- `SOROBAN_RPC_URL`
- `SEP45_WEB_AUTH_CONTRACT_ID`
- `SEP45_JWT_SECRET`

Reuse the existing SEP-10 settings — same anchor identity, same trust boundary, same protocol semantics:

- `SEP10_HOME_DOMAINS` (no `SEP45_HOME_DOMAINS`)
- derive web_auth_domain from `urlparse(HOST_URL).netloc` (no `SEP45_WEB_AUTH_DOMAIN`)
- reuse the SEP-10 auth timeout (no `SEP45_AUTH_TIMEOUT`)
- reuse the SEP-10 JWT timeout (no `SEP45_JWT_TIMEOUT`)
- reuse `SIGNING_SEED` / `SIGNING_KEY` for the server's Soroban auth-entry signature

Per-protocol knobs for the items above should be added the day a real deployment needs them, not preemptively.

Recommended load rules:

- load the three new settings only when `"sep-45" in ACTIVE_SEPS`
- otherwise set to `None`

Required validation at startup (raise `ImproperlyConfigured` on failure):

- `SOROBAN_RPC_URL` must start with `http`
- `SEP45_WEB_AUTH_CONTRACT_ID` must be a valid `C...` address (use `StrKey.is_valid_contract` or `stellar_sdk.Address(...)`)
- `SEP45_JWT_SECRET` must be present and non-empty
- `SIGNING_SEED` must be loaded (already covered by Step 5.1.2)

Imports needed:

- `from stellar_sdk import Address`

Do **not** instantiate an RPC client at import time yet unless tests demand it. The minimum-safe pattern is to validate strings at startup and instantiate RPC clients inside SEP-45 helpers.

### 5.2 Update `polaris/urls.py`

Modify:

- `polaris/urls.py:21-44`

Add:

```python
if "sep-45" in settings.ACTIVE_SEPS:
    urlpatterns.append(path("sep45/", include("polaris.sep45.urls")))
```

Placement:

- after SEP-38 or before SEP-58 is fine
- keep the structure flat and consistent with the existing pattern

### 5.3 Update `polaris/sep1/views.py`

Modify:

- `polaris/sep1/views.py:73-88`

Add new TOML output when SEP-45 is active:

- `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`
- `WEB_AUTH_CONTRACT_ID`

Required behavior:

- endpoint should be `os.path.join(settings.HOST_URL, "sep45", "auth")`
- contract ID should be `settings.SEP45_WEB_AUTH_CONTRACT_ID`
- if SEP-45 is active and SEP-10 is not, still include `SIGNING_KEY`

### 5.4 Update `polaris/cors.py`

Modify:

- `polaris/cors.py:4-13`

Add:

- `request.path.startswith("/sep45")`

Keep the function flat. Do not introduce config indirection here.

### 5.5 Tests for Phase 1

Update:

- `polaris/tests/sep1/test_toml.py`

Add one new test:

- TOML includes `WEB_AUTH_FOR_CONTRACTS_ENDPOINT` and `WEB_AUTH_CONTRACT_ID` when `"sep-45"` is active

Implementation note:

- patch `settings.ACTIVE_SEPS` with `"sep-45"` included
- patch `settings.SEP45_WEB_AUTH_CONTRACT_ID` to a fixed contract address

Suggested verification:

```bash
uv run pytest polaris/tests/sep1/test_toml.py -q
```

## 6. Phase 2: Nonce Model and Migration

### 6.1 Add `WebAuthNonce` to `polaris/models.py`

Modify:

- `polaris/models.py`

Add the model near the other simple persistence models. A practical insertion point is after `Transaction` and before `Quote`, so it stays near auth/session-related persisted state.

Add fields:

- `id = models.CharField(primary_key=True, max_length=64)` — the nonce string itself (e.g. `secrets.token_hex(16)`)
- `used = models.BooleanField(default=False)`
- `expires_at = models.DateTimeField()`
- `created_at = models.DateTimeField(auto_now_add=True)`
- `used_at = models.DateTimeField(null=True, blank=True)`

No `account` column, no `nonce` column, no composite unique. The nonce string is the PK and is unguessable; binding the account in the table adds no security and only operational debug noise. Matches Anchor Platform's `JdbcNonce` shape.

Do **not** add optional audit columns yet unless implementation proves they are needed.

### 6.2 Create migration

New migration file:

- `polaris/migrations/0015_webauthnonce.py`

Reason:

- current migration sequence ends at `0014_auto_20220211_0624.py`

Create with:

```bash
uv run python manage.py makemigrations polaris
```

Then inspect the generated migration. If the name is noisy, rename the file manually but keep the dependency chain correct.

### 6.3 Nonce helper behavior

Do not implement nonce logic inside the model.

Nonce issue/consume helpers belong in:

- `polaris/sep45/utils.py`

Required helpers:

- `issue_nonce(timeout_seconds: int) -> str` — generates `secrets.token_hex(16)`, persists `WebAuthNonce(id=nonce, expires_at=now+timeout)`, returns the nonce string
- `consume_nonce(nonce: str) -> bool` — atomic update by PK, returns True iff exactly one row was updated

Required consume semantics:

```python
updated = WebAuthNonce.objects.filter(
    id=nonce,
    used=False,
    expires_at__gt=now,
).update(used=True, used_at=now)
return updated == 1
```

### 6.4 Tests for Phase 2

Add:

- `polaris/tests/sep45/test_auth.py`

Add model/helper tests:

- nonce issue creates a row
- first consume succeeds
- second consume fails
- expired nonce fails

Suggested verification:

```bash
uv run pytest polaris/tests/sep45/test_auth.py -q
```

### 6.5 Cleanup management command

Add:

- `polaris/management/commands/cleanup_sep45_nonces.py`

Implement as a plain `BaseCommand`:

```python
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from polaris.models import WebAuthNonce

class Command(BaseCommand):
    help = "Delete expired SEP-45 nonces (expires_at < now() - 1 day)."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=1)
        deleted, _ = WebAuthNonce.objects.filter(expires_at__lt=cutoff).delete()
        self.stdout.write(f"deleted {deleted} expired nonces")
```

Operational notes:

- intended to run from cron / systemd timer alongside other Polaris cleanup commands
- no in-process timer, no Celery beat, no Django signal
- the one-day grace beyond expiry is plenty — `auth_timeout` is seconds, not hours

Add a test:

- create a row with `expires_at = now() - 2 days` and a row with `expires_at = now() + 1 minute`
- run the command
- assert only the expired row is gone

## 7. Phase 3: Create `polaris/sep45/` Endpoint Surface

### 7.1 Add package files

Create:

- `polaris/sep45/__init__.py`
- `polaris/sep45/urls.py`
- `polaris/sep45/views.py`
- `polaris/sep45/utils.py`

Keep module structure flat.

### 7.2 Add `polaris/sep45/urls.py`

Content:

```python
from django.urls import path
from polaris.sep45.views import SEP45Auth

urlpatterns = [path("auth", SEP45Auth.as_view())]
```

No extra aliases in the minimum implementation.

### 7.3 Add request/response constants and helpers in `polaris/sep45/utils.py`

This file should hold the protocol mechanics, not the view class.

Add helpers for:

- hostname validation
- home-domain matching
- web-auth-domain resolution
- nonce issue / consume
- auth-entry encode / decode
- auth-entry validation
- RPC simulation
- JWT issuance

Suggested function list:

- `validate_hostname(value: str, field_name: str) -> None`
- `domain_matches(pattern: str, actual: str) -> bool`
- `select_home_domain(home_domain: str | None, allowed: list[str]) -> str`
- `resolve_web_auth_domain(request_host: str) -> str`
- `issue_nonce(account: str, timeout_seconds: int) -> str`
- `consume_nonce(account: str, nonce: str) -> bool`
- `encode_auth_entries(entries: list[xdr.SorobanAuthorizationEntry]) -> str`
- `decode_auth_entries(payload_b64: str) -> list[xdr.SorobanAuthorizationEntry]`
- `validate_auth_entries(...) -> tuple[dict[str, Any], xdr.SCVal]`
- `build_args_map(...) -> xdr.SCVal`
- `build_challenge_entries(...) -> list[xdr.SorobanAuthorizationEntry]`
- `verify_entries_with_simulation(...) -> None`
- `issue_sep45_jwt(...) -> str`

Imports likely needed:

- `base64`
- `hashlib`
- `json`
- `secrets`
- `time`
- `xdrlib3`
- `from urllib.parse import urlparse`
- `from typing import Any`
- `from django.utils import timezone`
- `from stellar_sdk import Address, Keypair, SorobanServer, TransactionBuilder, auth, scval, xdr`
- `from stellar_sdk.soroban_rpc import AuthMode`
- `from rest_framework.exceptions import APIException` only if needed

### 7.4 Implement `GET /sep45/auth` in `polaris/sep45/views.py`

Add one class:

- `class SEP45Auth(APIView):`

Set:

- `parser_classes = [JSONParser, MultiPartParser, FormParser]`
- `renderer_classes = [JSONRenderer, BrowsableAPIRenderer]`

#### GET flow

1. Read `account`, `home_domain`, optional `client_domain`.
2. Require `account`.
3. Require `home_domain`.
4. Validate `account` as a contract account with `Address(account)`.
5. Validate/match `home_domain` against `settings.SEP45_HOME_DOMAINS`.
6. Validate optional `client_domain` hostname.
7. Resolve `web_auth_domain`.
8. Resolve optional client-domain signing key.
9. Issue nonce.
10. Build args map.
11. Build challenge entries through Soroban simulation.
12. Return:
   - `authorization_entries`
   - `network_passphrase`

Response shape:

```json
{
  "authorization_entries": "<base64 xdr>",
  "network_passphrase": "..."
}
```

### 7.5 Implement `POST /sep45/auth` in `polaris/sep45/views.py`

#### POST flow

1. Extract `authorization_entries` from:
   - JSON body
   - form body
2. Reject missing payload.
3. Reject oversized payload before decode.
4. Decode auth entries strictly.
5. Resolve expected `web_auth_domain`.
6. Get current ledger sequence.
7. Validate auth entries structurally.
8. Atomically consume nonce.
9. Simulate with `AuthMode.ENFORCE`.
10. Issue SEP-45 JWT.
11. Return `{"token": token}`.

### 7.6 JWT semantics

Use a dedicated helper in `polaris/sep45/utils.py`.

Required claims:

- `iss`
- `sub`
- `iat`
- `exp`
- `jti`
- `home_domain`
- optional `client_domain`

Required values:

- `iss = os.path.join(settings.HOST_URL, "sep45/auth")`
- `sub = C...`
- `jti = sha256(authorization_entries_b64.encode()).hexdigest()`

Required secret:

- `settings.SEP45_JWT_SECRET`

Do **not** reuse `settings.SERVER_JWT_KEY`.

### 7.7 Client-domain support

In the minimum implementation, keep this simple:

- if `client_domain` is present, attempt TOML fetch
- require `SIGNING_KEY`
- validate it as a `G...` key
- include `client_domain` and `client_domain_account` in args map

Do not build a new shared client-domain helper yet unless duplication with SEP-10 becomes annoying during implementation.

### 7.8 Required protocol checks

These checks must exist before simulation:

1. valid base64
2. valid XDR payload
3. non-empty entry list
4. no trailing bytes after decode
5. no sub-invocations
6. contract function type only
7. contract address matches configured contract
8. function name is `web_auth_verify`
9. one args map per entry
10. args identical across entries
11. required args present
12. `account` is `C...`
13. `home_domain` is accepted
14. `web_auth_domain` matches expected
15. `web_auth_domain_account` matches `SIGNING_KEY`
16. client-domain pair appears together
17. server auth entry present
18. client auth entry present
19. optional client-domain auth entry present when applicable
20. signature expiration ledger not expired

### 7.9 Error style

Stay consistent with existing Polaris error responses:

- use `render_error_response(...)`
- use 400 for invalid request/protocol data
- use 403 only if a policy decision explicitly fits 403
- use 500 only for actual internal failures

Keep logging concise:

- challenge issued
- token issued
- validation failed
- simulation failed

Do not log raw JWTs, raw auth-entry payloads, or signing material.

### 7.10 Tests for Phase 3

Add new file:

- `polaris/tests/sep45/test_auth.py`

Add tests for:

- GET missing account
- GET missing home_domain
- GET invalid home_domain
- GET success
- GET success with client_domain
- POST missing `authorization_entries`
- POST invalid base64
- POST empty payload
- POST contract mismatch
- POST function mismatch
- POST args mismatch
- POST missing server auth
- POST missing client auth
- POST missing client-domain auth
- POST success
- POST replay rejection
- POST URL-encoded form support

Use the local reference tests in:

- `sep45-reference/python-sep45/test_sep45_server.py`

as the behavioral model, but adapt to Django/DRF response conventions.

Suggested verification:

```bash
uv run pytest polaris/tests/sep45/test_auth.py -q
```

## 8. Phase 4: Compatibility Path In Existing SEP-10 Auth Layer

### 8.1 Update `polaris/sep10/token.py`

Modify:

- `polaris/sep10/token.py:27-124`

#### Step 8.1.1 Accept `C...` in `sub`

Current behavior:

- `Keypair.from_public_key(stellar_account)` rejects `C...`

Change logic:

- if `sub.startswith("M")`: existing muxed logic
- elif `":" in sub`: existing memo logic
- else:
  - if `sub.startswith("C")`: validate with `Address(sub)`
  - else: validate with `Keypair.from_public_key(sub)`

Imports needed:

- `from stellar_sdk import Address`

#### Step 8.1.2 Preserve the existing consumer API

Required properties:

- `account`
- `muxed_account`
- `memo`
- `issuer`
- `issued_at`
- `expires_at`
- `client_domain`
- `payload`

Required contract-account behavior:

- `account` returns the `C...` address
- `muxed_account is None`
- `memo is None`

Do not rename the class.

### 8.2 Update `polaris/sep10/utils.py`

Modify:

- `polaris/sep10/utils.py:9-62`

#### Step 8.2.1 Accept SEP-45 JWTs

Current behavior:

- `validate_jwt_request()` returns `SEP10Token(encoded_jwt)`
- `SEP10Token` decodes with `settings.SERVER_JWT_KEY`

Required change:

- make `validate_jwt_request()` decode with either secret (matches Anchor Platform `WebAuthJwtFilter.check()`)

Recommended implementation:

1. Parse bearer token as today.
2. Try `jwt.decode(encoded, settings.SERVER_JWT_KEY, algorithms=["HS256"])` → SEP-10 path.
3. If that raises `InvalidTokenError` and `"sep-45" in settings.ACTIVE_SEPS`, try `jwt.decode(encoded, settings.SEP45_JWT_SECRET, algorithms=["HS256"])` → SEP-45 path.
4. Raise a single auth error if both fail.
5. Pass the decoded dict into `SEP10Token(payload_dict)` — the token class itself stays single-secret-ignorant.

This keeps the two-secret logic at the decode boundary, not inside the token class.

#### Step 8.2.2 Keep old names

Do not rename:

- `check_auth`
- `validate_sep10_token`
- `validate_jwt_request`

Only widen their behavior.

### 8.3 Tests for Phase 4

Update:

- `polaris/tests/auth_test.py`

Add tests for:

- decoding a valid SEP-45 JWT through `check_auth`
- token object exposes:
  - `account == C...`
  - `muxed_account is None`
  - `memo is None`
- existing SEP-10 auth tests still pass unchanged

Also update helper mocks if useful:

- `polaris/tests/helpers.py`

You may add:

- a `mock_check_auth_success_contract_account(...)`

Suggested verification:

```bash
uv run pytest polaris/tests/auth_test.py -q
```

## 9. Phase 5: SEP-6 / SEP-24 Boundary — No Code Changes, Assertion Tests Only

The companion spec §6.1/§6.2 reverses the earlier "patch the request validators" plan. In v1, `account=C...` must continue to return 400 from SEP-6/24 — the classic payment pipeline (`process_pending_deposits.py`, `polaris/integrations/custody.py`) cannot route a `PaymentOp` to a contract address. Allowing `C...` at the request boundary would produce a stuck transaction at submit time. SEP-45 authentication and SEP-6/24 destination addressing are independent surfaces in v1.

### 9.1 No code changes

Do **not** modify:

- `polaris/sep6/deposit.py`
- `polaris/sep6/withdraw.py`
- `polaris/sep24/deposit.py`
- `polaris/sep24/withdraw.py`

### 9.2 Assertion tests (lock the v1 boundary in place)

Update:

- `polaris/tests/sep6/test_deposit.py`
- `polaris/tests/sep6/test_withdraw.py`
- `polaris/tests/sep24/test_deposit.py`
- `polaris/tests/sep24/test_withdraw.py`

Add tests proving the boundary:

- SEP-6 deposit with `account=C...` returns 400 (or the existing equivalent invalid-account error)
- SEP-6 withdraw with `account=C...` returns 400
- SEP-24 deposit interactive with `account=C...` returns 400
- SEP-24 withdraw interactive with `account=C...` returns 400
- SEP-6/24 deposit/withdraw with `account=G...` succeed when the caller authenticates with a SEP-45 JWT (`token.account=C...`, request `account=G...`) — auth identity and payment destination are independent

For these tests:

- generate a valid contract address (e.g. `Address.from_raw_contract(...)`)
- patch auth to return a token mock with `account=<C...>`, `memo=None`, `muxed_account=None` for the SEP-45 cases

Suggested verification:

```bash
uv run pytest polaris/tests/sep6/test_deposit.py -q
uv run pytest polaris/tests/sep6/test_withdraw.py -q
uv run pytest polaris/tests/sep24/test_deposit.py -q
uv run pytest polaris/tests/sep24/test_withdraw.py -q
```

## 10. Phase 6: Full Narrow Regression Pass

After Phases 1-5 pass individually, run the narrow full set:

```bash
uv run pytest polaris/tests/auth_test.py -q
uv run pytest polaris/tests/sep1/test_toml.py -q
uv run pytest polaris/tests/sep6/test_deposit.py -q
uv run pytest polaris/tests/sep6/test_withdraw.py -q
uv run pytest polaris/tests/sep24/test_deposit.py -q
uv run pytest polaris/tests/sep24/test_withdraw.py -q
uv run pytest polaris/tests/sep38/test_quote.py -q
uv run pytest polaris/tests/sep38/test_price.py -q
uv run pytest polaris/tests/sep38/test_prices.py -q
uv run pytest polaris/tests/sep12/test_customer.py -q
uv run pytest polaris/tests/sep58/test_accounts.py -q
uv run pytest polaris/tests/sep45/test_auth.py -q
```

Then run a broader suite if time allows:

```bash
uv run pytest polaris/tests -q
```

## 11. Files To Add

- `docs/sep45/polaris-sep45-min-implementation-todo-2026-04-14.md`
- `polaris/sep45/__init__.py`
- `polaris/sep45/urls.py`
- `polaris/sep45/views.py`
- `polaris/sep45/utils.py`
- `polaris/migrations/0015_webauthnonce.py`
- `polaris/management/commands/cleanup_sep45_nonces.py`
- `polaris/tests/sep45/test_auth.py`

## 12. Files To Modify

- `polaris/settings.py`
- `polaris/urls.py`
- `polaris/sep1/views.py`
- `polaris/cors.py`
- `polaris/models.py`
- `polaris/sep10/token.py`
- `polaris/sep10/utils.py`
- `polaris/tests/auth_test.py`
- `polaris/tests/helpers.py` if needed for contract-account mocks
- `polaris/tests/sep1/test_toml.py`
- `polaris/tests/sep6/test_deposit.py` (assertion tests only, no production-code changes)
- `polaris/tests/sep6/test_withdraw.py` (assertion tests only)
- `polaris/tests/sep24/test_deposit.py` (assertion tests only)
- `polaris/tests/sep24/test_withdraw.py` (assertion tests only)

SEP-6 / SEP-24 production code (`polaris/sep6/*.py`, `polaris/sep24/*.py`) is **not** modified.

## 13. Stop Conditions

Pause and reassess if any of these happen:

1. Existing SEP-10 auth tests fail after the compatibility changes.
2. SEP-24 interactive session tests fail because `C...` does not behave as an opaque `sub`.
3. The current `stellar-sdk` version lacks the Soroban auth types needed by the implementation.
4. The RPC client requires authenticated headers and the chosen client path does not expose them cleanly.
5. The generated migration includes unrelated model changes.

If one of these appears, stop broadening the patch. Fix the smallest blocking issue first.

## 14. Explicitly Deferred Work

Do not include in this implementation:

- repo-wide `WebAuthToken` abstraction
- repo-wide decorator rename
- SEP-31 contract-account support (the decorator will accept SEP-45 tokens at runtime, but the BPV receiver integration is not wired or tested for `C...` in v1)
- **Soroban/SAC payment SEND rail for `C...` destinations in SEP-6/24** — SEP-6/24 request validators continue to reject `C...`. Belongs in the immediate follow-up.
- **Soroban/SAC payment RECEIVE rail (CAP-0067)** — a payment observer that streams SAC `transfer` events from `SOROBAN_RPC_URL`, decodes the CAP-0067 memo data map (`SCMap { amount, memo }`), and matches against pending SEP-24 transactions. Without this, SEP-45-authenticated contract-account users cannot deposit through Soroban. **This is the immediate follow-up after SEP-45 auth lands.** Reference behavior: Anchor Platform `StellarRpcPaymentObserver.java:200-254`. Reference end-to-end flow: `sep45-reference/python-sep45/test_memo_flow_soroban.py` — a live-testnet test that walks `SAC.transfer → M(hotwallet, u64) → classic Payment MEMO_ID=u64` and asserts the same `u64` appears in the muxed destination, the SAC event data map, and the classic-hop Horizon memo. Read it first when scoping the observer.
- **CAP-0068 executable-type discrimination** — optional policy hardening. Only relevant if a deployment ever wants to restrict by Wasm/StellarAsset/Account. Independent of the CAP-0067 receive rail.
- transaction/account field renames like `web_auth_account`
- formal client-domain HTTPS policy hardening beyond the immediate fetch path
- stored audit digests for auth entries

Those belong in a second pass after the minimum rollout is green.

## 14a. Demo Wallet Smoke Step (Manual, Post-CI)

After the regression suite is green, run one manual interop check against the Stellar demo wallet (`/home/antb2/dev/rsync/stellar-demo-wallet/`) on testnet. Full discussion in companion spec §14; quick form here:

In scope for smoke test:

- SEP-45 challenge/token end-to-end.
- Contract-account passkey signing of Soroban auth entries (the wallet handles this in `packages/demo-wallet-shared/services/SmartWalletService.ts:319`).

Out of scope for the smoke test (deferred to a later rail):

- SEP-6/24 contract-account flows. The wallet sends `account=C...`; Polaris v1 returns 400 by design (Phase 5, companion spec §6).
- CAP-68 executable-type discrimination. The demo wallet does not exercise it; Polaris v1 does not call `get_address_executable`. See companion spec §14.5.

Operational notes:

- Polaris emits standard snake_case (`authorization_entries`, `network_passphrase`, `token`). Do not bend to camelCase.
- The demo wallet currently reads camelCase at `packages/demo-wallet-shared/methods/sep45Auth/start.ts:36` — needs a tiny local compatibility patch before it can hit Polaris. Track in the **wallet repo**, not Polaris.
- After the wallet patch, the wallet's challenge validator (`packages/demo-wallet-shared/methods/sep45Auth/sign.ts`) verifies sub-invocations, contract ID, function name, args, server signature, and footprint. If all pass and `POST /sep45/auth` returns a token, the challenge is interop-correct.

Do not block CI on this step. Record the result in the PR description.

## 14b. Operational Decisions Locked

Recorded so the implementer doesn't re-litigate them:

- **Soroban RPC failure mode**: RPC unreachable → 502, simulation error → 400, internal failure → 500. No verify-before-send pattern at the auth layer — no funds move there (companion spec §15.1).
- **Rate limiting**: deferred to deployment glue (WAF / nginx). Daily `cleanup_sep45_nonces` is the storage-side bound (companion spec §15.2).
- **Test signing strategy**: use `stellar_sdk.auth.authorize_entry(...)` locally in tests with a generated keypair as the "fake client". No on-chain contract account required. RPC simulation should be mocked in unit tests and exercised against testnet only in the end-to-end protocol test.
- **SEP-58 contract-account compatibility**: confirmed by code-read before Phase 3 work begins. `polaris/sep58/accounts.py` uses the shared decorator and treats `token.account` as opaque text; no change needed. Companion spec §7.3.

## 15. Definition Of Done

The minimum implementation is done when all of the following are true:

1. Polaris advertises SEP-45 in TOML (`WEB_AUTH_FOR_CONTRACTS_ENDPOINT`, `WEB_AUTH_CONTRACT_ID`, `SIGNING_KEY`).
2. `/sep45/auth` works for GET and POST (JSON and form bodies).
3. Nonce replay is rejected; oversized (>100 KB) payloads are rejected pre-decode.
4. Existing SEP-10 auth still works unchanged (`SERVER_JWT_KEY`).
5. `validate_sep10_token()` accepts SEP-45 JWTs (`SEP45_JWT_SECRET`) without a public API rename; tries SEP-10 first, falls back to SEP-45.
6. SEP-6/24 deposit/withdraw still reject `C...` request destinations with a clear 400.
7. SEP-6/24 deposit/withdraw succeed when the caller is SEP-45-authenticated (`token.account=C...`) AND the request supplies `account=G/M`.
8. `cleanup_sep45_nonces` removes expired rows; live rows are untouched.
9. The narrow regression suite passes.

That is the smallest useful SEP-45 rollout for this app.

## Annex A. Implementation Validation Notes - 2026-05-15

This annex records the current implementation review against this TODO and the companion SEP-45 implementation spec. It does not change the product scope. It turns the review findings into explicit implementation and test work so the remaining patch stays tied to the existing plan.

### A.1 Implementation Issues Found

Ranked from highest risk to lowest risk:

1. **HIGH - `web_auth_domain` derivation does not match the spec.** `polaris/sep45/utils.py::resolve_web_auth_domain()` currently returns the single configured `SEP10_HOME_DOMAINS[0]` when one non-wildcard home domain exists. The implementation spec requires `web_auth_domain == urlparse(settings.HOST_URL).netloc`. This matters when the auth server is on a subdomain or separate domain from the home domain; clients can reject the challenge because the signed args contain the wrong server domain.
2. **HIGH - `polaris/tests/sep45/test_auth.py` is missing.** The SEP-45 endpoint, nonce storage, replay protection, JSON/form POST support, malformed payload handling, OPTIONS behavior, JWT issuance, and cleanup command are not covered by the required dedicated test file.
3. **MEDIUM - SEP-45 JWT compatibility path is implemented but not tested.** `polaris/sep10/utils.py` tries `SERVER_JWT_KEY` first and falls back to `SEP45_JWT_SECRET`, and `polaris/sep10/token.py` accepts `C...` subjects. A regression test must prove `check_auth()` returns a `SEP10Token` with `account=C...`, `muxed_account=None`, and `memo=None`.
4. **MEDIUM - SEP-6/24 boundary tests are missing.** `polaris/tests/helpers.py` already has `mock_check_auth_success_contract_account()`, but the SEP-6/24 test files do not yet assert that `account=C...` request destinations still return 400 and that `token.account=C...` with an explicit `G/M` request destination succeeds.
5. **LOW - `validate_auth_entries()` should fail fast on an empty entry list.** The endpoint decoder rejects empty encoded payloads before this helper is reached, but the helper itself should still raise `SEP45ValidationError` instead of relying on `arg_maps[0]` and producing `IndexError` if called directly.

### A.2 Required Implementation Changes

Only the following production changes are needed from this validation pass:

1. In `polaris/sep45/utils.py`, change `resolve_web_auth_domain()` to derive from `urlparse(settings.HOST_URL).netloc`, falling back to `request_host` only if `HOST_URL` has no netloc.
2. In `polaris/sep45/utils.py`, add an explicit guard at the start of `validate_auth_entries()` that raises `SEP45ValidationError("authorization_entries cannot be empty")` when `entries` is empty.

Do not modify SEP-6 or SEP-24 production request validators. The v1 boundary remains: contract-account authentication is accepted through the shared auth decorator, but `account=C...` is not a classic payment destination.

### A.3 Required Test Changes

Add or update tests in these files:

1. `polaris/tests/sep45/test_auth.py`
   - nonce issue creates a row
   - first consume succeeds, second consume fails
   - expired nonce fails
   - `cleanup_sep45_nonces` deletes only rows expired by more than one day
   - `GET /sep45/auth` rejects missing `account`, missing `home_domain`, and invalid `home_domain`
   - `GET /sep45/auth` succeeds with mocked challenge construction and returns `authorization_entries` plus `network_passphrase`
   - `GET /sep45/auth` supports `client_domain` with a mocked TOML signing key lookup
   - `OPTIONS /sep45/auth` returns CORS preflight headers
   - `POST /sep45/auth` rejects missing, oversized, invalid-base64, empty, structurally invalid, and mismatched authorization entries
   - `POST /sep45/auth` succeeds for JSON and `application/x-www-form-urlencoded` bodies with mocked ledger and simulation helpers
   - replaying the same nonce is rejected
2. `polaris/tests/auth_test.py`
   - add a SEP-45 JWT fallback test for `check_auth()` / `validate_jwt_request()`
   - assert `SEP10Token.account == C...`, `muxed_account is None`, and `memo is None`
   - keep existing SEP-10 tests unchanged
3. `polaris/tests/sep6/test_deposit.py`
   - `account=C...` request destination returns 400
   - SEP-45-authenticated caller with request `account=G...` succeeds
4. `polaris/tests/sep6/test_withdraw.py`
   - `account=C...` request source returns 400
   - SEP-45-authenticated caller with request `account=G...` succeeds
5. `polaris/tests/sep24/test_deposit.py`
   - `account=C...` request destination returns 400
   - SEP-45-authenticated caller with request `account=G...` succeeds
6. `polaris/tests/sep24/test_withdraw.py`
   - `account=C...` request source returns 400
   - SEP-45-authenticated caller with request `account=G...` succeeds

### A.4 Testing Results Recorded

Validation commands run during review:

```bash
uv run pytest polaris/tests/auth_test.py polaris/tests/sep1/test_toml.py -q
```

Result:

- did not run because `uv` reported `No project table found in pyproject.toml`
- this repo is still Poetry-style (`[tool.poetry]`), so the current local verification path used the existing virtualenv

```bash
.venv/bin/pytest polaris/tests/auth_test.py polaris/tests/sep1/test_toml.py -q
```

Result:

- passed: `15 passed`
- warnings: PyJWT `InsecureKeyLengthWarning` from the existing short test `SERVER_JWT_KEY`

Post-implementation verification:

```bash
.venv/bin/pytest polaris/tests/sep45/test_auth.py polaris/tests/auth_test.py polaris/tests/sep6/test_deposit.py polaris/tests/sep6/test_withdraw.py polaris/tests/sep24/test_deposit.py polaris/tests/sep24/test_withdraw.py polaris/tests/sep1/test_toml.py -q
```

Result:

- passed: `179 passed`
- warnings: existing muxed-account deprecation warnings and short test JWT secret warnings

```bash
.venv/bin/pytest polaris/tests/sep38/test_quote.py polaris/tests/sep38/test_price.py polaris/tests/sep38/test_prices.py polaris/tests/sep12/test_customer.py polaris/tests/sep58/test_accounts.py -q
```

Result:

- passed: `207 passed`

```bash
.venv/bin/pytest polaris/tests -q
```

Result:

- passed: `658 passed`
- warnings: existing muxed-account deprecation warnings, short test JWT secret warnings, and existing Stellar SDK transaction timebounds warnings
