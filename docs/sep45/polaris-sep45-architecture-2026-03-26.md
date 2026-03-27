# SEP-45 Implementation Architecture — Django Polaris BPV

**Date:** 2026-03-26
**Status:** Research / Pre-implementation
**Audience:** Senior engineer, architect, technical product lead
**Spec version:** SEP-45 v0.1.1 (Draft) — [stellar-protocol/ecosystem/sep-0045.md](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md)
**Discussion:** [stellar-protocol#1620](https://github.com/stellar/stellar-protocol/discussions/1620)
**Companion research:** `../anchor-platform/sep45-research-memo.md` — Anchor Platform git history analysis

---

## 1. What SEP-45 Is (and Is Not)

SEP-45 is Stellar's web authentication protocol for **contract accounts** (`C...` addresses). It is a companion to SEP-10 — not a replacement.

| | SEP-10 | SEP-45 |
|---|---|---|
| Account types | `G...` (ed25519), `M...` (muxed) | `C...` (Soroban contract) only |
| Challenge format | Stellar transaction (ManageData ops, seq=0) | Soroban authorization entries (XDR) |
| Verification | Ed25519 signature check against on-chain signers/thresholds | Transaction simulation via Soroban RPC (`require_auth`) |
| stellar.toml key | `WEB_AUTH_ENDPOINT` | `WEB_AUTH_FOR_CONTRACTS_ENDPOINT` + `WEB_AUTH_CONTRACT_ID` |
| On-chain dependency | Horizon (optional — unfunded accounts skip threshold check) | **Soroban RPC** (mandatory — simulation is the verification) |
| Contract required | No | Yes — deployed `web_auth_verify` contract |
| Status | Active (v3.4.1) | Draft (v0.1.1) |

Services that want to support all Stellar accounts **must implement both SEPs**. The spec is explicit:
> "This SEP only supports C (contract) accounts. SEP-10 only supports G and M accounts." — [SEP-45 Simple Summary](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md)

---

## 2. Protocol Flow

```
WALLET                                              ANCHOR SERVER
  │                                                       │
  │  1. Fetch stellar.toml (SEP-1)                        │
  │     → extract WEB_AUTH_FOR_CONTRACTS_ENDPOINT          │
  │     → extract WEB_AUTH_CONTRACT_ID                     │
  │     → extract SIGNING_KEY (server G... key)            │
  │                                                       │
  │  2. GET /sep45/auth?account=C...&home_domain=x        │
  │     [&client_domain=wallet.example.com]                │
  │──────────────────────────────────────────────────────→ │
  │                                                       │
  │                        3. Validate params              │
  │                        4. Generate nonce               │
  │                        5. Build args map:              │
  │                           account, home_domain,        │
  │                           web_auth_domain,             │
  │                           web_auth_domain_account,     │
  │                           nonce,                       │
  │                           [client_domain,              │
  │                            client_domain_account]      │
  │                        6. Simulate web_auth_verify()   │
  │                           on WEB_AUTH_CONTRACT_ID      │
  │                        7. Extract auth entries          │
  │                        8. Sign server entry             │
  │                        9. Store nonce (TTL=auth_timeout)│
  │                                                       │
  │  ← 200 {authorization_entries: <XDR>, network_passphrase} │
  │←──────────────────────────────────────────────────────│
  │                                                       │
  │  10. Verify entries:                                   │
  │      - no sub-invocations                              │
  │      - contract_address == WEB_AUTH_CONTRACT_ID        │
  │      - function_name == "web_auth_verify"              │
  │      - args match expectations                         │
  │      - server signature valid                          │
  │  11. Simulate locally → verify footprint               │
  │      (only nonce read/writes allowed)                  │
  │  12. Sign client entry (contract __check_auth)         │
  │  13. Sign client_domain entry (if applicable)          │
  │                                                       │
  │  14. POST /sep45/auth                                  │
  │      {authorization_entries: <signed XDR>}             │
  │──────────────────────────────────────────────────────→ │
  │                                                       │
  │                       15. Decode XDR                    │
  │                       16. Validate invariants:          │
  │                           - contract ID match          │
  │                           - function name match        │
  │                           - args identical across      │
  │                             all entries                │
  │                           - required args present      │
  │                           - no sub-invocations         │
  │                           - signature_expiration_ledger│
  │                             not expired                │
  │                       17. Verify required auth entries: │
  │                           - server entry present       │
  │                           - client entry present       │
  │                           - client_domain entry        │
  │                             (if claimed)               │
  │                       18. Consume nonce (atomic)        │
  │                       19. Simulate tx in ENFORCE mode   │
  │                       20. Issue JWT                     │
  │                                                       │
  │  ← 200 {token: <JWT>}                                  │
  │←──────────────────────────────────────────────────────│
  │                                                       │
  │  [Bearer <JWT> in Authorization header for all         │
  │   subsequent SEP-6/12/24/31/38/58 calls]              │
```

### Critical architectural difference from SEP-10

SEP-10 verifies signatures locally — the server checks ed25519 signatures against on-chain signer lists. SEP-45 **delegates verification to the contract itself** via Soroban transaction simulation. The contract's `__check_auth` function can implement arbitrary auth logic (ed25519, passkeys, multi-sig, social recovery, etc.). The server never needs to know *how* the contract authenticates — it only needs to observe that simulation succeeds.

This means SEP-45 is fundamentally **Soroban RPC-dependent**. No RPC, no auth.

---

## 3. JWT Format

Both SEP-10 and SEP-45 produce JWTs with the same claim structure. The goal is that downstream SEPs operate on a shared authenticated identity without branching on auth mechanism at the request-handling level.

```json
{
  "iss": "https://anchor.example.com",
  "sub": "CABC...XYZ",
  "iat": 1711411200,
  "exp": 1711497600,
  "jti": "a1b2c3...",
  "client_domain": "wallet.example.com",
  "home_domain": "anchor.example.com"
}
```

**Key difference:** `sub` is always a `C...` address (never `G...`, `M...`, or `G...:memo`). SEP-45 does not provide memo or muxed-account semantics — there is no protocol-level mechanism to distinguish sub-users within a contract account the way SEP-10 does with `G...:memo` or `M...`. A contract wallet *can* represent multiple internal users or approval contexts, but that multiplexing is opaque to the anchor's auth layer.

The spec does not mandate a JWT signing algorithm. The reference implementation uses HS256. Polaris already uses HS256 for SEP-10 JWTs.

**However:** shared JWT shape does not mean zero downstream work. Polaris needs a `WebAuthToken` abstraction (Section 5), downstream SEPs need to accept `C...` in account parameters (Section 9.3), and some SEP-specific handling changes are required. The JWT wire format is compatible; the code that consumes it is not — yet.

---

## 4. Where This Lands in the Polaris Architecture

### 4.1 Current auth flow (SEP-10 only)

```
Request with Authorization: Bearer <jwt>
  → polaris/sep10/utils.py:validate_sep10_token() decorator
    → polaris/sep10/utils.py:check_auth()
      → polaris/sep10/utils.py:validate_jwt_request()
        → polaris/sep10/token.py:SEP10Token.__init__()
          → jwt.decode(token, SERVER_JWT_KEY, "HS256")
          → Validate sub: G... or M... or G...:memo
```

**Problem:** `SEP10Token.__init__()` at `polaris/sep10/token.py:56-62` calls `Keypair.from_public_key()` on the `sub` value, which will **reject** a `C...` address with `Ed25519PublicKeyInvalidError`. The token class is hard-coded to assume ed25519 accounts.

### 4.2 Files that need modification

| File | Line(s) | Change |
|---|---|---|
| `polaris/settings.py:44-61` | `accepted_seps` list | Add `"sep-45"` |
| `polaris/settings.py` | New section | Add SEP-45 settings: `SEP45_WEB_AUTH_CONTRACT_ID`, `SEP45_HOME_DOMAINS`, `SEP45_AUTH_TIMEOUT`, `SEP45_JWT_TIMEOUT`, `SOROBAN_RPC_URL` |
| `polaris/sep10/token.py:17-91` | `SEP10Token.__init__` | Must accept `C...` in `sub` claim. Extract to a shared `AuthToken` or teach `SEP10Token` about contract addresses. |
| `polaris/sep10/token.py:94-106` | `SEP10Token.account` property | Must handle `C...` prefix (no muxed decomposition, no memo) |
| `polaris/sep10/utils.py:9-63` | `check_auth`, `validate_jwt_request` | Rename or generalize — these validate JWTs from *either* auth method |
| `polaris/sep1/views.py:78-88` | `generate_toml` | Add `WEB_AUTH_FOR_CONTRACTS_ENDPOINT` and `WEB_AUTH_CONTRACT_ID` to TOML |
| `polaris/urls.py` | URL registration | Add conditional `sep-45` URL include |
| `polaris/models.py:478-497` | `Transaction.stellar_account` | Store `C...` in `stellar_account`. Update docstrings to say "web auth account" not "SEP-10 account". Consider adding `auth_mechanism` field (`sep10`/`sep45`) for auditability. |
| `polaris/middleware.py` / `polaris/cors.py` | CORS | Add SEP-45 endpoint to allowed origins |

### 4.3 New files

| File | Purpose |
|---|---|
| `polaris/webauth/__init__.py` | Shared web-auth module |
| `polaris/webauth/token.py` | `WebAuthToken` base class |
| `polaris/webauth/models.py` | `WebAuthNonce` model (DB-backed, atomic consume) |
| `polaris/webauth/client_domain.py` | Client-domain signer resolution with HTTPS enforcement |
| `polaris/sep45/__init__.py` | Module init |
| `polaris/sep45/urls.py` | URL patterns for `/sep45/auth` |
| `polaris/sep45/views.py` | `SEP45Auth` view (GET challenge, POST token exchange) |
| `polaris/tests/sep45/` | Test suite |

---

## 5. The Token Abstraction Problem

This is the most consequential design decision. Today, every SEP endpoint receives a `SEP10Token` instance:

```python
# polaris/sep6/deposit.py:53
@validate_sep10_token()
def deposit(token: SEP10Token, request: Request) -> Response:
```

All 21 integration methods across 8 SEP modules type-hint `token: SEP10Token`. Every downstream consumer accesses `token.account`, `token.muxed_account`, `token.memo`, `token.client_domain`.

### Lesson from Anchor Platform: rename before you extend

Anchor Platform's [PR #1657](https://github.com/stellar/anchor-platform/pull/1657) renamed all `sep10_account` fields to `web_auth_account` *after* SEP-45 was already merged. This is one of the clearest architectural lessons in their entire git history: SEP-10-specific names become wrong the moment SEP-45 exists. If we widen `SEP10Token` without renaming, we create the same debt they had to pay down retroactively.

### Option A: Widen `SEP10Token` to handle `C...` (minimal change)

Modify `SEP10Token.__init__` to accept `C...` addresses in `sub`. Add a `contract_account` property. Keep the class name (or alias it).

**Pros:** Zero changes to integration signatures, decorators, or downstream views.
**Cons:** The name `SEP10Token` becomes misleading. The `account` property semantics are ambiguous (`G...` vs `C...`). Anchor Platform tried this path and had to rename everything later.

### Option B: Introduce `WebAuthToken` base class first, then add SEP-45

```python
# polaris/webauth/token.py (new)
class WebAuthToken:
    """Authenticated session from SEP-10 or SEP-45."""
    account: str              # G..., C..., or derived from M...
    client_domain: str | None
    home_domain: str | None
    issued_at: datetime
    expires_at: datetime
    payload: dict
    auth_mechanism: str       # "sep10" or "sep45"

    @property
    def is_contract_account(self) -> bool:
        return self._payload["sub"].startswith("C")

# polaris/webauth/sep10.py
class SEP10Token(WebAuthToken):
    muxed_account: str | None
    memo: int | None

# polaris/webauth/sep45.py
class SEP45Token(WebAuthToken):
    pass  # no memo, no muxed — contract accounts are single-entity
```

**Pros:** Clean type semantics. Matches Anchor Platform's `WebAuthJwt` / `Sep45Jwt` hierarchy that survived production. Downstream endpoints type-hint `token: WebAuthToken` — one rename now, never again.
**Cons:** Requires updating integration base classes and decorators. Real cross-codebase refactor.

### Recommendation: Option B — generalize first

The Anchor Platform history is clear: Option A creates naming debt that must be paid later with a larger, riskier rename under production traffic. The right sequence:

1. Introduce `WebAuthToken` as the base class
2. Make `SEP10Token` a subclass (backward-compatible — it gains a parent, existing properties unchanged)
3. Rename the decorator: `validate_sep10_token()` → `validate_web_auth_token()` (alias the old name for backward compat)
4. Update integration type hints from `SEP10Token` → `WebAuthToken`
5. Add `SEP45Token` as a second subclass
6. Rename `validate_jwt_request()` → `validate_web_auth_request()` (old name aliased)

Steps 1-4 can be done as a preparatory refactor **before any SEP-45 endpoint code**. This is the pattern Anchor Platform wished they had followed — [PR #1657](https://github.com/stellar/anchor-platform/pull/1657) was a retroactive cleanup.

### JWT secret separation

Anchor Platform's [PR #1659](https://github.com/stellar/anchor-platform/pull/1659) switched SEP-45 JWT encoding to use a **dedicated secret** instead of the SEP-10 secret. Rationale: sharing secrets across auth mechanisms is unnecessary coupling and makes rotation and blast-radius management worse.

**Decision:** Use a separate `SEP45_JWT_SECRET` from day one. Do not reuse `SERVER_JWT_KEY`.

---

## 6. Soroban RPC Dependency

SEP-45 requires a Soroban RPC endpoint for:

1. **GET (challenge generation):** Simulate `web_auth_verify` to produce auth entries + get current ledger
2. **POST (token exchange):** Simulate transaction in ENFORCE mode to verify signatures

This is a new infrastructure dependency. SEP-10 only needs Horizon (and even that is optional for unfunded accounts). SEP-45 **cannot function without RPC**.

### RPC calls per auth flow

| Phase | RPC Method | Purpose |
|---|---|---|
| GET challenge | `simulateTransaction` | Generate auth entries with correct nonce structure |
| GET challenge | `getLatestLedger` | Set signature expiration (current + 10) |
| POST token | `simulateTransaction` (ENFORCE mode) | Verify client signatures |

**Minimum 3 RPC round-trips per authentication.** This is significantly more latency than SEP-10 (zero external calls for unfunded accounts, one Horizon call for funded accounts).

### Settings needed

```
SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
SEP45_WEB_AUTH_CONTRACT_ID=CALI6JC3MSNDGFRP7Z2OKUEPREHOJRRXKMJEWQDEFZPFGXALA45RAUTH
```

Pubnet contract is published: `CALI6JC3MSNDGFRP7Z2OKUEPREHOJRRXKMJEWQDEFZPFGXALA45RAUTH` ([source](https://github.com/stellar/sep45-reference/blob/3dcabc965f01512a631d2c0c6999786f5f6a01cd/contracts/web_auth/src/lib.rs)).

---

## 7. Nonce Storage

The reference implementation uses an in-memory `NonceStore` with a threading lock. This is fine for a single-process Flask server but won't work for production Django deployments (gunicorn workers, horizontal scaling).

### The TOCTOU lesson from Anchor Platform

Anchor Platform's [PR #1906](https://github.com/stellar/anchor-platform/pull/1906) fixed a **nonce TOCTOU race condition** discovered via a HackerOne report. The original implementation had separate verify/use steps — check if nonce exists and is unused, then mark it used. Under concurrent requests, two POST requests with the same nonce could both pass the verify step before either marked it used, producing two valid JWTs for one challenge.

The fix: atomic `verifyAndUse()` backed by a single SQL update predicate: `UPDATE nonce SET used=true WHERE id=? AND used=false AND expires_at > now()`. If the update affects zero rows, the nonce was already consumed or expired.

**This rules out naive cache-based approaches.** `cache.delete()` returns True if the key existed, but most cache backends don't guarantee this is atomic across distributed nodes. `cache.get()` followed by `cache.delete()` is explicitly vulnerable.

### Options

| Backend | Pros | Cons |
|---|---|---|
| Database (Django model) | Always available. Atomic via SQL predicate. Auditable. Anchor Platform's production choice. | Needs cleanup job. |
| Redis `GETDEL` or Lua script | Truly atomic single-key delete-and-return. Fast. | Requires Redis. Less auditable. |
| Django cache | Simple API. | **Not safe** — `cache.delete()` atomicity is backend-dependent and unauditable. |

### Recommendation: Database-backed nonce model

```python
# polaris/webauth/models.py
class WebAuthNonce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    nonce = models.CharField(max_length=64, db_index=True)
    account = models.CharField(max_length=56)
    used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True)
    home_domain = models.CharField(max_length=255, blank=True)
    client_domain = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("account", "nonce")]
```

```python
# Atomic consume — single UPDATE, no read-then-write
def consume_nonce(account: str, nonce: str) -> bool:
    now = timezone.now()
    updated = WebAuthNonce.objects.filter(
        account=account, nonce=nonce, used=False, expires_at__gt=now
    ).update(used=True, used_at=now)
    return updated == 1
```

This matches the pattern that survived Anchor Platform's HackerOne report. The `used` + `expires_at` predicate in a single UPDATE is race-proof at the database level.

### Nonce cleanup

Add a management command or periodic task to delete expired nonces. Anchor Platform uses a `NonceCleanupJob` ([PR #1676](https://github.com/stellar/anchor-platform/pull/1676)). For Django, a simple management command run via cron:

```python
WebAuthNonce.objects.filter(expires_at__lt=timezone.now()).delete()
```

---

## 8. Failure Modes Worth Documenting

### 8.1 Ledger race condition

The server sets `signature_expiration_ledger = latest_ledger + 10` on the challenge. If the client takes too long to sign and POST back, the ledger advances past expiration. The POST simulation fails with an opaque error.

**Mitigation:** The reference sets `+10` ledgers (~50 seconds on pubnet). This should be configurable. Document it clearly for wallet developers.

**Anchor impact:** Return a clear error ("authorization entry signature has expired — re-request the challenge"). Consider logging with the delta so operators can tune the buffer.

### 8.2 RPC unavailability

If Soroban RPC is down, SEP-45 auth is completely blocked. SEP-10 auth is unaffected.

**Mitigation:** Monitor RPC health. Consider a circuit breaker that returns 503 with `Retry-After` rather than hanging.

### 8.3 Contract archival

If the `web_auth` contract instance is archived (Soroban state archival), the simulation may fail or require a restore operation.

**Mitigation:** The spec allows the contract instance in the `read_write` footprint. Servers should periodically bump the contract's TTL. Monitor archival status.

### 8.4 XDR payload as attack surface

Anchor Platform's [PR #1900](https://github.com/stellar/anchor-platform/pull/1900) upgraded `java-stellar-sdk` because the XDR decoder had a vulnerability: it allocated memory based on a length prefix before validating the data, enabling OOM attacks. They also added a **100 KB size guard** on raw `authorization_entries` before decoding.

**Mitigation:** Add a size limit on the POST body before XDR parsing. Reject payloads over 100 KB. Treat auth-entry decoding as an attack surface, not trusted protocol data.

### 8.5 RPC auth header propagation

Anchor Platform's [PR #1904](https://github.com/stellar/anchor-platform/pull/1904) fixed a bug where configured RPC authentication headers were not actually propagated into simulation requests. Since SEP-45 is entirely RPC-dependent, broken infra auth silently breaks all SEP-45 auth.

**Mitigation:** Test authenticated RPC providers explicitly. Include RPC auth in integration tests. Add a startup health check that verifies the RPC connection actually works.

### 8.6 Client-domain HTTPS enforcement

Anchor Platform's [PR #1865](https://github.com/stellar/anchor-platform/pull/1865) restored HTTPS for client-domain signer fetching after it had been briefly downgraded to HTTP to make tests pass ([PR #1673](https://github.com/stellar/anchor-platform/pull/1673)). This is a real trust boundary — a malicious network could MITM an HTTP TOML fetch and inject a fake `SIGNING_KEY`.

**Policy:**
- HTTPS by default, always
- Fail closed on pubnet — never allow HTTP
- HTTP fallback only on testnet, gated by `STELLAR_NETWORK_PASSPHRASE` check
- Bound response size and redirect count for TOML fetching

### 8.7 Home domain normalization

Anchor Platform's [PR #1774](https://github.com/stellar/anchor-platform/pull/1774) fixed home-domain comparison because values from wallets, config, and TOML are **never normalized the same way**. Raw string comparison fails for cases like `example.com` vs `example.com:443` vs `https://example.com`.

**Mitigation:** Compare domains by authority (parsed netloc), not raw string equality. The Polaris SEP-10 code already uses `urlparse()` for this — carry the same pattern into SEP-45.

### 8.8 Sub-invocation attacks

A malicious server could craft auth entries with sub-invocations that transfer assets or modify state. The spec requires clients to check `sub_invocations == []`. The server should also reject entries with sub-invocations on POST.

Both the reference implementation and the spec are clear on this. Our implementation must enforce it on both GET and POST paths.

### 8.9 Args mismatch across entries

All entries must have identical args. The reference implementation validates this by JSON-stringifying each entry's args with sorted keys and comparing. If a client submits entries with different args (e.g., modifying the `account` in one entry), that's an attack vector.

**Implementation:** Reject any POST where args are not byte-identical across all entries.

---

## 9. Mixed SEP-10 + SEP-45 World

### 9.1 Product routing

An anchor needs to decide: does the client authenticate via SEP-10 or SEP-45?

**Answer: the account prefix determines the protocol.**

| Account prefix | Auth protocol | Endpoint |
|---|---|---|
| `G...` | SEP-10 | `/auth` |
| `M...` | SEP-10 | `/auth` |
| `C...` | SEP-45 | `/sep45/auth` |

This is unambiguous — a `C...` address **cannot** authenticate via SEP-10, and a `G...` address **cannot** authenticate via SEP-45.

### 9.2 stellar.toml changes

Current Polaris TOML generation (`polaris/sep1/views.py:78-80`):

```python
if "sep-10" in settings.ACTIVE_SEPS:
    toml_dict["WEB_AUTH_ENDPOINT"] = os.path.join(settings.HOST_URL, "auth")
    toml_dict["SIGNING_KEY"] = settings.SIGNING_KEY
```

With SEP-45 active, add:

```python
if "sep-45" in settings.ACTIVE_SEPS:
    toml_dict["WEB_AUTH_FOR_CONTRACTS_ENDPOINT"] = os.path.join(settings.HOST_URL, "sep45", "auth")
    toml_dict["WEB_AUTH_CONTRACT_ID"] = settings.SEP45_WEB_AUTH_CONTRACT_ID
```

The `SIGNING_KEY` is shared — SEP-45 uses the same server key as SEP-10 (the reference implementation reads `SECRET_SEP10_SIGNING_SEED` for both).

### 9.3 Downstream SEP handling — not as trivial as it looks

The JWT format is identical, so the `Authorization: Bearer <token>` header works mechanically. But Anchor Platform's history shows that "adding SEP-45" is **not an auth-only task**. Within days of the initial SEP-45 merge, they had to ship dedicated PRs for every downstream SEP:

- SEP-6 deposit: [PR #1629](https://github.com/stellar/anchor-platform/pull/1629)
- SEP-6 withdraw: [PR #1630](https://github.com/stellar/anchor-platform/pull/1630)
- SEP-24 deposit: [PR #1640](https://github.com/stellar/anchor-platform/pull/1640)
- SEP-24 withdraw: [PR #1649](https://github.com/stellar/anchor-platform/pull/1649)
- SEP-12 (KYC): [PR #1647](https://github.com/stellar/anchor-platform/pull/1647)
- SEP-38 (quotes): [PR #1667](https://github.com/stellar/anchor-platform/pull/1667)

**Implications for specific SEPs:**

| SEP | Impact | Needs work? |
|---|---|---|
| SEP-6 | `C...` stored in `Transaction.stellar_account`. No memo/muxed for contract accounts. SEP-6/24 specs now **explicitly allow** either SEP-10 or SEP-45 JWTs and `C...` in request params. | **Yes** — validate `C...` account format in deposit/withdraw request params |
| SEP-12 (KYC) | The authenticated identity may be a contract account. `CustomerIntegration.get()` and `.put()` receive `C...` in token.account. | **Yes** — anchor's customer integration must handle `C...` identity |
| SEP-24 (interactive) | Interactive JWT is derived from the initial auth token. But SEP-24 interactive helpers (`polaris/sep24/utils.py`) are tightly shaped around SEP-10 JWT structure and classic-account fields. | **Yes** — session/interactive flow needs review |
| SEP-31 | Current SEP-31 spec still frames auth in SEP-10 terms. Anchor Platform added contract-account support ([PR #1723](https://github.com/stellar/anchor-platform/pull/1723)) but this has less spec clarity than SEP-6/24/38. | **Defer** unless concrete interop need |
| SEP-38 (quotes) | SEP-38 explicitly added SEP-45 support. Quote/pricing APIs must accept either bearer token. | **Yes** — minor, mostly token type acceptance |
| SEP-58 | Already uses `@validate_sep10_token()`. Once renamed to `@validate_web_auth_token()`, works. | **Trivial** |

### 9.3.1 Product constraint: one transaction per contract account

Anchor Platform's [3.2.0-beta.1 release](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1) surfaced that because memos are not part of SEP-45 semantics, **SEP-6 and SEP-24 withdrawals were limited to one ongoing transaction per contract account**. Classic accounts use memo to multiplex concurrent transactions; contract accounts cannot.

This is a concrete product constraint that leaks directly from protocol identity semantics. Decide early whether to accept it, redesign around it (e.g., use transaction UUID as the disambiguator), or document it as a known limitation.

### 9.4 Self-serve vs admin-assisted vs hybrid

**SEP-45 should be self-serve.** The auth flow is programmatic — wallet signs, server verifies. No human in the loop. The only question is whether contract accounts get different KYC treatment.

Recommendation: treat `C...` accounts the same as `G...` accounts for KYC purposes. The anchor's `CustomerIntegration` can inspect the account prefix if it needs to apply different rules (e.g., requiring additional identity verification for smart contract wallets).

**The auth flow itself requires no admin intervention.** If the wallet can sign, it can authenticate.

---

## 10. Implementation Surface Area

### 10.1 New code

| Component | Estimated LOC | Complexity |
|---|---|---|
| **Phase 1: Preparatory refactor** | | |
| `polaris/webauth/token.py` (base class + aliases) | ~80 | Medium — wide blast radius but mechanically simple |
| Decorator rename + integration type hints | ~50 (across files) | Low per file, many files |
| Settings + config validation | ~60 | Low |
| Transaction model docstring/field updates | ~20 | Low |
| **Phase 2: SEP-45 core** | | |
| `polaris/webauth/models.py` (nonce model) | ~40 | Low |
| `polaris/webauth/client_domain.py` | ~50 | Medium — trust boundary |
| `polaris/sep45/views.py` (GET + POST) | ~300 | High — Soroban XDR handling, simulation, size guard |
| `polaris/sep45/urls.py` | ~5 | Trivial |
| TOML generation + CORS + URL wiring | ~20 | Trivial |
| Nonce cleanup management command | ~15 | Trivial |
| **Phase 3-4: Downstream + tests** | | |
| SEP-6/24/12/38 downstream updates | ~100 (across files) | Medium — contract account identity handling |
| Tests (unit + integration) | ~400 | Medium — need RPC mocking, concurrent nonce tests, XDR fuzzing |

### 10.2 Dependencies

The reference implementation uses:
- `stellar_sdk` (already in Polaris) — `SorobanServer`, `TransactionBuilder`, `auth`, `scval`, `xdr`
- `xdrlib3` (may already be a transitive dep of `stellar_sdk`)

**Verify:** Does `polaris`'s current `stellar-sdk` version support `SorobanServer`, `AuthMode.ENFORCE`, and `SorobanAuthorizationEntry` XDR types? Check `pyproject.toml`.

The reference uses `stellar_sdk.soroban_rpc.AuthMode` which was added in stellar-sdk 10.x. If Polaris is on an older version, this is a required upgrade.

### 10.3 Configuration delta

New `.env` / Django settings:

```ini
# Required when sep-45 is active
SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
SEP45_WEB_AUTH_CONTRACT_ID=CALI6JC...RAUTH
SEP45_JWT_SECRET=<separate from SERVER_JWT_KEY>

# Optional (defaults shown)
SEP45_HOME_DOMAINS=                # Falls back to SEP10_HOME_DOMAINS
SEP45_WEB_AUTH_DOMAIN=             # Falls back to HOST_URL netloc
SEP45_AUTH_TIMEOUT=900             # Nonce TTL in seconds
SEP45_JWT_TIMEOUT=86400            # JWT expiration in seconds
```

**Removed:** `SEP45_SOURCE_SIGNING_SEED` — Anchor Platform's [PR #1697](https://github.com/stellar/anchor-platform/pull/1697) removed this config after discovering it was unnecessary. Use a random account as the simulation source instead of a dedicated signing seed.

**Added:** `SEP45_JWT_SECRET` — separate from `SERVER_JWT_KEY` per [PR #1659](https://github.com/stellar/anchor-platform/pull/1659). Sharing secrets across auth mechanisms is unnecessary coupling and makes rotation worse.

### 10.4 Config validation — ship it first

Anchor Platform's [PR #1639](https://github.com/stellar/anchor-platform/pull/1639) added config validation *after* the core feature was already merged. The anchor-platform memo observes this is a predictable maturity pattern and recommends inverting the sequence.

**Ship hard validation first.** Before any endpoint code, validate at startup:
- `SOROBAN_RPC_URL` is present and reachable
- `SEP45_WEB_AUTH_CONTRACT_ID` is a valid `C...` address
- `SEP45_JWT_SECRET` is present and at least 32 bytes
- `SEP45_HOME_DOMAINS` are valid hostnames (not URLs)
- `SEP45_AUTH_TIMEOUT` and `SEP45_JWT_TIMEOUT` are positive integers
- `SIGNING_SEED` is present (shared with SEP-10)

---

## 11. Open Questions in the Spec

These are real ambiguities or gaps. An implementation must make decisions here.

### 11.1 Sub-signer authorization entries

If a contract account's `__check_auth` requires authorization from sub-signers (e.g., a 2-of-3 multi-sig contract), those signers may need their own auth entries. The spec only describes entries for: client account, server account, optional client domain account.

**Source:** Leigh McCulloch's review comment in [stellar-protocol#1620](https://github.com/stellar/stellar-protocol/discussions/1620).

**Impact:** Unknown. If a contract's `__check_auth` calls `require_auth` on sub-signers, the simulation at GET time should produce those extra entries. But the spec's validation rules (args identical across all entries) might not hold for sub-signer entries that have different credential addresses.

**Decision needed:** Accept additional entries beyond the three specified types, or reject them?

### 11.2 No structured error codes

The spec provides example error strings but no code taxonomy. Clients can't programmatically distinguish "expired nonce" from "invalid contract ID."

**Recommendation:** Use the error strings from the reference implementation (they're specific enough). Optionally add an `error_code` field for programmatic consumption.

### 11.3 No `aud` claim in JWT

Unlike OIDC, the JWT has no `aud` (audience) claim. In theory, a JWT issued by anchor A could be accepted by anchor B if they share signing material.

**Mitigation:** This design uses separate secrets — `SERVER_JWT_KEY` for SEP-10, `SEP45_JWT_SECRET` for SEP-45 (Section 10.3). Each anchor deploys unique values for both. The `iss` claim identifies the issuing anchor. Cross-anchor token replay requires compromising the target anchor's secret, which is an operational security concern independent of the `aud` claim. Not a practical risk for correctly configured deployments, but worth noting if token rotation or multi-tenant deployments become relevant.

### 11.4 JWT algorithm not mandated

The spec says "follow IETF JWT BCP" but doesn't require a specific algorithm. Polaris uses HS256 everywhere. No change needed, but worth noting for interop discussions.

---

## 12. What to Steal

### From the reference implementation (`/sep45-reference/python-sep45/sep45_server.py`)

1. **XDR validation is strict** — `unpacker.get_position() == len(raw)` prevents truncation attacks
2. **Args comparison is JSON-stringified with sorted keys** — byte-level comparison
3. **Nonce is keyed by `(account, nonce)` tuple** — prevents cross-account nonce reuse
4. **Signature expiration ledger is checked against live ledger** — not against a cached value
5. **Content-Type fallback** — tries JSON body first, then form data (spec requires both)
6. **Hostname validation** — `urlparse(f"https://{value}").netloc != value` prevents URL injection
7. **Wildcard domain matching** — `*.example.com` patterns for multi-tenant anchors

### From Anchor Platform's git history (the hard-won lessons)

8. **Generalize names before extending** — rename `sep10_*` → `web_auth_*` as Phase 1, not as cleanup ([PR #1657](https://github.com/stellar/anchor-platform/pull/1657))
9. **Separate JWT secrets** — one per auth mechanism, not shared ([PR #1659](https://github.com/stellar/anchor-platform/pull/1659))
10. **Atomic nonce consume** — single SQL `UPDATE ... WHERE used=false AND expires_at > now()` ([PR #1906](https://github.com/stellar/anchor-platform/pull/1906), HackerOne report)
11. **100KB size guard on auth entries** — before XDR parsing, not after ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900))
12. **HTTPS-only client-domain fetch on pubnet** — HTTP fallback gated by network passphrase ([PR #1865](https://github.com/stellar/anchor-platform/pull/1865))
13. **Test RPC auth propagation explicitly** — authenticated RPC providers silently break if headers aren't forwarded ([PR #1904](https://github.com/stellar/anchor-platform/pull/1904))
14. **Ship config validation before feature code** — validate all required settings at startup ([PR #1639](https://github.com/stellar/anchor-platform/pull/1639))
15. **Don't invent config you don't need** — `SEP45_SIMULATING_SIGNING_SEED` was removed as unnecessary ([PR #1697](https://github.com/stellar/anchor-platform/pull/1697))
16. **Domain comparison by authority, not raw string** — wallets, config, and TOML normalize differently ([PR #1774](https://github.com/stellar/anchor-platform/pull/1774))
17. **Request-shape validation is separate from protocol validation** — malformed requests should return 400, not 500 ([PR #1870](https://github.com/stellar/anchor-platform/pull/1870))
18. **Use SDK types, not local wrappers** — avoid inventing `SorobanAuthorizationEntryList` when the SDK has `SorobanAuthorizationEntries` ([commit eb383978](https://github.com/stellar/anchor-platform/commit/eb3839787dcae5aa2b7c55cc54714cf2894801d5))

### What NOT to copy from Anchor Platform

- Spring-style bean wiring and deep dependency injection hierarchies
- The initial merge-then-harden sequence — invert it
- Shared JWT secret between SEP-10 and SEP-45 (they fixed this in PR #1659)
- The SEP-10-specific naming they had to rename later

---

## 13. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Spec is Draft (v0.1.1) — may change before Active | **High** | Build behind feature flag (`"sep-45" in ACTIVE_SEPS`). Isolate in `polaris/sep45/` module. |
| Contract interface still moving — arg names changed mid-impl ([PR #1702](https://github.com/stellar/anchor-platform/pull/1702)) | **High** | Pin to published contract ID. Expect versioning. |
| Soroban RPC dependency adds failure mode | **Medium** | Circuit breaker. Health check. Test authenticated RPC providers. ([PR #1904](https://github.com/stellar/anchor-platform/pull/1904)) |
| `stellar-sdk` version may lack Soroban features / have XDR vulnerabilities | **Medium** | Verify before implementation. Upgrade if needed. ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900)) |
| `WebAuthToken` refactor has wide blast radius | **Medium** | Do it as Phase 1 before any SEP-45 code. Alias old names. |
| Downstream SEPs need more work than expected | **Medium** | Budget for SEP-6/24/12/38 follow-up PRs. Anchor Platform needed 6 dedicated PRs within days. |
| One-transaction-per-contract-account limit | **Low** | Document as known limitation or redesign transaction disambiguation. |
| Contract archival can break auth | **Low** | TTL monitoring + automatic restore. |

---

## 14. Implementation Sequence

Sequence based on Anchor Platform's evolution — generalize first, then extend:

**Phase 1: Preparatory refactor (no SEP-45 endpoints yet)**

1. **Settings + config validation** — Add `"sep-45"` to `accepted_seps`, add all new settings with strict startup validation. Ship validation before any feature code. ([Lesson: PR #1639](https://github.com/stellar/anchor-platform/pull/1639))
2. **`WebAuthToken` base class** — Introduce `polaris/webauth/token.py` with `WebAuthToken`. Make `SEP10Token` a subclass. Alias old names for backward compat.
3. **Rename decorator** — `validate_sep10_token()` → `validate_web_auth_token()` (old name aliased). Update integration type hints `SEP10Token` → `WebAuthToken`. ([Lesson: PR #1657](https://github.com/stellar/anchor-platform/pull/1657))
4. **Transaction model** — Update docstrings from "SEP-10 account" to "web auth account". Consider adding `auth_mechanism` field via migration.

**Phase 2: SEP-45 core (isolated in `polaris/sep45/`)**

5. **Nonce model** — `WebAuthNonce` Django model with atomic consume via single UPDATE predicate. Add cleanup management command. ([Lesson: PR #1906 TOCTOU fix](https://github.com/stellar/anchor-platform/pull/1906))
6. **GET endpoint** — Challenge generation (simulate `web_auth_verify`, sign server entry, return XDR). Include 100KB size guard on input, domain normalization.
7. **POST endpoint** — Token exchange (validate entries, consume nonce atomically, simulate ENFORCE, issue JWT with `SEP45_JWT_SECRET`)
8. **Client-domain helper** — HTTPS-only on pubnet, HTTP fallback only on testnet. Bound response size and redirects. ([Lesson: PR #1865](https://github.com/stellar/anchor-platform/pull/1865))
9. **TOML + CORS + URL wiring** — Register `/sep45/auth`, add TOML fields, add to CORS

**Phase 3: Downstream SEP updates**

10. **SEP-6** — Accept `C...` in deposit/withdraw params, handle no-memo identity
11. **SEP-24** — Review interactive flow session helpers for contract account compatibility
12. **SEP-12 / SEP-38 / SEP-58** — Accept `WebAuthToken` (likely works after Phase 1 rename)
13. **SEP-31** — Defer unless concrete interop need

**Phase 4: Hardening**

14. **Tests** — Malformed XDR payloads, concurrent nonce replay, HTTPS client-domain fetch, RPC auth propagation, mixed SEP-10/SEP-45 bearer tokens on same endpoints
15. **Operational** — RPC health check, nonce cleanup cron, contract TTL monitoring

Phase 1 is the highest-risk work (cross-cutting rename) but creates the cleanest foundation. Phase 2 is isolated. Phase 3 is where Anchor Platform burned the most follow-up PRs.

---

## 15. What to Test Aggressively

From Anchor Platform's post-release fix history, the real bugs live in trust boundaries and edge cases, not the happy path. Prioritize:

1. **Malformed and oversized `authorization_entries`** — XDR decoding as attack surface ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900))
2. **Duplicate nonce submissions under concurrency** — race the POST endpoint with the same nonce from two threads ([PR #1906](https://github.com/stellar/anchor-platform/pull/1906))
3. **Client-domain signer fetch over HTTPS** — verify HTTPS enforcement, test HTTP rejection on pubnet ([PR #1865](https://github.com/stellar/anchor-platform/pull/1865))
4. **Client-domain missing signature** — valid challenge, valid client signature, missing client-domain signature
5. **Home-domain normalization** — `example.com` vs `example.com:443` vs `https://example.com` ([PR #1774](https://github.com/stellar/anchor-platform/pull/1774))
6. **RPC auth-header propagation** — test with an authenticated RPC provider, verify headers actually arrive ([PR #1904](https://github.com/stellar/anchor-platform/pull/1904))
7. **Downstream transaction/quote ownership with `C...` identity** — create transaction with SEP-45 token, query with SEP-10 token for same underlying user, verify isolation
8. **Mixed SEP-10 and SEP-45 bearer-token handling** — same API surface, different token types
9. **Request-shape validation** — missing `account`, non-`C...` account, missing `home_domain`, empty `authorization_entries` ([PR #1870](https://github.com/stellar/anchor-platform/pull/1870))
10. **Expired signature ledger** — simulate slow client signing
11. **RPC unavailability** — verify 503, not 500 or hang
12. **Config validation** — missing settings fail at startup, not at first request

Skip integration tests when `SOROBAN_RPC_URL` is not set (Anchor Platform had to do this: [commit 1e0a79e6](https://github.com/stellar/anchor-platform/commit/1e0a79e655c64b990d17c07f5ebe289bd8812db0)).

---

## 16. Logging Policy

### Never log

- Signing seeds
- JWT secrets
- Raw JWTs
- Full raw authorization entries
- Client-domain signing keys

### Always log (one line per event, grep-friendly)

- Account identifier (`C...`)
- Auth mechanism (`sep10` / `sep45`)
- Home domain
- Client domain (if present)
- Nonce ID or digest
- Simulation success/failure
- Rejection reason (specific — "expired nonce", not "auth failed")
- RPC provider host
- Request correlation ID

Pattern: `SEP45 auth=ok account=CABC...XYZ home=example.com client_domain=wallet.example.com nonce=a1b2... rpc_ms=340`

---

## 17. Contract Deployment

The published pubnet contract exists at `CALI6JC3MSNDGFRP7Z2OKUEPREHOJRRXKMJEWQDEFZPFGXALA45RAUTH`.

Source code: [contracts/web_auth/src/lib.rs](https://github.com/stellar/sep45-reference/blob/3dcabc965f01512a631d2c0c6999786f5f6a01cd/contracts/web_auth/src/lib.rs)

The contract implements a single function that takes a **`Map<Symbol, String>` args parameter** — not positional arguments:

```rust
pub fn web_auth_verify(env: Env, args: Map<Symbol, String>) -> Result<(), WebAuthError> {
    // Extract "account" from args map → require_auth()
    // Extract "web_auth_domain_account" from args map → require_auth()
    // Extract "client_domain_account" from args map (optional) → require_auth()
}
```

The args map contains keys: `account`, `home_domain`, `web_auth_domain`, `web_auth_domain_account`, `nonce`, and optionally `client_domain` + `client_domain_account`. The contract extracts address values by symbol key and calls `require_auth()` on each — the Soroban runtime handles signature verification by invoking each contract's `__check_auth`.

This map-based interface is what the server must construct during challenge generation (Section 2, step 5) and what the server validates on POST (all entries must have identical args maps). Earlier versions of the contract used positional parameters ([PR #1702](https://github.com/stellar/anchor-platform/pull/1702) tracked a renaming); the current canonical interface is the map form.

**For testnet:** The same contract must be deployed. The reference repo includes build instructions. Wasm hash: `3c8d0b8b347752e57abe0b50380401ca8f5793bc971b685fd072571bbf5d54cc`.

---

## 18. References

### Specifications
- [SEP-45 Specification](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md) — v0.1.1, Draft
- [SEP-45 Discussion](https://github.com/stellar/stellar-protocol/discussions/1620) — 17 comments, including Leigh McCulloch's review
- [SEP-10 Specification](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md) — v3.4.1, Active
- [IETF JWT BCP](https://tools.ietf.org/wg/oauth/draft-ietf-oauth-jwt-bcp/) — Referenced by SEP-45

### Reference implementations
- [sep45-reference repo](https://github.com/stellar/sep45-reference) — Python, TypeScript, Rust implementations
- Local reference: `/sep45-reference/python-sep45/sep45_server.py` — ~530 LOC Python implementation
- Local spec copy: `/docs/sep-45.rst` — Full spec in RST format

### Anchor Platform (Java/Kotlin) — git history as lessons
- [PR #1623: Initial SEP-45 implementation](https://github.com/stellar/anchor-platform/pull/1623) — first merge, not production-hard
- [PR #1639: Config validation](https://github.com/stellar/anchor-platform/pull/1639) — shipped after feature, should have been first
- [PR #1657: Rename sep10_account → web_auth_account](https://github.com/stellar/anchor-platform/pull/1657) — the naming debt lesson
- [PR #1659: Separate SEP-45 JWT secret](https://github.com/stellar/anchor-platform/pull/1659) — blast-radius management
- [PR #1676: Nonce persistence](https://github.com/stellar/anchor-platform/pull/1676) — database-backed replay protection
- [PR #1697: Remove unnecessary signing seed config](https://github.com/stellar/anchor-platform/pull/1697) — don't invent config
- [PR #1702: Contract arg name changes](https://github.com/stellar/anchor-platform/pull/1702) — spec still moving
- [PR #1774: Home domain normalization](https://github.com/stellar/anchor-platform/pull/1774) — raw string comparison fails
- [PR #1865: HTTPS client-domain fetch](https://github.com/stellar/anchor-platform/pull/1865) — real trust boundary
- [PR #1870: Request validation hardening](https://github.com/stellar/anchor-platform/pull/1870) — malformed requests → 500
- [PR #1900: XDR decoder OOM fix + 100KB guard](https://github.com/stellar/anchor-platform/pull/1900) — auth entries as attack surface
- [PR #1904: RPC auth header propagation](https://github.com/stellar/anchor-platform/pull/1904) — silent infra auth failure
- [PR #1906: Nonce TOCTOU race condition](https://github.com/stellar/anchor-platform/pull/1906) — HackerOne report, atomic consume
- [3.2.0-beta.1 release notes](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1) — one-tx-per-contract-account limitation
- Companion research: `../anchor-platform/sep45-research-memo.md`
