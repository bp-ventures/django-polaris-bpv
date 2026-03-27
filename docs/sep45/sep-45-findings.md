# SEP-45 Findings

## Scope

This memo summarizes SEP-45 for a team building primarily in Django Polaris / Python, using the Stellar Anchor Platform Java/Kotlin implementation as a reference model.

It focuses on:

1. What SEP-45 is and the problem it solves
2. How it fits into the broader Stellar SEP ecosystem
3. How Anchor Platform implemented it
4. What changed over time in the protocol and reference implementations
5. What architectural and security lessons matter for a Python implementation

## Executive Summary

SEP-45 is the contract-account counterpart to SEP-10. It solves web authentication for `C...` accounts by replacing classic transaction-signature proof with Soroban authorization entries plus simulation against a `web_auth_verify` contract.

The Java/Kotlin Anchor Platform is a useful reference for service boundaries, config, persistence, and integration testing. It is not a complete substitute for the current SEP-45 draft, because some validation in Anchor Platform is lighter than the current draft and lighter than the local Python reference implementation.

The main implementation lesson is simple: treat SEP-45 as its own protocol surface, not as a small SEP-10 variation. The risk is in structural validation, nonce lifecycle, JWT semantics, and replay resistance.

## What SEP-45 Is

- SEP-45 is currently a `Draft` SEP, version `0.1.1`, updated `2025-12-16`, titled "Stellar Web Authentication for Contract Accounts".
- It exists because SEP-10 only authenticates `G...` and `M...` accounts. Contract accounts (`C...`) authenticate through Soroban auth, not standard Stellar account signatures.
- Discovery still uses SEP-1. The client reads `stellar.toml` and finds:
  - `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`
  - `WEB_AUTH_CONTRACT_ID`
  - `SIGNING_KEY`
- The flow is:
  1. Client requests a challenge
  2. Server returns `SorobanAuthorizationEntries`
  3. Client verifies structure and verifies the server-signed entry
  4. Client signs the contract-account auth entry
  5. Client simulates to ensure no unintended side effects
  6. Client posts signed entries back
  7. Server validates and simulates
  8. Server issues a JWT

Conceptually, SEP-45 reuses the web-session model of SEP-10, but the proof of control shifts from signing a challenge transaction to satisfying Soroban authorization rules for a contract account.

## What Problem It Solves

Without SEP-45, an anchor can authenticate classic Stellar accounts but has no standard way to authenticate a Soroban contract account for web APIs such as:

- SEP-12 customer/KYC flows
- SEP-24 interactive flows
- SEP-6 deposit and withdrawal flows

SEP-45 provides a standard session-establishment mechanism for those contract accounts.

## Position In The SEP Ecosystem

SEP-45 is complementary to SEP-10, not a replacement.

- SEP-1 provides discovery.
- SEP-10 remains the standard for classic accounts.
- SEP-45 covers contract accounts.
- SEP-12 can consume the resulting authenticated session.
- SEP-24 and SEP-6 use the resulting authenticated session for deposit and withdrawal workflows.

One practical product implication surfaced in Anchor Platform's release history: early contract-account support limited some withdrawal correlation because memo-based account multiplexing was not available in the same way. That is a product and state-model concern, not just an auth concern.

## Key Protocol Mechanics

### Discovery

The client reads `stellar.toml` and learns:

- the auth endpoint
- the verifier contract ID
- the server signing key

### Challenge Construction

The server simulates a call to `web_auth_verify` and obtains authorization entries. It signs the server-owned auth entry and returns the serialized entries to the client.

### Client Verification

The draft requires the client to verify at least:

- no sub-invocations
- correct contract ID
- function name is `web_auth_verify`
- argument consistency across all entries
- presence and validity of the server auth entry
- optional client-domain entry if requested
- simulated ledger footprint is limited to nonce-related effects plus archived-contract restoration when needed

### Server Verification

The server must validate the returned entries and simulate again in enforcement mode before issuing a JWT.

### Optional Client-Domain Verification

If `client_domain` is present, the server may fetch the client domain's `SIGNING_KEY` from SEP-1 and require an additional auth entry. This lets the anchor attribute a session not only to an account, but also to wallet software.

## Local Reference Implementation In This Repo

The repo contains a local SEP-45 draft copy in [docs/sep-45.rst](/home/antb2/dev/rsync/django-polaris-bpv/docs/sep-45.rst) and a standalone reference repository under [sep45-reference/](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference).

Important local reference pieces:

