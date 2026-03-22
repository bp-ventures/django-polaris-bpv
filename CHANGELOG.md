# Changelog

All notable changes to django-polaris-bpv will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.0] - 2026-03-22

### Changed
- **Python 3.12+ required** — dropped support for Python 3.10 and 3.11
- **Django 6.0+ required** — dropped support for Django 4.2, 5.0, and 5.1
- **DRF 3.16+ required** — first version with Django 6.0 support
- Replaced `pytz` with `zoneinfo` (Python 3.9+ stdlib) throughout the codebase
- Fixed `CrossOriginMiddleware` to reflect request origin instead of wildcard `*`
  when credentials are enabled (CORS spec compliance)
- Fixed `CrossOriginMiddleware` cookie handling to use Django's `response.cookies`
  API instead of fragile raw `Set-Cookie` header manipulation
- Updated documentation links and branding to django-polaris-bpv
- Updated CI test matrix to Python 3.12–3.13 / Django 6.0
- Synced `polaris/__init__.py` version with `pyproject.toml` (both `2.7.0`)

### Fixed
- Fixed missing comma in `CrossOriginMiddleware.SEP24_URLS` that caused
  `/sep31/` and `/sep24/` path strings to concatenate
- Fixed `CrossOriginMiddleware` cookie loop bug where only the last cookie
  was processed due to incorrect indentation

### Removed
- Dropped `pytz` dependency
- Removed deprecated `USE_I18N` setting
- Removed Django 4.2/5.0/5.1 and Python 3.10/3.11 classifiers

## [2.6.0] - forked from SDF

- Initial BP Ventures fork of django-polaris
- Added `CrossOriginMiddleware` for popup detection in Stellar Demo Wallet
- Updated branding and documentation to reflect BP Ventures maintenance

[2.7.0]: https://github.com/bp-ventures/django-polaris-bpv/releases/tag/v2.7.0
[2.6.0]: https://github.com/bp-ventures/django-polaris-bpv/releases/tag/v2.6.0
