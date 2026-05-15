# SEP-45 Python Service Specification

## Summary

Build a standalone Python SEP-45 auth service under `python-sep45/` using `Flask`, `stellar-sdk`, and `uv`.
Keep code minimal: one service file, one test file.

## Endpoints

1. `GET /sep45/auth`
   - Query:
     - `account` (required, `C...`)
     - `home_domain` (required)
     - `client_domain` (optional)
   - Response:
     - `200`: `{"authorization_entries": "<base64>", "network_passphrase": "..."}`
     - `400/403/500`: `{"error": "..."}`

2. `POST /sep45/auth`
   - Body (both formats supported):
     - `application/json`: `{"authorization_entries": "<base64-xdr>"}`
     - `application/x-www-form-urlencoded`: `authorization_entries=<base64-xdr>`
   - Response:
     - `200`: `{"token": "<jwt>"}`
     - `400/403/500`: `{"error": "..."}`

3. Compatibility aliases (kept for parity with current reference app):
   - `GET /challenge`
   - `POST /challenge`

## Configuration

Required environment variables:

1. `STELLAR_NETWORK_RPC_URL`
2. `SEP45_HOME_DOMAINS`
3. `SEP45_WEB_AUTH_CONTRACT_ID`
4. `SECRET_SEP10_SIGNING_SEED`
5. `SECRET_SEP45_JWT_SECRET`

Optional:

1. `SEP45_WEB_AUTH_DOMAIN`
2. `SEP45_AUTH_TIMEOUT` (default `900`)
3. `SEP45_JWT_TIMEOUT` (default `86400`)
4. `SEP45_SOURCE_SIGNING_SEED` (optional tx source; defaults to `SECRET_SEP10_SIGNING_SEED`)
5. `STELLAR_NETWORK_PASSPHRASE` (default testnet passphrase)
6. `PORT` (default `8080`)

Default RPC example:

1. `https://soroban-testnet.stellar.org`

Alternative test RPC:

1. `https://damp-skilled-feather.stellar-testnet.quiknode.pro/8f47cd2fc59da52c145d13b9e8b3133489878fe7/`

## Control Flow

```text
GET /sep45/auth
  -> validate request
  -> optional client_domain SIGNING_KEY lookup
  -> generate+store nonce
  -> build web_auth_verify invocation
  -> simulate tx to get auth entries
  -> sign server auth entry
  -> return auth entries + passphrase

POST /sep45/auth
  -> decode auth entries
  -> strict invariant checks
  -> signature-expiration-ledger checks
  -> nonce consume (one-time)
  -> simulate tx with submitted auth entries
  -> issue JWT
```

## Strict Validation Rules

1. All auth entries must call configured contract ID.
2. Function must be `web_auth_verify`.
3. No sub-invocations.
4. Args map must match across entries.
5. Required args:
   - `account`
   - `home_domain`
   - `web_auth_domain`
   - `web_auth_domain_account`
   - `nonce`
6. `account` must match `C...`.
7. `home_domain` must match configured accepted home domains.
8. `web_auth_domain` must match configured auth domain.
9. `web_auth_domain_account` must match signing key public key.
10. If `client_domain` present, `client_domain_account` must also be present and valid.
11. Must include server auth entry.
12. Must include client account auth entry.
13. If `client_domain` present, must include client-domain auth entry.
14. Nonce must exist, be unexpired, and unused.
15. For each address credential, `signature_expiration_ledger` must be in the future at verification time.

## JWT Claims

SEP-45 required claims:

1. `iss` = `web_auth_domain`
2. `sub` = account (`C...`)
3. `iat`
4. `exp`

Server extension claims (nonstandard, allowed by SEP-45):

1. `client_domain`
2. `home_domain`
3. `jti` (sha256 over submitted `authorization_entries` payload)

Algorithm:

1. `HS256` using `SECRET_SEP45_JWT_SECRET`

## Minimal File Layout

1. `python-sep45/pyproject.toml`
2. `python-sep45/README.md`
3. `python-sep45/sep45_server.py`
4. `python-sep45/test_sep45_server.py`
5. `python-sep45/docs/requirements.md`
6. `python-sep45/docs/spec.md`

## Testing Section

Test runner:

1. `uv run pytest -q`

Required tests:

1. `GET` success without `client_domain`
2. `GET` success with `client_domain`
3. Missing `account` => 400
4. Invalid account format => 400
5. Invalid home domain => 400
6. Invalid client domain => 400
7. client_domain TOML fetch failure => 400
8. missing/invalid client SIGNING_KEY => 400
9. `POST` success returns JWT with expected claims
10. nonce replay rejected
11. nonce expired rejected
12. contract ID mismatch rejected
13. function mismatch rejected
14. sub-invocation rejected
15. inconsistent args rejected
16. missing required args rejected
17. missing server auth entry rejected
18. missing client auth entry rejected
19. missing client-domain auth entry rejected when required
20. simulation failure rejected
21. alias route parity (`/challenge`)
22. form-url-encoded `POST` accepted
23. expired `signature_expiration_ledger` rejected

Test approach:

1. Unit/API tests mock RPC and XDR parsing seams.
2. Keep one optional smoke test behind env flag for live RPC.
