# SEP-45 Research Memo

## 1. Executive Summary

SEP-45 is the contract-account analog of SEP-10. It lets a wallet authenticate to an anchor on behalf of a Soroban contract account (`C...`) by exchanging signed Soroban authorization entries for a JWT session token. It is explicitly additive, not a replacement: SEP-45 covers contract accounts, while SEP-10 still covers classic (`G...`) and muxed (`M...`) accounts. Services that want to support the full account surface need both. The SEP remains `Draft` as of December 16, 2025, so implementation should stay conservative, feature-flagged, and easy to revise. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md), [SEP-10](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md))

The Java/Kotlin Anchor Platform implementation is a useful reference model, but not because it is a cleanly portable design. It is useful because it shows the real implementation footprint: a new auth handshake, new config, new tests, RPC simulation dependency, shared JWT abstraction changes, database changes for nonce handling, and downstream SEP-6/24/31/38 adjustments. The Git history is the most valuable source here. Most of the important lessons arrived after the initial merge, in follow-up fixes for config validation, client-domain signer fetching, replay protection, request validation, XDR parsing hardening, RPC auth propagation, and deployment wiring. ([Anchor Platform PR #1623](https://github.com/stellar/anchor-platform/pull/1623), [Anchor Platform PR #1906](https://github.com/stellar/anchor-platform/pull/1906), [Anchor Platform 4.2.0 release](https://github.com/stellar/anchor-platform/releases/tag/4.2.0))

For a Django Polaris / Python implementation, the main lesson is not "port the Java classes." The right lesson is to introduce a generic web-auth abstraction above SEP-10 and SEP-45, isolate SEP-45-specific handshake logic behind its own service layer, keep downstream SEP services dependent on a common authenticated session object, and add only the minimum new persisted state required for nonce replay prevention and operational auditability. Current upstream Polaris is still SEP-10-centric in its auth decorators, token model, TOML generation, and transaction schema, so SEP-45 support would be a real extension, not a small patch. ([Django Polaris README](https://github.com/stellar/django-polaris/blob/master/README.rst), [polaris/sep10/token.py](https://github.com/stellar/django-polaris/blob/master/polaris/sep10/token.py), [polaris/integrations/toml.py](https://github.com/stellar/django-polaris/blob/master/polaris/integrations/toml.py))

## 2. What SEP-45 Is

SEP-45 defines web authentication for contract accounts. Discovery still begins with SEP-1 `stellar.toml`, but the advertised fields differ from SEP-10: the client discovers `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`, `WEB_AUTH_CONTRACT_ID`, and `SIGNING_KEY`. The challenge is not a classic transaction envelope. It is a set of Soroban authorization entries for a `web_auth_verify` contract function, encoded as `SorobanAuthorizationEntries`. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md), [SEP-1](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0001.md))

The actors are:

- the home domain publishing `stellar.toml`
- the anchor auth server
- the server signing key from `SIGNING_KEY`
- the client wallet
- the client contract account (`C...`)
- optionally a client domain that publishes its own `SIGNING_KEY`
- the deployed SEP-45 web-auth contract referenced by `WEB_AUTH_CONTRACT_ID`

The authentication flow, as specified, is:

1. The client fetches `stellar.toml` from the home domain and discovers the SEP-45 endpoint and contract ID.
2. The client requests a challenge from `GET <WEB_AUTH_FOR_CONTRACTS_ENDPOINT>`, including `account`, `home_domain`, and optionally `client_domain`.
3. The server returns authorization entries for `web_auth_verify`, including common arguments such as `account`, `home_domain`, `web_auth_domain`, `web_auth_domain_account`, optional client-domain fields, and a nonce.
4. The client verifies those entries, signs the auth entry associated with the contract account, optionally signs the client-domain entry, simulates the transaction, verifies the footprint is safe, and posts the signed entries back.
5. The server verifies the returned entries, verifies the nonce has not been reused, simulates again, and returns a JWT token if successful. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

"Contract account authentication" in practice means proving that the contract account's auth logic authorizes the `web_auth_verify` invocation. This is not the same as SEP-10's proof that a user controls signing keys on a classic account. SEP-45 uses the Soroban authorization mechanism and contract-side `require_auth`, not classic threshold evaluation. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

SEP-45 differs from SEP-10 in several material ways:

| Dimension | SEP-10 | SEP-45 |
| --- | --- | --- |
| Supported account types | `G...`, `M...` | `C...` |
| Challenge format | classic transaction envelope | Soroban authorization entries |
| Verification basis | account signers and thresholds | contract auth semantics plus simulation |
| Replay controls | challenge tx constraints and server logic | nonce plus signature-expiration-ledger discipline |
| Wallet responsibility | verify tx shape, signatures | verify auth entries, simulate, verify footprint |

An implementation may need both because the user population is mixed. Any anchor serving both classic wallets and smart wallets needs classic-account auth and contract-account auth side by side. The SEP explicitly says SEP-45 does not replace SEP-10. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md), [SEP-10](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md))

## 3. Where SEP-45 Fits in the Stellar SEP Landscape

SEP-45 sits in the authentication layer of the anchor stack.

