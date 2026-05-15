# SEP-45 Python Service Requirements

## High-impact requirements

1. Expose SEP-45 auth endpoints:
   - `GET /sep45/auth`
   - `POST /sep45/auth`
2. Support contract-account authentication (`C...`) with Soroban authorization entries.
3. Enforce strict server-side validation before issuing JWT.
4. Prevent replay with nonce TTL and one-time use.
5. Enforce signature expiration ledger checks on submitted authorization entries.
6. Support optional `client_domain` resolution via SEP-1 `stellar.toml` (`SIGNING_KEY`).
7. Support JSON and form-urlencoded token requests.
8. Keep implementation minimal and separate from existing Deno/Polaris code.

## Non-goals for first pass

1. No Polaris integration yet.
2. No Deno replacement yet.
3. No production HA nonce backend in v1 (in-memory store is acceptable).
