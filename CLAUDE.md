# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django Polaris BPV is an extendable Django app for implementing Stellar Ecosystem Proposals (SEPs). It provides a web server supporting SEP-1, 6, 10, 12, 24, 31, and 38. Originally developed by Stellar Development Foundation, now maintained by BP Ventures.

## Build and Test Commands

```bash
# Install dependencies (uses Poetry)
poetry install

# Run tests (requires .env file)
cp .env.example .env
poetry run pytest

# Run single test file
poetry run pytest polaris/tests/sep24/test_deposit.py

# Run tests with coverage
poetry run pytest --cov=polaris

# Lint
poetry run pylint polaris --load-plugins pylint_django

# Format
poetry run black polaris

# Build docker image
make docker-build

# Issue testnet asset
python manage.py testnet issue --asset CODE --issuer-seed S... --distribution-seed S...

# Run dev server (use --nostatic when whitenoise is configured)
python manage.py runserver --nostatic
```

## Architecture

### Core Package Structure

The `polaris/` package is the reusable Django app. The `server/` directory contains a reference implementation (deployed at testanchor.stellar.org) - not production-ready.

### SEP Modules

Each SEP has its own subpackage under `polaris/`:
- `sep1/` - stellar.toml (/.well-known/)
- `sep6/` - Programmatic deposit/withdrawal (/sep6/)
- `sep10/` - Authentication (/auth)
- `sep12/` - KYC (/kyc/)
- `sep24/` - Interactive deposit/withdrawal (/sep24/)
- `sep31/` - Cross-border payments (/sep31/)
- `sep38/` - Quotes (/sep38/)

URL routing is conditional based on `ACTIVE_SEPS` environment variable.

### Integration Pattern

Anchors extend Polaris by subclassing integration classes and registering them in their Django app's `AppConfig.ready()`:

```python
from polaris.integrations import register_integrations

register_integrations(
    deposit=MyDepositIntegration(),
    withdrawal=MyWithdrawalIntegration(),
    customer=MyCustomerIntegration(),
    rails=MyRailsIntegration(),
    custody=MyCustodyIntegration(),
    quote=MyQuoteIntegration(),
    toml=my_toml_func,
    fee=my_fee_func,
    sep6_info=my_info_func,
)
```

Key integration classes in `polaris/integrations/`:
- `DepositIntegration` / `WithdrawalIntegration` - SEP-6/24 flows
- `RailsIntegration` - Off-chain payment processing
- `CustodyIntegration` - Key management and transaction signing
- `CustomerIntegration` - SEP-12 KYC
- `SEP31ReceiverIntegration` - Cross-border receiving
- `QuoteIntegration` - SEP-38 pricing

### Models

Core models in `polaris/models.py`:
- `Asset` - On-chain asset configuration with encrypted `distribution_seed`
- `Transaction` - Tracks deposit/withdrawal/send transactions with status lifecycle
- `Quote` - SEP-38 firm/indicative quotes
- `OffChainAsset` / `DeliveryMethod` / `ExchangePair` - SEP-38 off-chain asset support

### Management Commands

Background processing commands in `polaris/management/commands/`:
- `process_pending_deposits` - Polls for pending deposits, submits to Stellar
- `watch_transactions` - Watches Stellar for incoming transactions
- `execute_outgoing_transactions` - Processes withdrawals
- `poll_outgoing_transactions` - Polls for outgoing transaction status

### Configuration

Settings are loaded from environment variables (`.env` file) or Django settings prefixed with `POLARIS_`. Key settings:
- `ACTIVE_SEPS` - Comma-separated list of enabled SEPs
- `SIGNING_SEED` - SEP-10 signing key (signs challenge transactions)
- `SERVER_JWT_KEY` - JWT signing key (verifies token payload integrity)
- `HOST_URL` - Anchor's public URL (must include protocol)
- `HORIZON_URI` - Stellar Horizon server
- `STELLAR_NETWORK_PASSPHRASE` - Network passphrase
- `LOCAL_MODE` - Set truthy for HTTP development (production requires HTTPS)
- `SEP10_HOME_DOMAINS` - List of domains to issue auth tokens for

### SEP-24 Django Settings Requirements

Required middleware order in `settings.py`:
```python
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves static files
    "django.contrib.sessions.middleware.SessionMiddleware",
    "polaris.middleware.TimezoneMiddleware",  # must be after SessionMiddleware
    ...
]
```

Other required settings:
```python
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"  # allows template overrides
SESSION_COOKIE_SECURE = True  # required in production (not LOCAL_MODE)
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
```

Run `python manage.py collectstatic --no-input` after changes.

## SEP-24 Form Processing Flow

The interactive webview flow for deposits/withdrawals:

1. `form_for_transaction()` - Return Django form to display (or `None` when done)
2. `content_for_template()` - Return template context dict (or `None` when done)
3. Polaris renders template with form
4. User submits → Polaris calls `form_for_transaction()` again with POST data
5. If `Form.is_valid()`: call `after_form_validation()` to update state
6. Repeat until both methods return `None` → redirect to "more info" page

Use `TransactionForm` (from `polaris.integrations`) for amount collection - includes proper validations.

### External Interactive Flow

To use an external UI instead of Django templates:
- Return external URL from `interactive_url()`
- External app completes flow, then calls: `GET /sep24/transactions/<deposit|withdraw>/interactive/complete?transaction_id=...`
- Implement `after_interactive_flow()` to update Transaction with collected data

## SEP-1 stellar.toml

Two options:
1. **Code**: Register `toml` function returning dict with `register_integrations(toml=my_func)`
2. **Static file**: Place in `static/polaris/stellar.toml` (or `local-stellar.toml` when `LOCAL_MODE` is truthy)

## Template Customization

Template inheritance: `base.html` → `deposit.html`, `withdraw.html`, `more_info.html`

Override by creating matching path in your app's `templates/polaris/` directory.

Available blocks for extension:
- `extra_head` / `extra_body` - Add CSS/JS (intentionally empty)
- `body_scripts` - Override Polaris JS
- `header`, `content`, `footer` - Main content areas

Return `{"template_name": "path/to/template.html"}` from `content_for_template()` to use completely custom templates.
