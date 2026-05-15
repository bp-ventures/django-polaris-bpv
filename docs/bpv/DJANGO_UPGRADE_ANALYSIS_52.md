# Django Upgrade Analysis: 4.2 to 5.2 LTS / 6.0

**Date:** January 2026
**Current Version:** Django >=4.2
**Target Versions:** Django 5.2 LTS or Django 6.0

## Executive Summary

The Django Polaris codebase is **mostly compatible** with Django 5.x/6.0, but has one **critical blocker**: active `pytz` usage that must be migrated to Python's `zoneinfo` module. Django 5.0+ completely removed pytz support.

**Recommendation:** Upgrade to **Django 5.2 LTS** (step upgrade). Django 6.0 requires Python 3.12+ which would drop support for Python 3.10/3.11.

---

## Version Requirements Matrix

| Version | Python Requirement | Support Status | Release Date |
|---------|-------------------|----------------|--------------|
| Django 4.2 LTS | 3.8+ | Security fixes until Apr 2026 | Apr 2023 |
| Django 5.0 | 3.10+ | Superseded | Dec 2023 |
| Django 5.1 | 3.10+ | Superseded | Aug 2024 |
| Django 5.2 LTS | 3.10+ | Mainstream support until Dec 2027 | Apr 2025 |
| Django 6.0 | 3.12+ | Current | Dec 2025 |

**Current pyproject.toml:** `python = "^3.10"` (compatible with 5.2 LTS, NOT 6.0)

---

## Historical Upgrade Issues

### Django 4.x Upgrade (2025)

The previous Django 4.2 upgrade (commit `a909eb7`) encountered **cross-origin popup detection issues** that broke the Stellar Demo Wallet integration. This required adding `CrossOriginMiddleware` in `polaris/middleware.py:41-105` to:

- Add CORS headers for SEP-24 URLs
- Set `Cross-Origin-Opener-Policy: unsafe-none`
- Modify cookies: `SameSite=Lax` → `SameSite=None; Secure`

**Lesson:** Django security changes can break wallet integrations. Thorough testing with demo-wallet.stellar.org is essential.

---

## Critical Blocker: pytz Usage

**Django 5.0 completely removed pytz support.** The codebase has active pytz usage in 3 files:

### 1. `polaris/middleware.py:36`
```python
import pytz
# ...
timezone.activate(pytz.timezone(tzname))
```

### 2. `polaris/sep24/tzinfo.py:28`
```python
import pytz
# ...
for tz in map(pytz.timezone, pytz.all_timezones_set):
    if now.astimezone(tz).utcoffset() == offset:
```

### 3. `polaris/sep24/utils.py:373`
```python
import pytz
# ...
datetime.now().astimezone(pytz.timezone(timezone)).utcoffset().total_seconds()
```

### Required Migration

```python
# BEFORE (pytz)
import pytz
tz = pytz.timezone('America/New_York')
pytz.all_timezones_set

# AFTER (zoneinfo - Python 3.9+)
from zoneinfo import ZoneInfo
import zoneinfo
tz = ZoneInfo('America/New_York')
zoneinfo.available_timezones()
```

**Note:** `zoneinfo` requires `tzdata` package on Windows.

---

## Compatibility Analysis

### Fully Compatible (No Changes Required)

| Component | Status | Notes |
|-----------|--------|-------|
| URL patterns | ✅ | Uses modern `django.urls.path` and `re_path` |
| Middleware pattern | ✅ | Uses `__init__(get_response)` / `__call__` |
| Models | ✅ | No `index_together`, uses proper field types |
| Forms | ✅ | Standard Django forms |
| Admin | ✅ | Modern `ModelAdmin` patterns |
| Management commands | ✅ | Modern `BaseCommand` |
| DRF integration | ✅ | Compatible with Django 5.x |
| Translation | ✅ | Uses `gettext`/`gettext_lazy` (not deprecated `ugettext`) |
| Timezone handling | ✅ | Uses `datetime.timezone.utc` (not `django.utils.timezone.utc`) |

### Requires Changes

| Component | Status | Action Required |
|-----------|--------|-----------------|
| pytz usage | ❌ | Migrate to `zoneinfo` |
| `pytz` dependency | ❌ | Remove from pyproject.toml |
| pyproject.toml classifiers | ⚠️ | Add Django 5.x classifiers |
| Documentation | ⚠️ | Update `STATICFILES_STORAGE` to `STORAGES` dict |

### Test File Changes

`polaris/tests/sep24/test_tzinfo.py` uses `pytz` and must be updated.

---

## Django 5.x Breaking Changes Relevant to Polaris

### Settings Changes

| Setting | Django 4.2 | Django 5.0+ |
|---------|------------|-------------|
| `USE_L10N` | Optional | Removed |
| `STATICFILES_STORAGE` | Works | Deprecated (use `STORAGES["staticfiles"]`) |
| `DEFAULT_FILE_STORAGE` | Works | Deprecated (use `STORAGES["default"]`) |
| `CSRF_COOKIE_MASKED` | Works | Removed |

**Polaris impact:** Documentation mentions `STATICFILES_STORAGE` - should be updated.

### Form Rendering

Django 5.0 changed default form rendering from table-based to div-based. Polaris uses custom templates, so this should not affect functionality but may affect styling.

### Admin Logout

