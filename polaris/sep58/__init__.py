VALID_KINDS = ("crypto_address", "virtual_account", "bank_account")
VALID_STATUSES = ("pending", "active", "deactivated", "error")

# Required fields per kind for both offerings and account responses
KIND_REQUIRED_FIELDS = {
    "crypto_address": ("chain_id", "network"),
    "virtual_account": ("country_code", "currency", "rail"),
    "bank_account": ("country_code", "currency", "rail"),
}


def validate_kind_fields(obj: dict, kind: str, context: str = ""):
    """Validate kind-specific required fields are present in a dict."""
    for f in KIND_REQUIRED_FIELDS.get(kind, ()):
        if f not in obj:
            raise ValueError(f"kind={kind} missing '{f}'{context}")
