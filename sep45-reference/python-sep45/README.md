# python-sep45

Minimal standalone SEP-45 auth service for contract accounts.

## Install

```bash
uv sync
```

## Configure

Required:

```bash
export STELLAR_NETWORK_RPC_URL="https://soroban-testnet.stellar.org"
export SEP45_HOME_DOMAINS="localhost:8080"
export SEP45_WEB_AUTH_CONTRACT_ID="CD3LA6RKF5D2FN2R2L57MWXLBRSEWWENE74YBEFZSSGNJRJGICFGQXMX"
export SECRET_SEP10_SIGNING_SEED="S..."
export SECRET_SEP45_JWT_SECRET="change-me"
```

Optional:

```bash
export SEP45_WEB_AUTH_DOMAIN="localhost:8080"
export SEP45_AUTH_TIMEOUT="900"
export SEP45_JWT_TIMEOUT="86400"
export SEP45_SOURCE_SIGNING_SEED="S..." # tx source account key; defaults to SECRET_SEP10_SIGNING_SEED
export STELLAR_NETWORK_PASSPHRASE="Test SDF Network ; September 2015"
export PORT="8080"
```

QuickNode alternative:

```bash
export STELLAR_NETWORK_RPC_URL="https://damp-skilled-feather.stellar-testnet.quiknode.pro/8f47cd2fc59da52c145d13b9e8b3133489878fe7/"
```

## Run

```bash
uv run python sep45_server.py
```

## API

1. `GET /sep45/auth?account=C...&home_domain=...`
2. `POST /sep45/auth` with either:
   - `application/json` body
   - `application/x-www-form-urlencoded` body

## Wallet CLI Test

Run a simple challenge/sign/token flow against your running server:

```bash
uv run python wallet_cli.py \
  --server-url http://127.0.0.1:8080 \
  --auth-path /sep45/auth \
  --account C... \
  --signer-secret S... \
  --home-domain localhost:8080 \
  --rpc-url https://soroban-testnet.stellar.org
```

Use form-encoded POST:

```bash
uv run python wallet_cli.py ... --form-post
```

## Contracts

Copied contract sources live in `python-sep45/contracts/`.

See `python-sep45/contracts/README.md` for:

1. Toolchain prerequisites
2. Build/deploy steps
3. End-to-end contract testing flow

## Test

```bash
uv run pytest -q
```
