# SEP-45 Implementation TODO — Django Polaris BPV

**Date:** 2026-03-26
**Prereq:** `docs/polaris-sep45-architecture-2026-03-26.md`
**Spec:** SEP-45 v0.1.1 (Draft)
**stellar-sdk:** 12.1.0 installed (>= 12 required for `AuthMode.ENFORCE`) -- verified compatible

---

## Phase 1: Preparatory Refactor

No SEP-45 endpoints. Goal: generalize the auth layer so Phase 2 drops in cleanly.

---

### 1.1 Add `"sep-45"` to accepted SEPs

**File:** `polaris/settings.py:44-54`

```python
# Current accepted_seps list (line 44-54)
accepted_seps = [
    "sep-1", "sep-6", "sep-6", "sep-10", "sep-12",
    "sep-24", "sep-31", "sep-38", "sep-58",
]
```

- Add `"sep-45"` to the list
- Fix the duplicate `"sep-6"` on line 47 while here

---

### 1.2 Add SEP-45 settings with startup validation

**File:** `polaris/settings.py` — add new section after line 133 (after `SEP10_CLIENT_ATTRIBUTION_DENYLIST`)

New settings, loaded only when `"sep-45" in ACTIVE_SEPS`:

| Setting | Type | Required | Default | Validation |
|---|---|---|---|---|
| `SOROBAN_RPC_URL` | str | Yes | — | Must start with `http`. Must be reachable at startup. |
| `SEP45_WEB_AUTH_CONTRACT_ID` | str | Yes | — | Must start with `C`. Validate via `Address(value)`. |
| `SEP45_JWT_SECRET` | str | Yes | — | Min 32 bytes. **Separate from `SERVER_JWT_KEY`**. |
| `SEP45_HOME_DOMAINS` | list | No | Falls back to `SEP10_HOME_DOMAINS` | Each must be hostname, not URL. |
| `SEP45_WEB_AUTH_DOMAIN` | str | No | `urlparse(HOST_URL).netloc` | Must be hostname (used for args map and domain comparison). |
| `SEP45_SIGNATURE_EXPIRATION_BUFFER` | int | No | `10` | Ledgers ahead of current for signature expiration. Must be positive. Configurable so operators can tune for network conditions. |
| `SEP45_AUTH_TIMEOUT` | int | No | `900` | Must be positive. |
| `SEP45_JWT_TIMEOUT` | int | No | `86400` | Must be positive. |

**JWT `iss` is a URI, not a bare hostname.** The spec requires `iss` to be "a Uniform Resource Identifier (URI) for the issuer (`https://example.com` or `https://example.com/G...`)" per [SEP-45 Token section](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md) and RFC 7519 Section 4.1.1. The `iss` claim must be constructed from `HOST_URL` (which is already a URI), not from `SEP45_WEB_AUTH_DOMAIN` (which is a bare authority). Example: `iss = settings.HOST_URL` → `"https://anchor.example.com"`. Do not use the bare hostname.

Also require `SIGNING_SEED` when `"sep-45"` is active (shared signing key with SEP-10).

Create `SorobanServer` instance at module level (like `HORIZON_SERVER` on line 86):
```python
SOROBAN_SERVER = None
if "sep-45" in ACTIVE_SEPS:
    from stellar_sdk import SorobanServer
    SOROBAN_SERVER = SorobanServer(SOROBAN_RPC_URL)
```

