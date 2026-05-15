"""SEP-45 ``/sep45/auth`` endpoint.

Thin view: parse request → call helpers in :mod:`polaris.sep45.utils` → render response.
All protocol mechanics live in ``utils.py``.
"""

import logging

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from polaris import settings
from polaris.sep45 import utils
from polaris.utils import render_error_response


logger = logging.getLogger(__name__)


class SEP45Auth(APIView):
    """Issue and verify SEP-45 contract-account challenges."""

    parser_classes = [JSONParser, MultiPartParser, FormParser]
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]

    def get(self, request: Request) -> Response:
        try:
            account = (request.GET.get("account") or "").strip()
            if not account:
                raise utils.SEP45ValidationError("'account' is required")
            utils.validate_contract_account(account)

            home_domain = utils.select_home_domain(
                request.GET.get("home_domain"), settings.SEP10_HOME_DOMAINS
            )
            web_auth_domain = utils.resolve_web_auth_domain(request.get_host())

            client_domain = request.GET.get("client_domain")
            client_domain_account = None
            if client_domain:
                utils.validate_hostname(client_domain, "client_domain")
                client_domain_account = utils.get_client_domain_signing_key(
                    client_domain
                )

            nonce = utils.issue_nonce()
            args_map = utils.build_args_map(
                account=account,
                home_domain=home_domain,
                web_auth_domain=web_auth_domain,
                web_auth_domain_account=settings.SIGNING_KEY,
                nonce=nonce,
                client_domain=client_domain,
                client_domain_account=client_domain_account,
            )
            entries = utils.build_challenge_entries(args_map)
            logger.info(
                "sep45 event=challenge_issued account=%s home_domain=%s client_domain=%s",
                account,
                home_domain,
                client_domain,
            )
            return Response(
                {
                    "authorization_entries": utils.encode_auth_entries(entries),
                    "network_passphrase": settings.STELLAR_NETWORK_PASSPHRASE,
                }
            )
        except utils.SEP45ValidationError as exc:
            logger.info("sep45 event=challenge_failed error=%s", exc)
            return render_error_response(
                str(exc), status_code=status.HTTP_400_BAD_REQUEST
            )

    def post(self, request: Request) -> Response:
        try:
            entries_b64 = self._read_authorization_entries(request)
            if not entries_b64:
                raise utils.SEP45ValidationError(
                    "'authorization_entries' is required"
                )
            if len(entries_b64) > utils.MAX_AUTHORIZATION_ENTRIES_SIZE:
                raise utils.SEP45ValidationError(
                    "'authorization_entries' exceeds maximum size"
                )

            entries = utils.decode_auth_entries(entries_b64)
            expected_web_auth_domain = utils.resolve_web_auth_domain(
                request.get_host()
            )
            current_ledger = utils.get_latest_ledger_sequence()
            args_native, args_map = utils.validate_auth_entries(
                entries, expected_web_auth_domain, current_ledger
            )

            nonce = str(args_native["nonce"])
            if not utils.consume_nonce(nonce):
                raise utils.SEP45ValidationError("invalid or expired nonce")

            utils.verify_entries_with_simulation(args_map, entries)
            token = utils.issue_sep45_jwt(args_native, entries_b64)
            logger.info(
                "sep45 event=token_issued account=%s home_domain=%s client_domain=%s",
                args_native.get("account"),
                args_native.get("home_domain"),
                args_native.get("client_domain"),
            )
            return Response({"token": token})
        except utils.SEP45ValidationError as exc:
            logger.info("sep45 event=token_failed error=%s", exc)
            return render_error_response(
                str(exc), status_code=status.HTTP_400_BAD_REQUEST
            )

    @staticmethod
    def _read_authorization_entries(request: Request) -> str:
        value = request.data.get("authorization_entries") if request.data else None
        if value is None:
            value = request.POST.get("authorization_entries")
        return (value or "").strip()
