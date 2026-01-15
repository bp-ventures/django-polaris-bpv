# Native XLM Support Plan

## Overview

Add support for native XLM as a deposit/withdrawal asset using `issuer="native"` as a sentinel value in the Asset model.

## Current Limitation

The `Asset` model requires an issuer field with `MinLengthValidator(56)`, which doesn't accommodate native XLM (no issuer on Stellar network).

## Approach

Use `issuer="native"` as a sentinel value to indicate the native Stellar asset.

---

## Breakage Analysis

### Critical (Will Fail)

#### 1. `polaris/utils.py:435` - Payment Transaction Building

```python
asset = StellarAsset(code=transaction.asset.code, issuer=transaction.asset.issuer)
```

**Problem**: Stellar SDK's `Asset()` constructor with `issuer="native"` will fail validation. For native XLM, you need `Asset.native()` instead.

**Fix**:
```python
if transaction.asset.issuer == "native":
    asset = StellarAsset.native()
else:
    asset = StellarAsset(code=transaction.asset.code, issuer=transaction.asset.issuer)
```

#### 2. `polaris/utils.py:139-152` - Trustline Check

```python
if transaction.asset.issuer == asset_issuer:  # comparing to Horizon response
```

**Problem**: Horizon API returns `asset_type: "native"` without `asset_issuer` field for XLM. This comparison will never match.

**Fix**: Skip trustline check entirely for native assets (they don't need trustlines):
```python
def is_pending_trust(transaction, json_resp):
    if transaction.asset.issuer == "native":
        return False  # XLM doesn't need trustlines
    # ... rest of logic
```

#### 3. `polaris/management/commands/watch_transactions.py:271,313,323,327` - Incoming Payment Matching

```python
payment_data["issuer"] == want_asset.issuer
```

**Problem**: When matching incoming XLM payments, `operation.asset.issuer` from SDK is `None` for native assets, not `"native"`.

**Fix**: Handle native asset comparison:
```python
if want_asset.issuer == "native":
    matches = payment_data["issuer"] is None and payment_data["code"] == "XLM"
else:
    matches = payment_data["issuer"] == want_asset.issuer
```

---

### API Response Issues (Won't Crash, But Incorrect)

#### 4. `polaris/sep38/info.py:19` - SEP-38 Asset Format

```python
{"asset": f"stellar:{asset.code}:{asset.issuer}"}  # Returns "stellar:XLM:native"
```

**Problem**: SEP-38 spec expects `stellar:native` for XLM, not `stellar:XLM:native`.

**Fix**:
```python
if asset.issuer == "native":
    info_data["assets"].append({"asset": "stellar:native"})
else:
    info_data["assets"].append({"asset": f"stellar:{asset.code}:{asset.issuer}"})
```

#### 5. `polaris/integrations/toml.py:37` - stellar.toml CURRENCIES

```python
{"code": asset.code, "issuer": asset.issuer}  # Returns issuer: "native"
```

**Problem**: SEP-1 stellar.toml shouldn't include `issuer` field for XLM.

**Fix**: Conditionally exclude issuer for native.

#### 6. `polaris/models.py:271` - Asset Identification Format

```python
return f"stellar:{self.code}:{self.issuer}"  # Returns "stellar:XLM:native"
```

**Fix**: Return `"stellar:native"` for XLM.

#### 7. `polaris/models.py:784` - Transaction asset_name Property

```python
return self.asset.code + ":" + self.asset.issuer  # Returns "XLM:native"
```

Less critical but looks odd in logs/responses.

---

## Summary Table

| Location | Severity | Issue |
|----------|----------|-------|
| `utils.py:435` | **CRITICAL** | SDK Asset construction fails |
| `utils.py:139-152` | **CRITICAL** | Trustline check always fails |
| `watch_transactions.py:271` | **CRITICAL** | Payment matching fails |
| `sep38/info.py:19` | Medium | Wrong SEP-38 format |
| `integrations/toml.py:37` | Medium | Invalid stellar.toml |
| `models.py:271` | Medium | Wrong asset ID format |
| `models.py:784` | Low | Odd display in logs |

---

## Implementation Checklist

- [ ] Add `is_native` property to Asset model
- [ ] Update `asset_identification_format` property in Asset model
- [ ] Update `is_pending_trust()` in `utils.py`
- [ ] Update `create_deposit_envelope()` in `utils.py`
- [ ] Update `_check_for_payment_match()` in `watch_transactions.py`
- [ ] Update `_get_payment_values()` in `watch_transactions.py`
- [ ] Update SEP-38 info endpoint
- [ ] Update stellar.toml integration
- [ ] Update Transaction `asset_name` property
- [ ] Remove or adjust issuer MinLengthValidator for "native" value
- [ ] Add tests for native XLM deposit/withdrawal flows