SEP-1 is a prerequisite because it is the discovery mechanism for `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`, `WEB_AUTH_CONTRACT_ID`, and `SIGNING_KEY`. Without SEP-1 changes, SEP-45 is undiscoverable. ([SEP-1](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0001.md))

SEP-10 remains the classic-account auth mechanism. The two standards are complementary. Supporting SEP-45 does not remove the need for SEP-10 if classic or muxed accounts are still supported. ([SEP-10](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md), [SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

SEP-6 and SEP-24 now explicitly allow either SEP-10 or SEP-45 JWTs and permit `C...` accounts in their request parameters. This is where SEP-45 becomes operationally important for anchors. It is not enough to issue a token; deposit, withdrawal, and transaction lookup flows must accept it and interpret the authenticated identity correctly. ([SEP-6](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0006.md), [SEP-24](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0024.md))

SEP-12 is not an auth SEP, but it is downstream of auth. Contract-account authentication affects customer retrieval and update flows because the authenticated identity presented to SEP-12 may now be a contract account instead of a classic or muxed account. ([SEP-12](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0012.md))

SEP-31 is more ambiguous. The current SEP-31 text still frames inter-anchor auth in SEP-10 terms, but Anchor Platform's implementation added contract-account support in downstream services. This suggests implementation latitude, but not the same specification clarity that SEP-6, SEP-24, and SEP-38 now have. That matters if a Python implementation wants to support SEP-31 with contract accounts early. ([SEP-31](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0031.md), [Anchor Platform PR #1723](https://github.com/stellar/anchor-platform/pull/1723))

SEP-38 explicitly added SEP-45 support for request authentication. In practice, this means quote and pricing APIs may need to accept either SEP-10 or SEP-45 bearer tokens and preserve enough account identity to personalize or authorize quote access. ([SEP-38](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0038.md))

The direct design implication is simple: any anchor authentication abstraction that is SEP-10-specific will become friction once SEP-45 is added. The auth layer must become "web auth" instead of "SEP-10 auth."

## 4. How Anchor Platform Implements SEP-45

At a high level, Anchor Platform implements SEP-45 as a dedicated auth handshake service, wired into a broader shared web-auth model.

The main modules are:

- `Sep45Controller` for `GET /sep45/auth` and `POST /sep45/auth`
- `Sep45Service` for challenge generation and validation
- `PropertySep45Config` and `Sep45Config` for settings and validation
- `JwtService`, `Sep45Jwt`, and `WebAuthJwt` for token issuance and downstream parsing
- `NonceManager`, `NonceStore`, and JDBC nonce persistence for replay protection
- `StellarRpc` as the required simulation backend
- `ClientDomainHelper` for optional client-domain signer lookup
- downstream shared auth filter `WebAuthJwtFilter` so SEP-6/24/31/38 can accept either SEP-10 or SEP-45 bearer tokens ([Sep45Controller](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/controller/sep/Sep45Controller.java), [Sep45Service](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java), [SepBeans](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/component/sep/SepBeans.java))

The request flow is:

1. `GET /sep45/auth` validates the incoming `account` and `home_domain`.
2. The service constructs argument SCVals for `web_auth_verify`.
3. It simulates an `InvokeHostFunctionOperation` against the configured web-auth contract in recording mode.
4. It extracts the auth entries from simulation results.
5. It signs the server-account auth entry with the SEP-10 signing seed.
6. It returns encoded authorization entries plus `network_passphrase`. ([Sep45Service](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java))

The verification flow is:

1. `POST /sep45/auth` decodes returned authorization entries.
2. It checks the entries are present and that all entries share the same arguments.
3. It verifies the argument values, including `home_domain`, `web_auth_domain`, and optional client-domain signer resolution.
4. It verifies the server signature if one of the entries belongs to the server account.
5. It atomically verifies and consumes the nonce.
6. It simulates again in enforcing mode with the submitted auth entries.
7. If simulation succeeds, it issues a SEP-45 JWT. ([Sep45Service](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java), [NonceManager](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/auth/NonceManager.java))

JWT/session handling is split sensibly. `Sep45Jwt` extends `WebAuthJwt`, and the shared filter tries to decode SEP-10 first and then SEP-45. This lets downstream SEP controllers work with a common request attribute and common account/client-domain accessors. That is a good conceptual pattern to reuse in Python. ([Sep45Jwt](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/auth/Sep45Jwt.java), [WebAuthJwt](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/auth/WebAuthJwt.java), [WebAuthJwtFilter](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/filter/WebAuthJwtFilter.java))

Configuration requirements are explicit:

- SEP-45 must be enabled.
- RPC URL must be present.
- SEP-45 JWT secret must be present.
- home domains must be configured.
- web-auth contract ID must be configured.
- web-auth domain may be inferred only when there is a single non-wildcard home domain.
- auth timeout and JWT timeout must be positive. ([PropertySep45Config](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/config/PropertySep45Config.java), [Anchor Platform SEP-45 docs](https://developers.stellar.org/platforms/anchor-platform/sep-guide/sep45))

The external dependency that changes the architecture is RPC simulation. Anchor Platform asserts that the `LedgerClient` used for SEP-45 is actually `StellarRpc`, not Horizon. This is a hard coupling to Soroban RPC behavior. ([SepBeans](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/component/sep/SepBeans.java))

Inference: the handshake service itself is reasonably separable. The broader support story is not. Once SEP-45 is added, token abstractions, transaction identity fields, quote ownership checks, interactive-flow assumptions, deployment config, and tests all need attention.

## 5. GitHub History and Evolution of SEP-45 Support

### Initial feature introduction

The first step was not the endpoint itself but the Soroban contracts used to test and exercise the flow. `PR #1620` added the web-auth contract and a test account contract. That is a useful signal: SEP-45 is contract-aware at the protocol level, so an implementation without contract fixtures and contract-version thinking is incomplete from the start. ([PR #1620](https://github.com/stellar/anchor-platform/pull/1620))

`PR #1623`, merged on February 6, 2025, introduced the core SEP-45 implementation. The PR text is unusually candid and therefore valuable. It explicitly says the config was intentionally kept simple, tests and config comments were not fully implemented yet, and client-domain verification had not been tested. That is the best evidence that the first merge was not production-hard. ([PR #1623](https://github.com/stellar/anchor-platform/pull/1623))

### Downstream contract-account rollout

Within days, Anchor Platform had to touch downstream SEPs:

- SEP-6 deposit for contract accounts in `PR #1629`
- SEP-6 withdraw for contract accounts in `PR #1630`
- SEP-24 deposit in `PR #1640`
- SEP-24 withdraw in `PR #1649`
- SEP-12 explicit contract-account support in `PR #1647`
- SEP-38 contract-account support in `PR #1667` ([PR #1629](https://github.com/stellar/anchor-platform/pull/1629), [PR #1630](https://github.com/stellar/anchor-platform/pull/1630), [PR #1640](https://github.com/stellar/anchor-platform/pull/1640), [PR #1649](https://github.com/stellar/anchor-platform/pull/1649), [PR #1647](https://github.com/stellar/anchor-platform/pull/1647), [PR #1667](https://github.com/stellar/anchor-platform/pull/1667))

This reveals the first hidden assumption: "adding SEP-45" is not an auth-only task. Any anchor implementation that treats it that way will end up with downstream flows that either reject contract accounts or mishandle identity.

### Config hardening

`PR #1639` added SEP-45 config validation after the core feature already existed. That included validation for RPC presence, JWT secret, home domains, contract ID format, web-auth domain rules, and timeout values. This is a predictable maturity pattern: protocol features tend to land before config validation is strict enough. A Python implementation should invert that sequence and ship hard validation first. ([PR #1639](https://github.com/stellar/anchor-platform/pull/1639))

### Identity model cleanup

`PR #1657` renamed `sep10_account` fields to `web_auth_account`. This is one of the clearest architectural lessons in the entire history. As soon as SEP-45 existed, SEP-10-specific field names became wrong. If Django Polaris introduces SEP-45 support without first generalizing the identity model, it will create the same debt. ([PR #1657](https://github.com/stellar/anchor-platform/pull/1657))

### JWT secret separation

`PR #1659` changed SEP-45 JWT encoding and decoding to use a dedicated SEP-45 secret instead of the SEP-10 secret. That is a straightforward boundary correction. Reusing the same secret across auth mechanisms is unnecessary coupling and makes rotation and blast-radius management worse. The Python design should use a separate secret from day one. ([PR #1659](https://github.com/stellar/anchor-platform/pull/1659))

### Client-domain verification and trust model changes

`PR #1673` added SEP-45 client-domain verification tests. The current `Sep45Client` and integration tests show two important cases: successful verification when the client-domain signer signs, and rejection when that signature is missing. This confirms client-domain support is optional but semantically meaningful. ([PR #1673](https://github.com/stellar/anchor-platform/pull/1673), [Sep45Tests](https://github.com/stellar/anchor-platform/blob/develop/essential-tests/src/testFixtures/kotlin/org/stellar/anchor/platform/integrationtest/Sep45Tests.kt))

The trust model then changed again. The history for `ClientDomainHelper` shows `PR #1865`, "Fetch client domain signer over HTTPS", merged on December 3, 2025. Before that, the helper had briefly been switched to HTTP in `PR #1673` to make tests work. The later fix restored HTTPS and only allows HTTP retry outside pubnet by checking the network passphrase. That sequence teaches two things:

- signer discovery is a real security boundary, not a convenience lookup
- test convenience can create silent production trust regressions if not corrected promptly ([PR #1865](https://github.com/stellar/anchor-platform/pull/1865), [ClientDomainHelper](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/util/ClientDomainHelper.java))

### Replay protection and nonce handling

`PR #1676` added nonce generation, persistence, cleanup, and validation. This created nonce models, JDBC storage, cleanup jobs, and tests. The important lesson is that replay protection became a database concern, not just a protocol concern. The SEP talks about nonce uniqueness and expiration, but production implementation required explicit persistence and scheduled cleanup. ([PR #1676](https://github.com/stellar/anchor-platform/pull/1676), [NonceCleanupJob](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/job/NonceCleanupJob.java))

That was still not enough. On March 18, 2026, `PR #1906` fixed a SEP-45 nonce TOCTOU race condition after a HackerOne report. The patch replaced separate verify/use semantics with atomic `verifyAndUse()` backed by a single SQL update predicate `used=false AND expires_at > now`. This is the strongest evidence in the history that replay protection must be atomic at the persistence layer. A Django implementation should not ship a two-step verify-then-mark flow. ([PR #1906](https://github.com/stellar/anchor-platform/pull/1906), [JdbcNonceRepo](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/data/JdbcNonceRepo.java))

### Simulation/config simplification

`PR #1697` removed `SEP45_SIMULATING_SIGNING_SEED`. Earlier versions assumed a separate signing seed was needed for simulation. The follow-up removed that config and switched to a random account as the simulation source. This is a good example of configuration load that turned out to be unnecessary. For Python, avoid introducing configuration unless it is proven necessary. ([PR #1697](https://github.com/stellar/anchor-platform/pull/1697))

### Contract-version and compatibility churn

`PR #1702` updated argument names for the contract from `home_domain_address` / `client_domain_address` to `web_auth_domain_account` / `client_domain_account` and added contract metadata fields `sep` and `version`. This shows that even the reference contract interface was still moving. A Python implementation must expect versioning and compatibility checks around the contract ID and contract semantics. ([PR #1702](https://github.com/stellar/anchor-platform/pull/1702), [web-auth contract](https://github.com/stellar/anchor-platform/blob/develop/soroban/contracts/web-auth/src/lib.rs))

### Test-environment and rollout friction

The integration tests had to be explicitly skipped when `stellar_network.rpc_url` was not set (`commit 1e0a79e6`). Later PRs enabled SEP-45 in dev env (`PR #1766`) and fixed the Helm chart so the SEP-45 JWT secret actually reached deployments (`PR #1767`). This is operationally important. SEP-45 is not just code plus tests. It is code plus RPC infrastructure plus deployment secrets plus a credible local/dev path. ([commit 1e0a79e6](https://github.com/stellar/anchor-platform/commit/1e0a79e655c64b990d17c07f5ebe289bd8812db0), [PR #1766](https://github.com/stellar/anchor-platform/pull/1766), [PR #1767](https://github.com/stellar/anchor-platform/pull/1767))

### Interoperability and validation fixes

`PR #1774` normalized home-domain comparison so that values like `example.com`, `localhost:8080`, and `https://example.com` would compare on authority rather than exact string form. This reveals a classic implementation trap: domain values from wallets, config, and TOML are rarely normalized the same way. Do not compare raw strings. ([PR #1774](https://github.com/stellar/anchor-platform/pull/1774))

`PR #1870` added basic request validation because malformed requests were falling through to internal server errors. Specifically, the service added checks for missing `account`, non-contract `account`, missing `home_domain`, missing `authorization_entries`, and empty decoded auth-entry sets. This is a maturity signal: even if the protocol is correct, request-shape hardening often gets postponed until after bad traffic hits real servers. ([PR #1870](https://github.com/stellar/anchor-platform/pull/1870))

### XDR/SDK evolution

`commit eb383978` replaced a local `SorobanAuthorizationEntryList` wrapper with the SDK's `SorobanAuthorizationEntries` type. This indicates underlying SDK/XDR model movement and a shift toward official types. A Python implementation should avoid inventing local wrappers unless the SDK truly cannot express the required shape. ([commit eb383978](https://github.com/stellar/anchor-platform/commit/eb3839787dcae5aa2b7c55cc54714cf2894801d5))

### Security hardening in 2026

`PR #1900`, merged on March 5, 2026, upgraded `java-stellar-sdk` from `2.0.0` to `2.2.3` because the SDK's XDR decoder needed a fix that validated length prefixes before allocating memory. Anchor Platform also added a 100 KB size guard on raw `authorization_entries` before decoding. This is a direct lesson for Python: treat auth-entry payload decoding as an attack surface, not as trusted protocol data. ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900), [Anchor Platform 4.1.8 release](https://github.com/stellar/anchor-platform/releases/tag/4.1.8))

`PR #1904`, merged on March 18, 2026, fixed a bug where configured RPC auth headers were not actually propagated into requests. Since SEP-45 is RPC-dependent, bad infra auth broke auth flows. This is not a protocol bug. It is an operational dependency bug. Python needs explicit tests for authenticated RPC providers. ([PR #1904](https://github.com/stellar/anchor-platform/pull/1904))

Overall, the history teaches that the real implementation complexity lives in trust boundaries, request validation, replay protection, infrastructure auth, and downstream identity semantics, not in the happy-path controller.

## 6. API / Auth / Integration Surface

The public SEP-45 API surface is small:

- `GET <WEB_AUTH_FOR_CONTRACTS_ENDPOINT>` to request authorization entries
- `POST <WEB_AUTH_FOR_CONTRACTS_ENDPOINT>` to exchange signed entries for a JWT
- downstream SEP requests continue to use `Authorization: Bearer <JWT>` ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

The discovery surface is also small but non-negotiable:

- `WEB_AUTH_FOR_CONTRACTS_ENDPOINT`
- `WEB_AUTH_CONTRACT_ID`
- `SIGNING_KEY` in `stellar.toml` ([SEP-1](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0001.md))

Anchor Platform's service surface adds config and runtime dependencies:

- `sep45.enabled`
- `sep45.home_domains`
- `sep45.web_auth_domain`
- `sep45.web_auth_contract_id`
- `sep45.auth_timeout`
- `sep45.jwt_timeout`
- `secret.sep45.jwt_secret`
- `secret.sep10.signing_seed`
- `stellar_network.rpc_url` ([Anchor Platform docs](https://developers.stellar.org/platforms/anchor-platform/sep-guide/sep45), [PropertySep45Config](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/config/PropertySep45Config.java))

The integration surface into other SEPs is through shared bearer-token handling. Anchor Platform solved this by switching to `WebAuthJwtFilter` and a shared `WebAuthJwt` abstraction. Conceptually, that is the right move. Python should do the same.

Inference: the right Python integration shape is:

- one bearer-token contract for downstream SEP endpoints
- two authenticators behind it, SEP-10 and SEP-45
- one generic authenticated identity object passed into SEP-6/24/38 logic
- one TOML generator that can advertise both auth mechanisms

Anything more fragmented will create controller-level duplication and inconsistent auth behavior.

## 7. Data Model and State Implications

Anchor Platform's persisted SEP-45 state is intentionally narrow:

- a nonce table with `id`, `used`, and `expires_at`
- transaction tables carrying generic authenticated identity fields and `client_domain`
- no persistence of raw challenge authorization entries by default ([V22 migration](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/resources/db/migration/V22__web_auth_account.sql), [JdbcNonce](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/data/JdbcNonce.java))

That is a good default. For Django Polaris / Python, the likely persisted entities are:

- `WebAuthNonce`
  - `id`
  - `used`
  - `expires_at`
  - optional `created_at`
  - optional `used_at`
  - optional `account`
  - optional `home_domain`
  - optional `client_domain`

- transaction identity fields generalized away from SEP-10 assumptions
  - authenticated account
  - authenticated account type (`classic`, `muxed`, `contract`)
  - authenticated memo if applicable
  - client domain
  - optional auth mechanism (`sep10`, `sep45`)

- optional audit/event model
  - request id
  - auth mechanism
  - endpoint
  - account
  - client domain
  - nonce id or nonce digest
  - simulation status
  - failure reason
  - timestamps

The practical split should be:

- persisted state for replay prevention and operations
- derived/transient state for auth-entry content, simulation results, and JWT parsing

Do not persist raw JWTs. Do not persist raw authorization entries unless there is a strong forensic requirement. If auditability is needed, store a digest and structured metadata instead.

For current Polaris specifically, the data model is still centered on `stellar_account`, `muxed_account`, and `account_memo`, with docstrings that explicitly describe SEP-10-authenticated classic accounts. That is serviceable for classic flows but awkward for contract-account support. It strongly suggests either:

- adding new generic web-auth fields and migrating call sites, or
- wrapping the existing fields with a new abstraction while introducing a contract-account extension model

The first option is architecturally cleaner. ([polaris/models.py](https://github.com/stellar/django-polaris/blob/master/polaris/models.py))

## 8. Security, Compliance, and Operational Concerns

### Trust boundaries

SEP-45 adds more trust boundaries than SEP-10:

- the wallet's verification of server intent
- the anchor's verification of contract authorization
- the optional client-domain signer lookup
- the trustworthiness and availability of the RPC provider
- the correctness and version of the deployed web-auth contract
- the JWT signing and verification secret material ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

### Attack surfaces

Replay risk is the clearest one. The spec requires nonce handling and recommends signature-expiration-ledger discipline. Anchor Platform's post-release TOCTOU fix proves that nonce persistence alone is insufficient if consume semantics are not atomic. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md), [PR #1906](https://github.com/stellar/anchor-platform/pull/1906))

Signature verification risk exists in both server and client directions:

- the wallet must verify the server-auth entry is actually signed by the discovered server account
- the server must verify its own signed entry and trust simulation to validate the rest
- if client-domain verification is enabled, both sides must agree on the correct `SIGNING_KEY`

Client-domain fetching is a real attack surface. The history shows an implementation regression from HTTPS to HTTP and then a fix back to HTTPS. Production Python should:

- use HTTPS by default
- fail closed on pubnet
- make any HTTP fallback explicit and non-production only
- bound response size and redirect count for TOML fetching ([PR #1865](https://github.com/stellar/anchor-platform/pull/1865), [Anchor Platform 4.1.8 release](https://github.com/stellar/anchor-platform/releases/tag/4.1.8))

RPC simulation responses are another fragility source. If the RPC provider requires auth, incorrect header propagation can break all SEP-45 auth. If the provider is slow or inconsistent, SEP-45 auth latency becomes infrastructure-bound. If the SDK parser is weak, malformed auth-entry payloads become a denial-of-service vector. ([PR #1900](https://github.com/stellar/anchor-platform/pull/1900), [PR #1904](https://github.com/stellar/anchor-platform/pull/1904))

### JWT issuance and expiry

Short JWT lifetimes are preferable. The spec notes that account control can change after issuance. For contract accounts, the same principle applies, and arguably more so, because contract auth rules may change in ways the anchor does not observe directly. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

### Logging

What must never be logged:

- signing seeds
- JWT secrets
- raw JWTs
- full raw authorization entries
- client-domain secrets

What should be logged:

- account identifier
- auth mechanism
- home domain
- client domain if present
- nonce id or digest
- simulation success/failure
- rejection reason
- RPC provider class or URL host
- request correlation id

Anchor Platform's own logging style is concise and factual, which is the right pattern to follow.

### Production controls

Operational controls worth carrying into Python:

- feature-flag SEP-45 separately from SEP-10
- health checks for RPC reachability
- startup config validation for all required keys and domains
- strict input size limits on auth-entry payloads
- atomic nonce consumption
- clear pubnet vs non-pubnet client-domain fetch policy
- metrics on auth success, auth failure, nonce reuse, simulation latency, TOML fetch failures, and RPC auth failures

## 9. UX / Product Implications

SEP-45 changes product behavior for smart wallets because the wallet must understand Soroban auth entries, not just sign a challenge transaction. A compliant wallet should verify the challenge semantics and perform its own simulation and footprint checks. That is more demanding than SEP-10 and will create more wallet-side failure modes. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))

For anchors, the biggest product shift is that "authenticated user identity" is no longer synonymous with a classic account plus optional memo. This matters for KYC reuse, transaction grouping, rate personalization, and support tools.

For support teams, the confusing failure cases will be:

- unsupported or malformed home domain
- invalid contract account format
- client-domain signer fetch failure
- missing client-domain signature
- expired or reused nonce
- expired signature ledger
- RPC outage or unauthorized RPC
- wallet signed the wrong network passphrase

For admin teams, the operational questions become:

- which wallets are using SEP-45 successfully
- which client domains are presenting themselves
- whether the deployed contract ID is consistent across environments
- whether a failure is caused by the wallet, TOML hosting, RPC, or anchor logic

Mixed SEP-10 + SEP-45 support should be presented as account-type-dependent behavior, not as two products. A user with a classic account should experience normal SEP-10. A user with a smart wallet should discover and use SEP-45. The product should avoid forcing users to choose an auth mechanism manually whenever possible.

One specific product limitation surfaced in Anchor Platform's `3.2.0-beta.1` release notes: because memos are not part of SEP-45 semantics, SEP-6 and SEP-24 withdrawals were limited to one ongoing transaction per account. That is a concrete reminder that protocol identity semantics can leak directly into product constraints. A Python implementation should identify those cases early and decide whether to accept them, redesign around them, or delay the feature. ([Anchor Platform 3.2.0-beta.1 release](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1))

## 10. Translation to Django Polaris / Python

This is the most important section.

Current upstream Django Polaris does not appear to have first-class SEP-45 support. The official README still says Polaris supports SEP-1, 6, 10, 12, 24, 31, and 38. The code tree has no SEP-45 module. The SEP-1 TOML integration only lists `WEB_AUTH_ENDPOINT` among default auth-related fields. The token abstraction is `SEP10Token`, and the validation decorator is `validate_sep10_token()`. The `Transaction` model is documented in explicitly SEP-10-centric terms. There are no SEP-45 mentions in current official releases. ([README.rst](https://github.com/stellar/django-polaris/blob/master/README.rst), [polaris/integrations/toml.py](https://github.com/stellar/django-polaris/blob/master/polaris/integrations/toml.py), [polaris/sep10/utils.py](https://github.com/stellar/django-polaris/blob/master/polaris/sep10/utils.py), [polaris/models.py](https://github.com/stellar/django-polaris/blob/master/polaris/models.py), [Django Polaris releases](https://github.com/stellar/django-polaris/releases))

That means SEP-45 support in Polaris is not a small extension. It is an architectural addition.

### What is worth copying conceptually from Anchor Platform

- A dedicated SEP-45 handshake service.
- A shared web-auth token abstraction consumed by downstream SEP services.
- Separate JWT secret material for SEP-45.
- Database-backed nonce storage with atomic consume semantics.
- Explicit RPC dependency rather than trying to fake SEP-45 through Horizon-oriented abstractions.
- Client-domain handling as a separate helper/service with its own trust policy.

### What should be simplified for Django Polaris

- Avoid Spring-style bean wiring. Use a small service layer:
  - `webauth/session.py`
  - `webauth/sep10.py`
  - `webauth/sep45.py`
  - `webauth/nonces.py`
  - `webauth/client_domain.py`
  - `webauth/rpc.py`

- Do not add a separate microservice initially. Keep it in the same Django app if SEP-45 is only used by Polaris-managed SEP endpoints.
- Do not overbuild contract-version registries or generic policy engines. Start with one supported contract interface and explicit config.

### What should likely live outside stock Polaris flows

- The SEP-45 auth endpoints themselves are new.
- Any contract-account-aware interactive-flow session logic for SEP-24 will likely need custom work because Polaris' current interactive auth/session helpers are tightly shaped around SEP-10 JWT structure and classic-account fields. ([polaris/sep24/utils.py](https://github.com/stellar/django-polaris/blob/master/polaris/sep24/utils.py))

### What custom Django models or layers are likely needed

- `WebAuthNonce` model
- a generic `WebAuthSession` or `WebAuthToken` parser type
- a `Sep45Token` parser
- middleware or DRF auth classes that can accept either SEP-10 or SEP-45 bearer tokens
- TOML integration override that emits `WEB_AUTH_FOR_CONTRACTS_ENDPOINT` and `WEB_AUTH_CONTRACT_ID`
- generic transaction identity fields or adapters to remove SEP-10-only assumptions

### What is a natural fit in Polaris

- existing SEP-6, SEP-24, and SEP-38 endpoint structure
- integration hook model for customer, quote, info, and rails logic
- Django ORM for nonce persistence and audit tables

### What is awkward in Polaris

- the SEP-10-specific token class and decorator naming
- the transaction model's classic-account framing
- SEP-24 interactive helpers that assume current token/session structure
- lack of native RPC simulation/auth service abstractions

### Same service, dedicated auth service, or hybrid?

The simplest robust design is a hybrid inside one deployable:

- one Django service
- one internal `webauth` module
- one generic auth layer for downstream SEPs
- separate endpoint modules for SEP-10 and SEP-45
- one shared transaction/customer/quote ownership model

A standalone auth microservice is unnecessary unless there is a broader platform need to share auth across multiple independently deployed services. Nothing in the current evidence suggests that is needed first.

## 11. Recommended Learnings for Our Django Polaris Python Implementation

### Concrete design recommendations

- Introduce a generic web-auth abstraction before adding SEP-45 endpoints.
- Add SEP-45 alongside SEP-10, not instead of SEP-10.
- Treat RPC simulation as a first-class dependency with explicit config, health checks, and auth support.
- Store nonces in the database and consume them atomically.
- Keep JWTs stateless and separate SEP-45 signing secrets from SEP-10 secrets.
- Generalize transaction and quote ownership concepts away from SEP-10-specific names.

### Recommended boundaries and abstractions

- `Sep10Authenticator`
- `Sep45Authenticator`
- `WebAuthSession`
- `NonceRepository`
- `ClientDomainResolver`
- `RpcSimulationService`
- `WebAuthDiscoveryTOMLAdapter`

These are small, obvious boundaries. They are enough to avoid coupling, but not so many that the design becomes framework noise.

### Minimum sensible implementation

The minimum sensible Python implementation is:

1. Add SEP-45 discovery fields to `stellar.toml`.
2. Add `GET /sep45/auth` and `POST /sep45/auth`.
3. Add a generic bearer-token authentication layer that accepts SEP-10 or SEP-45.
4. Add nonce persistence and atomic consume.
5. Update SEP-6, SEP-24, and SEP-38 to accept generic web-auth identity.
6. Defer SEP-31 unless there is a concrete interoperability need.

### What we should not overbuild initially

- a separate auth service
- generalized multi-contract policy engines
- rich challenge persistence
- broad abstraction for every possible Soroban auth pattern
- custom client SDKs unless there is an immediate wallet integration need

### What we should test aggressively

- malformed and oversized `authorization_entries`
- duplicate nonce submissions under concurrency
- client-domain signer fetch over HTTPS
- client-domain missing signature
- home-domain normalization and matching
- RPC auth-header propagation
- downstream transaction and quote ownership checks with SEP-45 identities
- mixed SEP-10 and SEP-45 bearer-token handling on the same API surface

### Open unknowns before implementation begins

- whether target wallets already implement the required client-side simulation and footprint validation
- how much SEP-24 interactive flow behavior must change for contract-account users
- whether product requirements demand shared-account-like behavior for contract wallets
- whether SEP-31 support is actually needed in the first release
- whether upstream Polaris evolution is expected to add SEP-45 in a way worth aligning with

## 12. Open Questions / Risks / Ambiguities

- SEP-45 remains `Draft`, so some details are still subject to change. ([SEP-45](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md))
- The current SEP-31 spec is less explicit about SEP-45 than SEP-6, SEP-24, and SEP-38.
- Wallet-side compliance with the spec's simulation and footprint-verification guidance is uncertain.
- Anchor Platform's current SEP-45 JWT issuer appears inconsistent with SEP-10 and the SEP text. That should be treated as an implementation detail to evaluate, not as a precedent to copy. ([Sep10Service](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep10/Sep10Service.java), [Sep45Service](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java))
- Current Polaris is structurally SEP-10-centric enough that SEP-45 will likely require internal refactoring, not just additive endpoints.

## 13. References

- [SEP-45: Stellar Web Authentication for Contract Accounts](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0045.md)
- [SEP-1: Stellar Info File](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0001.md)
- [SEP-10: Stellar Web Authentication](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md)
- [SEP-6: Anchor/Client Interoperability](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0006.md)
- [SEP-12: Customer Information Transfer](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0012.md)
- [SEP-24: Hosted Deposit and Withdrawal](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0024.md)
- [SEP-31: Cross-Border Payments](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0031.md)
- [SEP-38: Anchor RFQ API](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0038.md)
- [Stellar Docs: Anchor Platform SEP-45 Guide](https://developers.stellar.org/platforms/anchor-platform/sep-guide/sep45)
- [Anchor Platform `Sep45Service`](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/sep45/Sep45Service.java)
- [Anchor Platform `Sep45Controller`](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/controller/sep/Sep45Controller.java)
- [Anchor Platform `PropertySep45Config`](https://github.com/stellar/anchor-platform/blob/develop/platform/src/main/java/org/stellar/anchor/platform/config/PropertySep45Config.java)
- [Anchor Platform `WebAuthJwtFilter`](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/filter/WebAuthJwtFilter.java)
- [Anchor Platform `ClientDomainHelper`](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/util/ClientDomainHelper.java)
- [Anchor Platform `StellarRpc`](https://github.com/stellar/anchor-platform/blob/develop/core/src/main/java/org/stellar/anchor/ledger/StellarRpc.java)
- [Anchor Platform web-auth contract](https://github.com/stellar/anchor-platform/blob/develop/soroban/contracts/web-auth/src/lib.rs)
- [Anchor Platform PR #1620: Add SEP-45 contracts](https://github.com/stellar/anchor-platform/pull/1620)
- [Anchor Platform PR #1623: Implement SEP-45](https://github.com/stellar/anchor-platform/pull/1623)
- [Anchor Platform PR #1639: Add SEP-45 config validation](https://github.com/stellar/anchor-platform/pull/1639)
- [Anchor Platform PR #1657: Rename all `sep_10_account` fields to `web_auth_account`](https://github.com/stellar/anchor-platform/pull/1657)
- [Anchor Platform PR #1659: Use SEP-45 JWT secret for signing SEP-45 JWTs](https://github.com/stellar/anchor-platform/pull/1659)
- [Anchor Platform PR #1673: Add SEP-45 client domain verification tests](https://github.com/stellar/anchor-platform/pull/1673)
- [Anchor Platform PR #1676: Implement nonce validation for SEP-45](https://github.com/stellar/anchor-platform/pull/1676)
- [Anchor Platform PR #1697: Remove `SEP45_SIMULATING_SIGNING_SEED`](https://github.com/stellar/anchor-platform/pull/1697)
- [Anchor Platform PR #1702: Update SEP-45 contract version](https://github.com/stellar/anchor-platform/pull/1702)
- [Anchor Platform PR #1723: Contract Account Support](https://github.com/stellar/anchor-platform/pull/1723)
- [Anchor Platform PR #1766: Enable sep45 for dev env](https://github.com/stellar/anchor-platform/pull/1766)
- [Anchor Platform PR #1767: Fix sep45 helm chart](https://github.com/stellar/anchor-platform/pull/1767)
- [Anchor Platform PR #1774: Fix sep45 homeDomain validation](https://github.com/stellar/anchor-platform/pull/1774)
- [Anchor Platform commit `1e0a79e6`: Disable Sep45Tests if RPC URL is not set](https://github.com/stellar/anchor-platform/commit/1e0a79e655c64b990d17c07f5ebe289bd8812db0)
- [Anchor Platform commit `eb383978`: Replace SorobanAuthEntryList with SorobanAuthEntries](https://github.com/stellar/anchor-platform/commit/eb3839787dcae5aa2b7c55cc54714cf2894801d5)
- [Anchor Platform PR #1865: Fetch client domain signer over HTTPS](https://github.com/stellar/anchor-platform/pull/1865)
- [Anchor Platform PR #1870: Fix SEP-45 request validation](https://github.com/stellar/anchor-platform/pull/1870)
- [Anchor Platform PR #1900: Upgrade `java-stellar-sdk` to fix SEP-45 OOM](https://github.com/stellar/anchor-platform/pull/1900)
- [Anchor Platform PR #1904: Fix RPC header auth not being sent in requests](https://github.com/stellar/anchor-platform/pull/1904)
- [Anchor Platform PR #1906: Fix SEP-45 nonce TOCTOU race condition](https://github.com/stellar/anchor-platform/pull/1906)
- [Anchor Platform release `3.2.0-beta.1`](https://github.com/stellar/anchor-platform/releases/tag/3.2.0-beta.1)
- [Anchor Platform release `4.0.0`](https://github.com/stellar/anchor-platform/releases/tag/4.0.0)
- [Anchor Platform release `4.1.8`](https://github.com/stellar/anchor-platform/releases/tag/4.1.8)
- [Anchor Platform release `4.2.0`](https://github.com/stellar/anchor-platform/releases/tag/4.2.0)
- [Stellar Protocol PR #1827: Handle archived SEP-45 contract instances](https://github.com/stellar/stellar-protocol/pull/1827)
- [Django Polaris README](https://github.com/stellar/django-polaris/blob/master/README.rst)
- [Django Polaris `polaris/sep10/token.py`](https://github.com/stellar/django-polaris/blob/master/polaris/sep10/token.py)
- [Django Polaris `polaris/sep10/utils.py`](https://github.com/stellar/django-polaris/blob/master/polaris/sep10/utils.py)
- [Django Polaris `polaris/models.py`](https://github.com/stellar/django-polaris/blob/master/polaris/models.py)
- [Django Polaris `polaris/integrations/toml.py`](https://github.com/stellar/django-polaris/blob/master/polaris/integrations/toml.py)
- [Django Polaris reference `stellar.toml`](https://github.com/stellar/django-polaris/blob/master/server/static/polaris/stellar.toml)
- [Django Polaris releases](https://github.com/stellar/django-polaris/releases)