- Python service:
  [sep45-reference/python-sep45/sep45_server.py](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/python-sep45/sep45_server.py)
- Python tests:
  [sep45-reference/python-sep45/test_sep45_server.py](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/python-sep45/test_sep45_server.py)
- Reference contracts:
  [sep45-reference/contracts/web_auth/src/lib.rs](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/contracts/web_auth/src/lib.rs)
  [sep45-reference/contracts/account/src/lib.rs](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/contracts/account/src/lib.rs)
- Earlier TypeScript prototype:
  [sep45-reference/server/challenge.ts](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/server/challenge.ts)

### What The Local Python Reference Gets Right

The local Python service is useful because it explicitly validates more protocol invariants before simulation than the earlier TypeScript prototype. In particular it checks:

- contract account format
- allowed home domains
- `web_auth_domain`
- `web_auth_domain_account`
- consistent argument maps across entries
- required argument presence
- required auth-entry presence
- sub-invocation rejection
- expired `signature_expiration_ledger`
- nonce one-time use
- simulation after structural validation

That is closer to what a production Python implementation should do.

### What The Reference Contracts Actually Enforce

Both the local reference contract and the Anchor Platform contract are intentionally small. The `web_auth_verify` contract mostly does this:

- `account.require_auth()`
- `web_auth_domain_account.require_auth()`
- optional `client_domain_account.require_auth()`

This is important architecturally: many semantic checks live in the service, not in the contract. The contract proves required parties authorized the invocation. The web service still has to validate meaning.

## How Anchor Platform Implemented SEP-45

Anchor Platform added a dedicated SEP-45 surface instead of burying it inside SEP-10 code paths.

Key files in upstream Anchor Platform:

- Core service:
  `core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java`
- Controller:
  `platform/src/main/java/org/stellar/anchor/platform/controller/sep/Sep45Controller.java`
- Config interface:
  `core/src/main/java/org/stellar/anchor/config/Sep45Config.java`
- Config implementation:
  `platform/src/main/java/org/stellar/anchor/platform/config/PropertySep45Config.java`
- JWT type:
  `core/src/main/java/org/stellar/anchor/auth/Sep45Jwt.java`
- Client helper:
  `lib-util/src/main/kotlin/org/stellar/anchor/client/Sep45Client.kt`
- Integration tests:
  `essential-tests/src/testFixtures/kotlin/org/stellar/anchor/platform/integrationtest/Sep45Tests.kt`
- DB migration:
  `platform/src/main/resources/db/migration/V22__web_auth_account.sql`

### Anchor Platform Architectural Shape

The architecture is clean and worth copying:

- Thin HTTP controller
- Core service with protocol logic
- Dedicated config object
- Separate nonce persistence
- Dedicated JWT type
- Dedicated client and integration tests

### Strong Design Choices In Anchor Platform

- SEP-45 was given its own service and controller
- nonces are persisted in the database
- transaction/account storage was generalized from SEP-10-centric naming to `web_auth_account`
- integration tests exercise challenge, signing, validation, replay failure, and client-domain signing
- config validation became explicit rather than implicit

### Gaps Relative To The Current Draft

Anchor Platform leans heavily on simulation and does not visibly enforce every current draft invariant in code as strictly as the local Python reference does.

Compared with the draft and local Python reference, the Java implementation appears lighter on:

- explicit contract-ID validation on every entry
- explicit function-name checks on every entry
- explicit sub-invocation rejection
- explicit required-entry presence checks beyond server-signature handling
- explicit signature-expiration-ledger validation

Simulation is necessary but not sufficient. A Python implementation should keep both:

- structural validation before simulation
- simulation as final authorization proof

### A Concrete Semantics Risk

In the current upstream `Sep45Service`, the JWT is created with `iss = homeDomain`, not `web_auth_domain`.

That is only safe when both values are identical. The draft distinguishes them. A Python implementation should not collapse those two fields casually.

## Evolution Over Time

### Anchor Platform Timeline