GET requests for logout are no longer supported (must use POST). Polaris admin should be tested.

---

## Django 6.0 Considerations

If targeting Django 6.0:

1. **Python 3.12+ required** - Would drop Python 3.10/3.11 support
2. **`DEFAULT_AUTO_FIELD`** - Defaults to `BigAutoField` (was `AutoField`)
3. **URLField** - HTTPS assumed by default
4. **`Model.save()` positional args** - Must use keyword arguments

**Recommendation:** Not recommended at this time. Django 5.2 LTS provides support until Dec 2027.

---

## Recommended Upgrade Path

### Phase 1: Prepare (Current Django 4.2)

1. **Run deprecation warnings:**
   ```bash
   python -Wd manage.py test
   ```

2. **Install django-upgrade tool:**
   ```bash
   pip install django-upgrade
   django-upgrade --target-version 5.2 polaris/**/*.py
   ```

3. **Update pyproject.toml:**
   - Remove `pytz = "*"` dependency
   - Add `tzdata = {version = "*", markers = "sys_platform == 'win32'"}` for Windows

### Phase 2: Migrate pytz to zoneinfo

**File: `polaris/middleware.py`**
```python
# Before
import pytz
timezone.activate(pytz.timezone(tzname))

# After
from zoneinfo import ZoneInfo
timezone.activate(ZoneInfo(tzname))
```

**File: `polaris/sep24/tzinfo.py`**
```python
# Before
import pytz
for tz in map(pytz.timezone, pytz.all_timezones_set):

# After
from zoneinfo import ZoneInfo, available_timezones
for tz_name in available_timezones():
    tz = ZoneInfo(tz_name)
```

**File: `polaris/sep24/utils.py`**
```python
# Before
import pytz
datetime.now().astimezone(pytz.timezone(timezone))

# After
from zoneinfo import ZoneInfo
datetime.now().astimezone(ZoneInfo(timezone))
```

### Phase 3: Update Dependencies

**pyproject.toml changes:**
```toml
[tool.poetry.dependencies]
django = ">=5.2"  # Was ">=4.2"
# Remove: pytz = "*"
tzdata = {version = "*", markers = "sys_platform == 'win32'"}

[tool.poetry]
classifiers = [
    # Add:
    "Framework :: Django :: 5.2",
]
```

### Phase 4: Update Documentation

Update `docs/sep-24.rst` and `CLAUDE.md` to use `STORAGES` instead of `STATICFILES_STORAGE`:

```python
# Before
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# After
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
```

### Phase 5: Testing

1. **Run full test suite:**
   ```bash
   poetry run pytest
   ```

2. **Test with demo wallet:**
   - Start server: `python manage.py runserver --nostatic`
   - Test at: https://demo-wallet.stellar.org
   - Test SEP-24 deposit/withdrawal flows
   - Verify popup detection works (historical issue area)

3. **Test timezone detection:**
   - Verify timezone middleware works
   - Test users in different timezones

4. **Test admin interface:**
   - Verify logout works (POST method)
   - Check form rendering

---

## Estimated Effort

| Task | Effort | Risk |
|------|--------|------|
| pytz → zoneinfo migration | 2-4 hours | Medium |
| pyproject.toml updates | 30 min | Low |
| Documentation updates | 1 hour | Low |
| Testing & validation | 4-8 hours | Medium |
| **Total** | **8-14 hours** | **Medium** |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| pytz migration breaks timezone detection | Medium | High | Comprehensive timezone tests |
| Cross-origin issues (like Django 4.x) | Low | High | Test with demo wallet |
| Third-party package incompatibility | Low | Medium | Check stellar-sdk, DRF compatibility |
| Form rendering changes | Low | Low | Visual inspection of interactive flow |

---

## Recommendation

**Upgrade to Django 5.2 LTS** with the following rationale:

1. **Long-term support** - Maintained until December 2027
2. **Python compatibility** - Works with current Python 3.10+ requirement
3. **Stability** - Not bleeding edge, well-tested
4. **Migration path** - Can upgrade to Django 6.x later when ready to drop Python 3.10/3.11

**Do NOT upgrade to Django 6.0 yet** because:
1. Requires Python 3.12+ (would drop 3.10/3.11 users)
2. More breaking changes
3. No significant features needed for Polaris

---

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Django version, remove pytz, add tzdata, update classifiers |
| `polaris/middleware.py` | pytz → zoneinfo |
| `polaris/sep24/tzinfo.py` | pytz → zoneinfo |
| `polaris/sep24/utils.py` | pytz → zoneinfo |
| `polaris/tests/sep24/test_tzinfo.py` | pytz → zoneinfo |
| `docs/sep-24.rst` | STATICFILES_STORAGE → STORAGES |
| `CLAUDE.md` | STATICFILES_STORAGE → STORAGES |
| `docs/requirements.txt` | Remove pytz |

---

## References

- [Django 5.0 Release Notes](https://docs.djangoproject.com/en/5.2/releases/5.0/)
- [Django 5.2 Release Notes](https://docs.djangoproject.com/en/5.2/releases/5.2/)
- [Django Deprecation Timeline](https://docs.djangoproject.com/en/dev/internals/deprecation/)
- [Python zoneinfo documentation](https://docs.python.org/3/library/zoneinfo.html)
- [django-upgrade tool](https://github.com/adamchainz/django-upgrade)
