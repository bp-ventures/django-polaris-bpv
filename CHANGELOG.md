# Changelog

All notable changes to django-polaris-bpv will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.0] - 2026-03-22

### Changed
- Raised minimum Django version from 4.2 to 5.0
- Replaced `pytz` with `zoneinfo` (Python 3.9+ stdlib) throughout the codebase
- Fixed `CrossOriginMiddleware` to reflect request origin instead of wildcard `*`
  when credentials are enabled (CORS spec compliance)
- Fixed `CrossOriginMiddleware` cookie handling to use Django's `response.cookies`
  API instead of fragile raw `Set-Cookie` header manipulation
- Updated documentation links and branding to django-polaris-bpv
- Expanded CI test matrix to cover Python 3.10-3.13 and Django 5.0-5.1

### Fixed
- Fixed missing comma in `CrossOriginMiddleware.SEP24_URLS` that caused
  `/sep31/` and `/sep24/` path strings to concatenate
- Fixed `CrossOriginMiddleware` cookie loop bug where only the last cookie
  was processed due to incorrect indentation

### Removed
- Dropped `pytz` dependency
- Removed Django 4.2 classifier

[2.7.0]: https://github.com/bp-ventures/django-polaris-bpv/releases/tag/v2.7.0
