# Contracts In `python-sep45/contracts`

This directory is a local copy of the reference Soroban contracts:

1. `account` custom account contract (`C...` contract account)
2. `web_auth` SEP-45 verifier contract

## Current Status

On this machine, `cargo`, `rustup`, and `stellar` CLI were not available during setup.
So build/deploy commands below are the next steps once toolchain is installed.

## 1) Install Tooling

Install:

1. Rust toolchain (`rustup`, `cargo`)
2. wasm target: `wasm32-unknown-unknown`
3. Stellar CLI (`stellar`)

Verify:

```bash
cargo --version
rustup target list --installed | rg wasm32-unknown-unknown
stellar --version
```

## 2) Build Contracts

From repo root:

```bash
cd python-sep45/contracts
stellar contract build
```

Expected output wasm files under:

1. `python-sep45/contracts/target/wasm32-unknown-unknown/release/account.wasm`
2. `python-sep45/contracts/target/wasm32-unknown-unknown/release/web_auth.wasm`

## 3) Deploy Account Contract (create a fresh `C...`)

Set env:

```bash
export STELLAR_NETWORK=testnet
export STELLAR_RPC_URL="https://soroban-testnet.stellar.org"
export ADMIN_SECRET="S..."
```

Get admin pubkey and raw signer pubkey bytes:

```bash
python - <<'PY'
from stellar_sdk import Keypair
kp = Keypair.from_secret("S...")
print("G:", kp.public_key)
print("raw_hex:", kp.raw_public_key().hex())
PY
```

Deploy `account.wasm` with constructor args:

1. `admin`: `Address` (`G...`)
2. `signer`: `BytesN<32>` (raw signer pubkey bytes)

Use Stellar CLI deploy/init flow (exact command flags can vary by CLI version):

1. Install wasm
2. Create/deploy contract
3. Invoke constructor with `admin` and `signer`
4. Save resulting contract ID (`C...`)

## 4) Deploy or Reuse Web Auth Contract

You can either:

1. Reuse deployed testnet contract from SEP-45 docs:
   - `CD3LA6RKF5D2FN2R2L57MWXLBRSEWWENE74YBEFZSSGNJRJGICFGQXMX`
2. Or deploy local `web_auth.wasm` similarly and use that contract ID.

## 5) Configure Python SEP-45 Server

Set:

```bash
export STELLAR_NETWORK_RPC_URL="https://soroban-testnet.stellar.org"
export SEP45_HOME_DOMAINS="localhost:8080"
export SEP45_WEB_AUTH_DOMAIN="localhost:8080"
export SEP45_WEB_AUTH_CONTRACT_ID="C...or CD3LA6..."
export SEP45_SOURCE_SIGNING_SEED="S..."        # tx source account
export SECRET_SEP10_SIGNING_SEED="S..."        # server auth signer (maps to web_auth_domain_account)
export SECRET_SEP45_JWT_SECRET="change-me-32-bytes-minimum"
```

Run:

```bash
cd python-sep45
uv run python sep45_server.py
```

## 6) End-to-end Test With Wallet CLI

In another terminal:

```bash
cd python-sep45
uv run python wallet_cli.py \
  --server-url http://127.0.0.1:8080 \
  --auth-path /sep45/auth \
  --account C... \
  --signer-secret S... \
  --home-domain localhost:8080 \
  --rpc-url https://soroban-testnet.stellar.org
```

For form post test:

```bash
uv run python wallet_cli.py ... --form-post
```

## 7) Contract Testing Notes

Suggested checks:

1. Challenge returns two entries minimum (client + server).
2. Client entry signs successfully with your contract signer.
3. POST returns JWT and replaying the same `authorization_entries` fails.
4. Expired `signature_expiration_ledger` is rejected.

If token simulation fails with `Auth, InvalidAction`, the contract account signer state or constructor args are usually wrong.