**Validation order:** Validate all settings at import time (startup). This is the config-before-feature pattern from Anchor Platform [PR #1639](https://github.com/stellar/anchor-platform/pull/1639).

---

### 1.3 Introduce `WebAuthToken` base class

**New file:** `polaris/webauth/__init__.py` (empty)
**New file:** `polaris/webauth/token.py`

Extract a base class from `polaris/sep10/token.py:17-164`. The base handles JWT decoding, claim validation, and the common properties. `SEP10Token` becomes a subclass.

#### `WebAuthToken` (base)

Handles:
- JWT decode with configurable secret (passed in or from settings)
- Required fields: `iss`, `sub`, `iat`, `exp`
- Timestamp validation (`iat <= now <= exp`)
- `client_domain` validation
- Properties: `account`, `client_domain`, `issuer`, `issued_at`, `expires_at`, `payload`
- New property: `is_contract_account` — `self._payload["sub"].startswith("C")`
- New property: `auth_mechanism` — `"sep45"` if `C...`, else `"sep10"`

**Critical change in `__init__`:** The current code at `token.py:58-62` calls `Keypair.from_public_key(stellar_account)` which rejects `C...` addresses. The base class must route:

```python
if stellar_account:
    if stellar_account.startswith("C"):
        Address(stellar_account)          # validates C... format
    else:
        Keypair.from_public_key(stellar_account)  # validates G... format
```

Import needed: `from stellar_sdk import Address`

#### `SEP10Token` subclass

Keeps: `muxed_account`, `memo` properties (only relevant for G.../M... accounts).

Override `__init__` to add M.../memo parsing before calling `super().__init__()`, or keep the parsing in the base with branch logic.

**Decision:** Keep all parsing in the base class with prefix-based branching (`M...` → muxed, `C...` → contract, `:` → memo, else → plain G...). This avoids splitting the parsing logic across classes. `muxed_account` and `memo` return `None` for `C...` accounts.

#### Backward compatibility

```python
# polaris/sep10/token.py — becomes a thin re-export
from polaris.webauth.token import SEP10Token, WebAuthToken  # noqa: F401
```

All existing `from polaris.sep10.token import SEP10Token` imports continue to work.

---

### 1.4 Rename auth decorator

**File:** `polaris/sep10/utils.py:24-62`

Current:
```python
def validate_sep10_token():
    ...
def validate_jwt_request(request: Request) -> SEP10Token:
    ...
```

Add new names, alias the old:

```python
def validate_web_auth_token():
    """Decorator to validate a SEP-10 or SEP-45 bearer token."""
    def decorator(view):
        def wrapper(request, *args, **kwargs):
            return check_auth(request, view, *args, **kwargs)
        return wrapper
    return decorator

# Backward compatibility
validate_sep10_token = validate_web_auth_token
```

Update `validate_jwt_request` to accept either `SERVER_JWT_KEY` or `SEP45_JWT_SECRET`:

```python
def validate_web_auth_request(request: Request) -> WebAuthToken:
    # Extract Bearer token from Authorization header (unchanged)
    # Try decoding with SERVER_JWT_KEY first
    # If that fails and SEP-45 is active, try SEP45_JWT_SECRET
    # Return WebAuthToken (or SEP10Token subclass)
```

**Important:** The SEP-45 JWT uses a different secret. The decoder must try both. Order: try `SERVER_JWT_KEY` first (SEP-10 is the common case), then `SEP45_JWT_SECRET`.

Update error message at line 62 from `"SEP-10 token error:"` to `"authentication token error:"`.

---

### 1.5 Update type hints across all consumers

Every file that imports `SEP10Token` for type hints needs updating to `WebAuthToken`. These are the **exact files and lines** (verified by codebase audit):

#### View endpoints (decorator + type hint)

| File | Import lines | Decorator lines | Type hint lines |
|---|---|---|---|
| `polaris/sep6/deposit.py` | 38-39 | 53, 61 | 54, 62, 66, 129, 213 |
| `polaris/sep6/withdraw.py` | 31-32 | 52, 60 | 53, 61, 65, 156 |
| `polaris/sep6/transaction.py` | 14-15 | 43, 59, 70 | 44, 60, 71 |
| `polaris/sep6/utils.py` | 9 | — | 17 |
| `polaris/sep24/deposit.py` | 39-40 | 415 | 416 |
| `polaris/sep24/withdraw.py` | 44-45 | 432 | 436 |
| `polaris/sep24/transaction.py` | 13-14 | 30, 44 | 32, 46 |
| `polaris/sep31/transactions.py` | 17-18 | 43, 76, 118 | 45, 78, 120, 205 |
| `polaris/sep12/customer.py` | 22-23 | 35, 81, 124, 171, 211 | 36, 82, 125, 173, 212, 288, 324 |
| `polaris/sep38/prices.py` | 10-11 | 28, 76 | 29, 77, 109, 166 |
| `polaris/sep38/quote.py` | 16-17 | 34, 46 | 35, 47, 86, 169 |
| `polaris/sep58/accounts.py` | 10-11 | 21, 30 | 22, 31, 46, 72 |
| `polaris/shared/endpoints.py` | 27 | — | 212, 272, 379 |
| `polaris/utils.py` | 41 | — | 457, 532 |

#### Integration base classes (type hint in method signatures + docstrings)

| File | Import line | Type hint lines |
|---|---|---|
| `polaris/integrations/customers.py` | 5 | 11, 34, 60, 105, 128, 159 |
| `polaris/integrations/transactions.py` | 11 | 464, 526, 657, 804, 822, 858 |
| `polaris/integrations/quote.py` | 6 | 13, 62, 110 |
| `polaris/integrations/sep31.py` | 6 | 79, 171, 206 |
| `polaris/integrations/external_account.py` | 3 | 30, 53, 60 |

**Approach:** In each file, change:
- `from polaris.sep10.token import SEP10Token` → `from polaris.webauth.token import WebAuthToken`
- `token: SEP10Token` → `token: WebAuthToken`
- `from polaris.sep10.utils import validate_sep10_token` → `from polaris.webauth.utils import validate_web_auth_token`
- `@validate_sep10_token()` → `@validate_web_auth_token()`

Keep backward-compatible re-exports in `polaris/sep10/token.py` and `polaris/sep10/utils.py` so any anchor code importing from the old paths still works.

#### Tests

| File | Import line | Usage lines |
|---|---|---|
| `polaris/tests/auth_test.py` | 17 | 164, 197, 241, 286, 370 (isinstance checks) |
| `polaris/tests/sep6/test_deposit.py` | 24 | 32, 308, 338, 400, 436, 466, 498, 531, 563, 604 |
| `polaris/tests/sep6/test_withdraw.py` | 25 | 34, 317, 353, 470, 515 |

Update imports. The `isinstance(token, SEP10Token)` checks in `auth_test.py` should also pass for `WebAuthToken` since `SEP10Token` is a subclass.

---

### 1.6 Update Transaction model docstrings

**File:** `polaris/models.py:478-497`

Change:
- Line 479-483: `"The Stellar (G...) account authenticated via SEP-10"` → `"The Stellar account (G... or C...) authenticated via SEP-10 or SEP-45"`
- Line 485-490: `"The muxed (M...) account authenticated via SEP-10"` → `"The muxed (M...) account authenticated via SEP-10. Always null for SEP-45 (contract) accounts."`
- Line 492-497: Similar update for `account_memo`

**Optional:** Add `auth_mechanism` field to Transaction model:
```python
auth_mechanism = models.CharField(max_length=5, null=True, blank=True)
# "sep10" or "sep45" — for operational auditability
```
This requires a migration. Decide if needed now or later.

Also update the Quote model docstrings (same fields at `polaris/utils.py:521-523, 595-597`).

---

## Phase 2: SEP-45 Core

Isolated in `polaris/sep45/` and `polaris/webauth/`. No changes to existing SEP endpoints.

---

### 2.1 Create `WebAuthNonce` model

**New file:** `polaris/webauth/models.py`

```python
class WebAuthNonce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    nonce = models.CharField(max_length=64, db_index=True)
    account = models.CharField(max_length=56)        # C... address
    used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    home_domain = models.CharField(max_length=255, blank=True, default="")
    client_domain = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = [("account", "nonce")]
```

Issue function:
```python
def issue_nonce(account: str, home_domain: str, client_domain: str, timeout: int) -> str:
    nonce = secrets.token_hex(16)
    expires_at = timezone.now() + timedelta(seconds=timeout)
    WebAuthNonce.objects.create(
        nonce=nonce, account=account, expires_at=expires_at,
        home_domain=home_domain, client_domain=client_domain,
    )
    return nonce
```

Atomic consume function (single UPDATE, no read-then-write):
```python
def consume_nonce(account: str, nonce: str) -> bool:
    now = timezone.now()
    updated = WebAuthNonce.objects.filter(
        account=account, nonce=nonce, used=False, expires_at__gt=now
    ).update(used=True, used_at=now)
    return updated == 1
```

This matches the pattern from Anchor Platform [PR #1906](https://github.com/stellar/anchor-platform/pull/1906).

**Migration:** Create `polaris/migrations/XXXX_add_webauthnonce.py` via `makemigrations`.

---

### 2.2 Create nonce cleanup management command

**New file:** `polaris/management/commands/cleanup_nonces.py`

```python
class Command(BaseCommand):
    def handle(self, *args, **options):
        deleted, _ = WebAuthNonce.objects.filter(expires_at__lt=timezone.now()).delete()
        if deleted:
            logger.info(f"SEP45 nonce cleanup: deleted {deleted} expired nonces")
```

Run via cron: `*/15 * * * * python manage.py cleanup_nonces`

---

### 2.3 Create client-domain helper

**New file:** `polaris/webauth/client_domain.py`

Resolves the `SIGNING_KEY` from a client domain's `stellar.toml`.

```python
def get_client_domain_signing_key(client_domain: str) -> str:
    """Fetch SIGNING_KEY from client_domain's stellar.toml. HTTPS only on pubnet."""
```

Security requirements (from Anchor Platform [PR #1865](https://github.com/stellar/anchor-platform/pull/1865)):
- HTTPS by default
- HTTP fallback **only** when `STELLAR_NETWORK_PASSPHRASE` is testnet passphrase
- Bound redirect count (max 3)
- Bound response size (max 100KB)
- Timeout: `SEP10_CLIENT_ATTRIBUTION_REQUEST_TIMEOUT` (default 3s)
- Validate `SIGNING_KEY` with `Keypair.from_public_key()`

This largely mirrors the existing `SEP10Auth._get_client_signing_key()` at `polaris/sep10/views.py:319-336`. Extract and generalize.

---

### 2.4 Create SEP-45 views — GET challenge

**New file:** `polaris/sep45/views.py`

#### `SEP45Auth` class (APIView)

##### `get(self, request)` — Challenge generation

Request params:
- `account` (required) — must start with `C`, validate via `Address(account)`
- `home_domain` (required) — must match `SEP45_HOME_DOMAINS` patterns (support wildcards)
- `client_domain` (optional) — must be valid hostname

**Step-by-step implementation** (maps to `sep45_server.py` functions):

1. **Request-shape validation** — return 400 for missing/invalid params before any logic. Separate from protocol validation ([PR #1870](https://github.com/stellar/anchor-platform/pull/1870)).

2. **Validate `account`:**
   ```python
   if not account or not account.startswith("C"):
       return error_response("'account' must be a contract account (C...)", 400)
   try:
       Address(account)
   except:
       return error_response("invalid 'account'", 400)
   ```

3. **Validate `home_domain`** — domain normalization by authority, not raw string ([PR #1774](https://github.com/stellar/anchor-platform/pull/1774)):
   ```python
   if not home_domain:
       return error_response("'home_domain' is required", 400)
   if not any(domain_matches(pattern, home_domain) for pattern in settings.SEP45_HOME_DOMAINS):
       return error_response(f"invalid 'home_domain'. Accepted: {settings.SEP45_HOME_DOMAINS}", 400)
   ```

4. **Resolve `client_domain`** (optional):
   ```python
   if client_domain:
       if urlparse(f"https://{client_domain}").netloc != client_domain:
           return error_response("'client_domain' must be a valid hostname", 400)
       client_signing_key = get_client_domain_signing_key(client_domain)
   ```

5. **Build args map as SCVal:**
   ```python
   args_map = scval.to_map({
       scval.to_symbol("account"): scval.to_string(account),
       scval.to_symbol("home_domain"): scval.to_string(home_domain),
       scval.to_symbol("web_auth_domain"): scval.to_string(web_auth_domain),
       scval.to_symbol("web_auth_domain_account"): scval.to_string(settings.SIGNING_KEY),
       scval.to_symbol("nonce"): scval.to_string(nonce),
   })
   # Add client_domain + client_domain_account if present
   ```

6. **Issue nonce:**
   ```python
   nonce = issue_nonce(account, home_domain, client_domain or "", settings.SEP45_AUTH_TIMEOUT)
   ```

7. **Build and simulate transaction:**
   ```python
   rpc = settings.SOROBAN_SERVER
   source = rpc.load_account(settings.SIGNING_KEY)  # Use signing key as source
   tx = (
       TransactionBuilder(source, network_passphrase=settings.STELLAR_NETWORK_PASSPHRASE, base_fee=100)
       .append_invoke_contract_function_op(
           contract_id=settings.SEP45_WEB_AUTH_CONTRACT_ID,
           function_name="web_auth_verify",
           parameters=[args_map],
       )
       .set_timeout(settings.SEP45_AUTH_TIMEOUT)
       .build()
   )
   simulation = rpc.simulate_transaction(tx)
   ```

8. **Extract auth entries from simulation:**
   ```python
   entries = [xdr.SorobanAuthorizationEntry.from_xdr(item) for item in simulation.results[0].auth]
   ```

9. **Sign server entry** — use configurable buffer, not hardcoded `+10`:
   ```python
   latest = rpc.get_latest_ledger()
   valid_until = latest.sequence + settings.SEP45_SIGNATURE_EXPIRATION_BUFFER  # default 10
   signed_entries = []
   for entry in entries:
       if (entry.credentials.type == xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS
           and Address.from_xdr_sc_address(entry.credentials.address.address).address == settings.SIGNING_KEY):
           signed_entries.append(auth.authorize_entry(
               entry=entry, signer=Keypair.from_secret(settings.SIGNING_SEED),
               valid_until_ledger_sequence=valid_until,
               network_passphrase=settings.STELLAR_NETWORK_PASSPHRASE,
           ))
       else:
           signed_entries.append(entry)
   ```

10. **Encode and return:**
    ```python
    entries_b64 = encode_auth_entries(signed_entries)
    return Response({
        "authorization_entries": entries_b64,
        "network_passphrase": settings.STELLAR_NETWORK_PASSPHRASE,
    })
    ```

**Stellar SDK imports needed:**
```python
from stellar_sdk import Address, Keypair, SorobanServer, TransactionBuilder, auth, scval, xdr
from stellar_sdk.soroban_rpc import AuthMode
```

---

### 2.5 Create SEP-45 views — POST token exchange

##### `post(self, request)` — Token exchange

1. **Size guard** — reject > 100KB before any parsing ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900)):
   ```python
   entries_b64 = request.data.get("authorization_entries")
   if not entries_b64:
       return error_response("'authorization_entries' is required", 400)
   if len(entries_b64) > 102400:
       return error_response("'authorization_entries' payload too large", 400)
   ```

2. **Decode auth entries** (XDR deserialization with strict validation):
   ```python
   entries = decode_auth_entries(entries_b64)
   # Uses xdrlib3.Unpacker, validates all bytes consumed
   ```

3. **Resolve `web_auth_domain`:**
   ```python
   web_auth_domain = settings.SEP45_WEB_AUTH_DOMAIN
   ```

4. **Get current ledger:**
   ```python
   current_ledger = settings.SOROBAN_SERVER.get_latest_ledger().sequence
   ```

5. **Validate entries** — comprehensive checks (maps to `validate_auth_entries()`):
   - For each entry:
     - Check `credentials.type == SOROBAN_CREDENTIALS_ADDRESS`
     - Check `signature_expiration_ledger > current_ledger`
     - Check `sub_invocations` is empty
     - Check `function.type == SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN`
     - Check `contract_address == SEP45_WEB_AUTH_CONTRACT_ID`
     - Check `function_name == "web_auth_verify"`
     - Check args is a single map
   - Verify all entries have identical args (JSON-stringify with sorted keys)
   - Verify required args: `account`, `home_domain`, `nonce`, `web_auth_domain`, `web_auth_domain_account`
   - Validate arg values:
     - `account` starts with `C`
     - `home_domain` in allowed domains
     - `web_auth_domain` matches expected
     - `web_auth_domain_account` matches `SIGNING_KEY`
   - Check `client_domain` / `client_domain_account` appear together
   - Verify all required auth addresses present (server, client, optional client_domain)
   - **Extra auth entries policy** (resolves architecture doc Section 11.1): If the contract's `__check_auth` produces additional auth entries beyond client/server/client-domain (e.g., sub-signers in a multi-sig contract), **accept them** provided they call the same `WEB_AUTH_CONTRACT_ID` with the same `web_auth_verify` function and identical args. Reject entries that call a different contract or have different args. This is the most permissive correct policy — it allows complex `__check_auth` flows while preventing unrelated contract invocations.

6. **Consume nonce atomically:**
   ```python
   account = str(args["account"])
   nonce_value = str(args["nonce"])
   if not consume_nonce(account, nonce_value):
       return error_response("invalid or expired nonce", 400)
   ```

7. **Simulate in ENFORCE mode:**
   ```python
   # Rebuild transaction with signed entries
   tx = TransactionBuilder(source, ...).append_invoke_contract_function_op(
       contract_id=settings.SEP45_WEB_AUTH_CONTRACT_ID,
       function_name="web_auth_verify",
       parameters=[args_scval],
       auth=entries,  # client-signed entries
   ).set_timeout(settings.SEP45_AUTH_TIMEOUT).build()

   simulation = rpc.simulate_transaction(tx, auth_mode=AuthMode.ENFORCE)
   if simulation.error:
       return error_response(f"transaction simulation failed: {simulation.error}", 400)
   ```

8. **Issue JWT** — `iss` must be a URI per RFC 7519 Section 4.1.1 and SEP-45 spec:
   ```python
   now = int(time.time())
   claims = {
       "iss": settings.HOST_URL,                # URI, e.g. "https://anchor.example.com"
       "sub": str(args["account"]),              # C... address
       "iat": now,
       "exp": now + settings.SEP45_JWT_TIMEOUT,
       "jti": hashlib.sha256(entries_b64.encode()).hexdigest(),
       "home_domain": str(args["home_domain"]),
   }
   if "client_domain" in args:
       claims["client_domain"] = str(args["client_domain"])

   token = jwt.encode(claims, settings.SEP45_JWT_SECRET, algorithm="HS256")
   return Response({"token": token})
   ```

   **Not** `web_auth_domain` (bare hostname). The spec example is `https://example.com`. This matches Polaris SEP-10 behavior (`sep10/views.py:310` uses `os.path.join(settings.HOST_URL, "auth")`).
   ```

---

### 2.6 XDR encode/decode helpers

**New file:** `polaris/sep45/xdr_utils.py`

```python
def encode_auth_entries(entries: list[xdr.SorobanAuthorizationEntry]) -> str:
    """Pack entries to base64. Format: [uint32 count][entry1][entry2]..."""

def decode_auth_entries(payload_b64: str) -> list[xdr.SorobanAuthorizationEntry]:
    """Unpack base64 to entries. Validate all bytes consumed."""
```

Uses `xdrlib3.Packer` / `xdrlib3.Unpacker`. Validates `unpacker.get_position() == len(raw)`.

---

### 2.7 Domain normalization and matching

**New file or add to** `polaris/sep45/utils.py`

Anchor Platform [PR #1774](https://github.com/stellar/anchor-platform/pull/1774) proved that raw-string domain comparison fails because wallets, config, and TOML normalize differently (`example.com` vs `https://example.com` vs `example.com:443` vs `localhost:8080`). Compare by **parsed authority**, not raw string.

```python
def normalize_domain(value: str) -> str:
    """Normalize a domain value to a bare authority for comparison.
    Handles: 'example.com', 'https://example.com', 'example.com:443', etc.
    """
    if "://" in value:
        return urlparse(value).netloc
    # Strip default HTTPS port if present
    if value.endswith(":443"):
        return value[:-4]
    return value

def domain_matches(pattern: str, value: str) -> bool:
    """Match domain against pattern, supporting *.example.com wildcards.
    Both pattern and value are normalized before comparison."""
    value = normalize_domain(value)
    pattern_norm = normalize_domain(pattern)
    if pattern_norm.startswith("*."):
        suffix = pattern_norm[1:]  # ".example.com"
        return value.endswith(suffix) or value == pattern_norm[2:]
    return pattern_norm == value
```

---

### 2.8 URL routing

**New file:** `polaris/sep45/urls.py`

```python
from django.urls import path
from polaris.sep45.views import SEP45Auth

urlpatterns = [path("auth", SEP45Auth.as_view())]
```

**File:** `polaris/urls.py` — add after line 44:

```python
if "sep-45" in settings.ACTIVE_SEPS:
    urlpatterns.append(path("sep45/", include("polaris.sep45.urls")))
```

---

### 2.9 TOML update

**File:** `polaris/sep1/views.py:87-88` — add after SEP-58 block:

```python
if "sep-45" in settings.ACTIVE_SEPS:
    toml_dict["WEB_AUTH_FOR_CONTRACTS_ENDPOINT"] = os.path.join(settings.HOST_URL, "sep45", "auth")
    toml_dict["WEB_AUTH_CONTRACT_ID"] = settings.SEP45_WEB_AUTH_CONTRACT_ID
    # SIGNING_KEY already set by SEP-10 block (line 80). If SEP-10 is not active but SEP-45 is:
    if "sep-10" not in settings.ACTIVE_SEPS:
        toml_dict["SIGNING_KEY"] = settings.SIGNING_KEY
```

---

### 2.10 CORS

**File:** `polaris/cors.py:4-13` — add to allowed paths:

```python
or request.path.startswith("/sep45")
```

**File:** `polaris/middleware.py:62-70` — add to `SEP24_URLS` if SEP-45 interactive flows exist (unlikely for now, skip).

---

## Phase 3: Downstream SEP Updates

Accept `C...` accounts in SEP-6, SEP-24, SEP-12, SEP-38.

---

### 3.1 SEP-6 deposit — accept `C...` in account parameter

**File:** `polaris/sep6/deposit.py:162-171`

Current code validates the `account` request param:
```python
if account.startswith("M"):
    StrKey.decode_muxed_account(account)
else:
    Keypair.from_public_key(account)  # ← FAILS for C...
```

Fix — add `C...` branch:
```python
if account.startswith("M"):
    StrKey.decode_muxed_account(account)
elif account.startswith("C"):
    Address(account)  # validate contract address
else:
    Keypair.from_public_key(account)
```

Same fix at **line 331-332** (account creation support check).

---

### 3.2 SEP-6 withdraw — accept `C...` in account parameter

**File:** `polaris/sep6/withdraw.py:162-171`

Same pattern as deposit. Add `C...` branch.

---

### 3.3 SEP-24 deposit — accept `C...` destination account

**File:** `polaris/sep24/deposit.py:475-484`

Current code:
```python
if destination_account.startswith("M"):
    stellar_account = StrKey.decode_muxed_account(destination_account).ed25519
elif:
    Keypair.from_public_key(destination_account)  # ← FAILS for C...
```

Fix — add `C...` branch:
```python
if destination_account.startswith("M"):
    ...
elif destination_account.startswith("C"):
    Address(destination_account)  # validate contract address
else:
    Keypair.from_public_key(destination_account)
```

---

### 3.4 SEP-24 withdraw — accept `C...` source account

**File:** `polaris/sep24/withdraw.py:468-477`

Same pattern. Add `C...` branch.

---

### 3.5 SEP-24 interactive flow — verify `C...` handling

**File:** `polaris/sep24/utils.py:134-144`

Current code:
```python
if jwt_dict["sub"].startswith("M"):
    muxed_account = jwt_dict["sub"]
    stellar_account = MuxedAccount.from_account(muxed_account).account_id
    account_memo = None
elif ":" in jwt_dict["sub"]:
    stellar_account, account_memo = jwt_dict["sub"].split(":")
    muxed_account = None
else:
    stellar_account = jwt_dict["sub"]
    account_memo = None
    muxed_account = None
```

This **accidentally works** for `C...` (falls through to `else` branch). But add an explicit branch for clarity and future safety:

```python
if jwt_dict["sub"].startswith("M"):
    ...
elif jwt_dict["sub"].startswith("C"):
    stellar_account = jwt_dict["sub"]
    account_memo = None
    muxed_account = None
elif ":" in jwt_dict["sub"]:
    ...
else:
    ...
```

**File:** `polaris/sep24/utils.py:290-305` — `generate_interactive_jwt()`

Works for `C...` (memo is None, so `sub` = `"C..."`). No change needed. Add comment for clarity.

---

### 3.6 SEP-12 customer — no code change needed

`polaris/sep12/customer.py` passes `token.muxed_account or token.account` to integration callbacks. For `C...` tokens, `muxed_account` is `None`, so `token.account` (the `C...` address) is passed. Works correctly.

Anchor-implementors may need to update their `CustomerIntegration` to handle `C...` identities. Document this.

---

### 3.7 SEP-38 — no code change needed

`polaris/sep38/prices.py` and `polaris/sep38/quote.py` use the token for authorization but don't validate account format. Works after Phase 1 rename.

---

### 3.8 SEP-31 — defer

Current SEP-31 spec is less explicit about SEP-45 than SEP-6/24/38. Defer unless concrete interop need arises.

---

## Phase 4: Hardening and Tests

---

### 4.1 Tests — unit tests for `WebAuthToken`

**New file:** `polaris/tests/webauth/test_token.py`

Test cases:
- `C...` address in `sub` → `is_contract_account == True`, `muxed_account == None`, `memo == None`
- `G...` address in `sub` → backward compatible with existing behavior
- `M...` address in `sub` → backward compatible
- `G...:memo` in `sub` → backward compatible
- Invalid `C...` → `ValueError`
- Expired token → `ValueError`
- Missing required claims → `ValueError`

---

### 4.2 Tests — nonce model

**New file:** `polaris/tests/webauth/test_nonce.py`

Test cases:
- Issue and consume → success
- Consume same nonce twice → second returns False (atomic)
- Consume expired nonce → returns False
- Consume with wrong account → returns False
- **Concurrent consume** — two threads race to consume same nonce, only one succeeds

---

### 4.3 Tests — SEP-45 GET endpoint

**New file:** `polaris/tests/sep45/test_challenge.py`

**Malformed request shape:**
- Missing `account` → 400
- Non-`C...` account (e.g. `G...`) → 400
- Invalid `C...` address (bad checksum) → 400
- Missing `home_domain` → 400
- Invalid `home_domain` → 400
- Invalid `client_domain` hostname → 400
- `client_domain` TOML fetch failure → 400
- `client_domain` missing SIGNING_KEY → 400

**Home-domain normalization** (Anchor Platform [PR #1774](https://github.com/stellar/anchor-platform/pull/1774)):
- `example.com` vs `https://example.com` → both match if configured as `example.com`
- `example.com:443` → matches `example.com` (default HTTPS port stripped)
- `localhost:8080` → matches `localhost:8080` (non-default port preserved)
- Wildcard `*.example.com` matches `sub.example.com` but not `example.com`

**Happy path:**
- Valid request → 200 with `authorization_entries` and `network_passphrase`
- Valid request with `client_domain` → 200 with entries including client-domain signer entry

**RPC failure modes** (Anchor Platform [PR #1904](https://github.com/stellar/anchor-platform/pull/1904)):
- RPC unreachable → **503** with `Retry-After` header, not 500
- RPC returns error on simulation → 502 with descriptive error
- Authenticated RPC provider with correct headers → 200 (verify headers actually propagate)
- Authenticated RPC provider with missing/wrong headers → 503 (not silent failure)

---

### 4.4 Tests — SEP-45 POST endpoint

**New file:** `polaris/tests/sep45/test_token_exchange.py`

**Malformed/oversized XDR:**
- Empty `authorization_entries` → 400
- Oversized payload (> 100KB) → 400
- Invalid base64 → 400
- Invalid XDR (corrupted bytes) → 400
- Extra bytes after XDR → 400
- Truncated XDR → 400

**Args mismatch across entries:**
- All entries identical args → pass
- One entry has different `account` arg → 400
- One entry has extra arg → 400

**Missing/invalid server entry:**
- Missing server auth entry entirely → 400
- Server entry with wrong credential address → 400

**Missing/invalid client-domain signer entry:**
- `client_domain` + `client_domain_account` in args, client-domain auth entry **unsigned** (valid metadata, absent signature) → 400 (simulation ENFORCE fails)
- `client_domain` without `client_domain_account` in args → 400
- `client_domain_account` without `client_domain` → 400

**Other protocol violations:**
- Contract address mismatch → 400
- Function name mismatch → 400
- Sub-invocations present → 400
- Missing required args → 400
- `web_auth_domain` mismatch → 400
- `web_auth_domain_account` mismatch → 400
- Missing client auth entry → 400
- Expired signature ledger → 400
- Expired nonce → 400
- Replayed nonce → 400

**Nonce replay under concurrency:**
- Two threads POST same nonce simultaneously → exactly one gets 200, the other gets 400

**Expired signature ledger:**
- Signature expiration ledger < current ledger → 400 with clear error message ("authorization entry signature has expired")

**ENFORCE simulation failure:**
- Invalid client signature → 400 (simulation fails)

**JWT claims validation:**
- Successful token → verify `iss` is a URI (e.g. `https://anchor.example.com`), not a bare hostname
- Successful token → verify `sub` is the `C...` account from args
- Successful token → verify `home_domain` claim matches request
- Successful token → verify `client_domain` claim present iff `client_domain` was in challenge
- Successful token → verify `jti` is SHA-256 hex of the entries payload
- Successful token → verify `exp` = `iat` + `SEP45_JWT_TIMEOUT`
- Successful token → verify signed with `SEP45_JWT_SECRET`, NOT `SERVER_JWT_KEY`

**Content-type and CORS** (SEP-45 spec requires both POST formats):
- `Content-Type: application/json` with JSON body → 200
- `Content-Type: application/x-www-form-urlencoded` with form body → 200
- `OPTIONS /sep45/auth` → proper preflight response with `Access-Control-Allow-Origin: *`
- `Access-Control-Allow-Origin: *` header present on **error** responses (400, 503), not just success

**Extra auth entries policy** (architecture doc Section 11.1):
If the contract's `__check_auth` produces additional auth entries beyond the three specified types (client, server, client-domain), behavior must be deterministic. **Policy decision: accept extra entries** if they call the same contract with identical args, reject if they call a different contract or have different args. Test both cases.

---

### 4.5 Tests — mixed SEP-10/SEP-45 bearer handling

**New file:** `polaris/tests/webauth/test_mixed_auth.py`

These test the shared auth surface with two authenticator types on the same endpoints:

**Same endpoint accepts both token types:**
- SEP-6 `/sep6/deposit` with valid SEP-10 JWT (`G...` sub) → 200
- SEP-6 `/sep6/deposit` with valid SEP-45 JWT (`C...` sub) → 200
- SEP-12 `/kyc/customer` GET with SEP-10 JWT → 200
- SEP-12 `/kyc/customer` GET with SEP-45 JWT → 200
- SEP-38 `/sep38/quote` with SEP-10 JWT → 200
- SEP-38 `/sep38/quote` with SEP-45 JWT → 200
- Invalid JWT (wrong secret) → 403 from both paths
- Expired SEP-45 JWT → 403

**Ownership isolation across G... and C... identities:**
- Create transaction with SEP-45 JWT (C... account) → success
- Query that transaction with SEP-10 JWT (G... account for different user) → not found / 403
- Query that transaction with SEP-10 JWT for an account that happens to share a name → not found (C... and G... are different namespaces)
- Create transaction with SEP-10 JWT → query with SEP-45 JWT → not found

---

### 4.6 Tests — downstream SEP-6/24 with `C...` token

Add test cases to existing test files verifying:
- SEP-6 deposit with `C...` in `account` request param → accepted (not rejected by `Keypair.from_public_key`)
- SEP-6 withdraw with `C...` in `account` request param → accepted
- SEP-24 deposit with `C...` destination account → accepted
- SEP-24 withdraw with `C...` source account → accepted
- SEP-24 interactive flow session: `C...` stored in `session["account"]`, transaction lookup works
- Transaction lookup filters: `stellar_account=C...` returns correct rows

---

### 4.7 Tests — skip when RPC unavailable

```python
@pytest.mark.skipif(
    not os.getenv("SOROBAN_RPC_URL"),
    reason="SOROBAN_RPC_URL not set"
)
class TestSEP45Integration:
    ...
```

Pattern from Anchor Platform [commit 1e0a79e6](https://github.com/stellar/anchor-platform/commit/1e0a79e655c64b990d17c07f5ebe289bd8812db0).

---

### 4.8 Tests — startup and health check

- SEP-45 active with missing `SOROBAN_RPC_URL` → `ImproperlyConfigured` at startup
- SEP-45 active with missing `SEP45_WEB_AUTH_CONTRACT_ID` → `ImproperlyConfigured` at startup
- SEP-45 active with missing `SEP45_JWT_SECRET` → `ImproperlyConfigured` at startup
- SEP-45 active with invalid `C...` contract ID → `ImproperlyConfigured` at startup
- SEP-45 active with `SEP45_JWT_SECRET` < 32 bytes → `ImproperlyConfigured` at startup
- RPC health check endpoint (if implemented) returns status when RPC is reachable
- RPC health check returns degraded status when RPC is unreachable

---

### 4.9 Logging

Add structured logging to all SEP-45 endpoints. **Log a nonce digest, not the raw nonce** — the raw nonce is a replay token and must not appear in logs (architecture doc Section 16, anchor-platform memo Section 8).

```python
import hashlib

def nonce_digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()[:12]

logger.info(
    "SEP45 challenge account=%s home=%s client_domain=%s nonce_id=%s",
    account, home_domain, client_domain or "-", nonce_digest(nonce)
)
logger.info(
    "SEP45 auth=ok account=%s home=%s nonce_id=%s rpc_ms=%d",
    account, home_domain, nonce_digest(nonce_value), rpc_duration_ms
)
logger.warning(
    "SEP45 auth=fail account=%s nonce_id=%s reason=%s",
    account, nonce_digest(nonce_value), error_message
)
```

Never log: signing seeds, JWT secrets, raw JWTs, full auth entries, client-domain signing keys.

---

## Checklist Summary

| Step | Description | Files touched | Risk |
|---|---|---|---|
| 1.1 | Add `"sep-45"` to accepted_seps | `settings.py` | Trivial |
| 1.2 | SEP-45 settings + startup validation | `settings.py` | Low |
| 1.3 | `WebAuthToken` base class | New `webauth/token.py`, update `sep10/token.py` | Medium |
| 1.4 | Rename auth decorator | `sep10/utils.py` | Low |
| 1.5 | Update type hints (21 files) | All SEP view + integration files | Medium (many files, mechanical) |
| 1.6 | Transaction model docstrings | `models.py` | Trivial |
| 2.1 | `WebAuthNonce` model | New `webauth/models.py` + migration | Low |
| 2.2 | Nonce cleanup command | New management command | Trivial |
| 2.3 | Client-domain helper | New `webauth/client_domain.py` | Medium (trust boundary) |
| 2.4 | GET /sep45/auth | New `sep45/views.py` | High (Soroban XDR, simulation) |
| 2.5 | POST /sep45/auth | Same file | High (validation, ENFORCE sim) |
| 2.6 | XDR encode/decode | New `sep45/xdr_utils.py` | Medium (attack surface) |
| 2.7 | Domain normalization + matching | New `sep45/utils.py` | Medium (authority comparison, not raw string) |
| 2.8 | URL routing | `urls.py`, new `sep45/urls.py` | Trivial |
| 2.9 | TOML update | `sep1/views.py` | Trivial |
| 2.10 | CORS | `cors.py` | Trivial |
| 3.1-3.2 | SEP-6 `C...` support | `sep6/deposit.py`, `sep6/withdraw.py` | Low |
| 3.3-3.4 | SEP-24 `C...` support | `sep24/deposit.py`, `sep24/withdraw.py` | Low |
| 3.5 | SEP-24 interactive flow | `sep24/utils.py` | Low (works by accident, add clarity) |
| 4.1-4.2 | Token + nonce unit tests | New test files | Low |
| 4.3 | GET endpoint tests (incl. RPC failure, domain normalization) | New test file | Medium |
| 4.4 | POST endpoint tests (incl. JWT claims, CORS, form-encoded, extra entries) | New test file | Medium |
| 4.5 | Mixed SEP-10/SEP-45 bearer + ownership isolation | New test file | Medium |
| 4.6 | Downstream SEP-6/24 with C... | Existing test files | Low |
| 4.7 | Skip-when-no-RPC guard | Test config | Trivial |
| 4.8 | Startup config validation tests | New test file | Low |
| 4.9 | Logging (nonce digest, not raw nonce) | `sep45/views.py` | Low |
