from typing import Union
from rest_framework.request import Request
from polaris.sep10.token import SEP10Token


class ExternalAccountIntegration:
    """
    Base class for SEP-58 External Account API integrations.
    Anchors subclass this and register via register_integrations(external_account=...).

    Error conventions:
        ValueError   -> 400 Bad Request
        RuntimeError -> 503 Service Unavailable

    KYC convention (same as SEP-6):
        Return {"type": "non_interactive_customer_info_needed", "fields": [...]}
        from create_account() instead of the normal tuple -> view returns 403.
    """

    def get_offerings(self, request: Request, *args, **kwargs) -> list:
        """
        Return list of offering dicts per SEP-58 /info spec.
        No authentication required. Each dict should contain:
        - kind: "crypto_address", "virtual_account", or "bank_account"
        - For virtual_account/bank_account: country_code, currency, rail
        - For crypto_address: chain_id, network
        """
        raise NotImplementedError()

    def create_account(self, token: SEP10Token, request: Request, params: dict, *args, **kwargs) -> Union[tuple, dict]:
        """
        Create or return existing account for the authenticated user.

        On success, return (account_dict, created: bool).
        - account_dict: full Account Object per SEP-58 spec
        - created: True if new account (201), False if reused existing (200)

        On KYC needed, return a dict with "type" key (SEP-6 convention):
        - {"type": "non_interactive_customer_info_needed", "fields": ["birth_date", ...]}

        params contains validated fields:
        - kind (always)
        - country_code, currency (for virtual_account/bank_account)
        - rail (optional, for virtual_account/bank_account)
        - chain_id (for crypto_address)
        - on_change_callback (optional)

        Raise ValueError for bad request (400).
        Raise RuntimeError for service unavailability (503).
        """
        raise NotImplementedError()

    def get_account(self, token: SEP10Token, request: Request, account_id: str, *args, **kwargs) -> dict:
        """
        Return single account dict for the authenticated user.
        Raise ValueError if not found (will return 404).
        """
        raise NotImplementedError()

    def list_accounts(self, token: SEP10Token, request: Request, filters: dict, *args, **kwargs) -> list:
        """
        Return list of account dicts for the authenticated user.
        filters may contain: kind, country_code, status (all optional).
        """
        raise NotImplementedError()


registered_external_account_integration = ExternalAccountIntegration()