- `2025-01-16`: PR [#1620](https://github.com/stellar/anchor-platform/pull/1620) added SEP-45 contracts.
- `2025-02-06`: PR [#1623](https://github.com/stellar/anchor-platform/pull/1623) implemented SEP-45 core logic.
  - The PR explicitly noted config was still rough.
  - The PR also noted client-domain verification had not yet been tested.
- `2025-02-24`: PR [#1639](https://github.com/stellar/anchor-platform/pull/1639) added SEP-45 config validation.
- `2025-04-23`: release [`3.2.0-beta.1`](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1) introduced first beta contract-account support in SEP-45, SEP-24, and SEP-6.
- `2025-09-04`: release [`4.0.0`](https://github.com/stellar/anchor-platform/releases/tag/4.0.0) shipped broader contract-account support.
- `2025-12-11`: PR [#1870](https://github.com/stellar/anchor-platform/pull/1870) added request validation so malformed inputs return `400` instead of falling through to `500`.
- `2026-03-05`: PR [#1900](https://github.com/stellar/anchor-platform/pull/1900) upgraded `java-stellar-sdk` to fix XDR decode OOM risk and added a 100 KB input-size guard.
- `2026-03-18`: PR [#1906](https://github.com/stellar/anchor-platform/pull/1906) replaced separate nonce verify/use calls with atomic `verifyAndUse()` after a TOCTOU report.
- `2026-03-24`: release [`4.2.0`](https://github.com/stellar/anchor-platform/releases/tag/4.2.0) shipped the nonce race fix.

### What These Changes Mean

The change history is informative:

- first implementation came before full hardening
- config correctness had to be added after core logic
- malformed request handling had to be added later
- parser/OOM hardening had to be added later
- nonce race safety had to be added later

That is a clear signal for a Python implementation: input size limits, strict validation, and atomic nonce consumption are not polish. They are part of the protocol boundary.

### SEP-45 Reference Repository Timeline

The standalone `sep45-reference` repo evolved from prototype to something intended for verifiable deployment:

- initial import into the standalone repo
- PR [#3](https://github.com/stellar/sep45-reference/pull/3): added contract build verification workflow for SEP-55-style source verification
- PR [#4](https://github.com/stellar/sep45-reference/pull/4): removed upgradeability so the verifier contract becomes immutable
- PR [#5](https://github.com/stellar/sep45-reference/pull/5): updated build attestation workflow
- release [`v0.1.3`](https://github.com/stellar/sep45-reference/releases/tag/v0.1.3) published verified Wasm on `2026-01-14`

This matters because a server depending on a deployed verifier contract needs:

- stable source
- verifiable build
- immutable deployed behavior

### SEP-45 Draft Evolution

The local draft notes a `0.1.1` change:

- stellar-protocol PR [#1827](https://github.com/stellar/stellar-protocol/pull/1827) added handling for archived SEP-45 contract instances

That changed the expected client-side footprint verification rules. A client must allow the verifier contract instance restoration footprint when the contract instance is archived.

## Security Findings

### 1. Nonce Consumption Must Be Atomic

Anchor Platform originally had separate verify and use steps. That was later fixed with a single atomic update query.

This is the clearest production lesson from the history.

For Django/Polaris:

- use durable storage
- make validation and consumption one atomic operation
- do not use a process-local in-memory nonce store in production

### 2. Size Limits Must Exist Before Decode

Anchor Platform added a 100 KB pre-decode size cap after an OOM issue related to XDR decoding.

For Python:

- cap request body size
- cap `authorization_entries` length before base64 decode
- reject obviously oversized inputs before parser allocation

### 3. Structural Validation Must Precede Simulation

The service should reject malformed or semantically inconsistent entries before calling RPC simulation.

Do not rely on simulation alone to infer:

- field consistency
- domain semantics
- function identity
- absence of sub-invocations
- presence of expected auth actors

### 4. JWT Claims Need Careful Semantics

At minimum:

- `iss` should reflect the correct auth domain semantics
- `sub` should be the `C...` account
- `exp` should be short enough to reflect signer-set drift risk
- optional `client_domain` should only be included when actually verified

### 5. The Verifier Contract Should Be Immutable Or Official

If the validator contract can change in place, the security model shifts under the service.

That is why the standalone SEP-45 reference contract moved toward immutability and source-verifiable release artifacts.

## State-Management Findings

### Nonces

Nonce state is protocol state.

Required properties:

- unique
- expiring
- one-time use
- atomically consumed

### Authenticated Account Identity

Anchor Platform renamed stored fields from `sep10_account` to `web_auth_account`.

That is a good design choice. It prevents classic-account assumptions from leaking into contract-account workflows.

### Transaction Correlation

Contract-account flows may not have the same memo-based correlation patterns as classic account flows. Product and transaction state machines need to account for that explicitly.

## Testing Findings

A Python implementation should have three test layers.

### 1. Unit Tests

Focus on:

- argument extraction
- domain validation
- required field checks
- malformed XDR rejection
- oversized payload rejection
- nonce replay rejection
- signature-expiration-ledger checks

### 2. Service/API Tests

Focus on:

- `GET /sep45/auth`
- `POST /sep45/auth`
- JSON and form-encoded POST variants
- error-code correctness
- JWT claims

### 3. Integration Tests

Use real Soroban RPC and real contracts when possible.

At minimum test:

- challenge generation
- client signing
- successful validation
- missing client signature
- tampered server signature
- client-domain verification
- replay failure

Anchor Platform's end-to-end tests are a good model here.

## Product / Architecture Guidance For Django Polaris

### Recommended Architecture

Keep SEP-45 isolated:

- `sep45/service.py`
- `sep45/views.py`
- `sep45/config.py`
- `sep45/jwt.py`
- `sep45/nonces.py`
- `sep45/tests/...`

Do not bury this inside SEP-10 modules.

### Recommended Validation Order

One obvious control path:

1. Parse request
2. Enforce request-size limits
3. Decode XDR
4. Validate structure and required invariants
5. Atomically verify-and-consume nonce
6. Simulate with enforcement
7. Issue JWT

### Recommended Operational Defaults

- short JWT lifetimes
- durable nonce store
- RPC timeouts and failure logging
- concise structured logs for challenge issuance, validation failure, and JWT issuance

### Recommended Data Model Choices

- store authenticated account as `web_auth_account`, not `sep10_account`
- store optional `client_domain`
- store nonce expiry separately from used-state

## Practical Recommendations

If implementing SEP-45 in Django Polaris / Python:

1. Use the SEP draft and the local Python reference as the main validation reference.
2. Use Anchor Platform as the architectural reference for service boundaries, persistence, and integration tests.
3. Treat atomic nonce consumption as mandatory.
4. Add pre-decode payload limits from day one.
5. Validate draft invariants explicitly before simulation.
6. Keep `home_domain` and `web_auth_domain` semantics distinct.
7. Prefer immutable or official verifier contracts.

## Source References

Local sources:

- [docs/sep-45.rst](/home/antb2/dev/rsync/django-polaris-bpv/docs/sep-45.rst)
- [sep45-reference/README.md](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/README.md)
- [sep45-reference/python-sep45/sep45_server.py](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/python-sep45/sep45_server.py)
- [sep45-reference/python-sep45/test_sep45_server.py](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/python-sep45/test_sep45_server.py)
- [sep45-reference/server/challenge.ts](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/server/challenge.ts)
- [sep45-reference/contracts/web_auth/src/lib.rs](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/contracts/web_auth/src/lib.rs)
- [sep45-reference/contracts/account/src/lib.rs](/home/antb2/dev/rsync/django-polaris-bpv/sep45-reference/contracts/account/src/lib.rs)

Upstream sources:

- Anchor Platform repo: https://github.com/stellar/anchor-platform
- SEP-45 reference repo: https://github.com/stellar/sep45-reference
- Stellar protocol repo: https://github.com/stellar/stellar-protocol

Key PRs and releases:

- Anchor Platform PR [#1620](https://github.com/stellar/anchor-platform/pull/1620)
- Anchor Platform PR [#1623](https://github.com/stellar/anchor-platform/pull/1623)
- Anchor Platform PR [#1639](https://github.com/stellar/anchor-platform/pull/1639)
- Anchor Platform PR [#1870](https://github.com/stellar/anchor-platform/pull/1870)
- Anchor Platform PR [#1900](https://github.com/stellar/anchor-platform/pull/1900)
- Anchor Platform PR [#1906](https://github.com/stellar/anchor-platform/pull/1906)
- Anchor Platform release [`3.2.0-beta.1`](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1)
- Anchor Platform release [`4.0.0`](https://github.com/stellar/anchor-platform/releases/tag/4.0.0)
- Anchor Platform release [`4.2.0`](https://github.com/stellar/anchor-platform/releases/tag/4.2.0)
- SEP-45 reference PR [#3](https://github.com/stellar/sep45-reference/pull/3)
- SEP-45 reference PR [#4](https://github.com/stellar/sep45-reference/pull/4)
- SEP-45 reference PR [#5](https://github.com/stellar/sep45-reference/pull/5)
- SEP-45 reference release [`v0.1.3`](https://github.com/stellar/sep45-reference/releases/tag/v0.1.3)
- Stellar protocol PR [#1827](https://github.com/stellar/stellar-protocol/pull/1827)
