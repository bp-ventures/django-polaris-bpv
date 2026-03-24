from logging import getLogger

from rest_framework.decorators import api_view, parser_classes, renderer_classes
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from polaris.integrations import registered_external_account_integration as eai
from polaris.sep10.token import SEP10Token
from polaris.sep10.utils import validate_sep10_token
from polaris.sep58 import VALID_KINDS, VALID_STATUSES, validate_kind_fields
from polaris.utils import render_error_response

logger = getLogger(__name__)


@api_view(["GET", "POST"])
@parser_classes([JSONParser])
@renderer_classes([JSONRenderer])
@validate_sep10_token()
def accounts(token: SEP10Token, request: Request) -> Response:
    if request.method == "POST":
        return _create_account(token, request)
    return _list_accounts(token, request)


@api_view(["GET"])
@renderer_classes([JSONRenderer])
@validate_sep10_token()
def get_account(token: SEP10Token, request: Request, account_id: str) -> Response:
    try:
        account = eai.get_account(token=token, request=request, account_id=account_id)
    except ValueError as e:
        return render_error_response(str(e), status_code=404)
    except RuntimeError as e:
        return render_error_response(str(e), status_code=503)
    try:
        _validate_account_response(account)
    except ValueError as e:
        logger.error(str(e))
        return render_error_response("internal server error", status_code=500)
    return Response({"account": account})


def _create_account(token: SEP10Token, request: Request) -> Response:
    try:
        params = _validate_create_request(request)
    except ValueError as e:
        return render_error_response(str(e))

    try:
        result = eai.create_account(token=token, request=request, params=params)
    except ValueError as e:
        return render_error_response(str(e), status_code=400)
    except RuntimeError as e:
        return render_error_response(str(e), status_code=503)

    # KYC needed -- SEP-6 dict-return convention
    if isinstance(result, dict) and "type" in result:
        return Response(result, status=403)

    account, created = result
    try:
        _validate_account_response(account)
    except ValueError as e:
        logger.error(str(e))
        return render_error_response("internal server error", status_code=500)
    return Response({"account": account}, status=201 if created else 200)


def _list_accounts(token: SEP10Token, request: Request) -> Response:
    filters = {}
    for key in ("kind", "country_code", "status"):
        val = request.query_params.get(key)
        if val:
            filters[key] = val

    try:
        account_list = eai.list_accounts(token=token, request=request, filters=filters)
    except ValueError as e:
        return render_error_response(str(e), status_code=400)
    except RuntimeError as e:
        return render_error_response(str(e), status_code=503)

    try:
        for i, account in enumerate(account_list):
            _validate_account_response(account, context=f" (list index {i})")
    except ValueError as e:
        logger.error(str(e))
        return render_error_response("internal server error", status_code=500)

    return Response({"accounts": account_list})


def _validate_account_response(account: dict, context: str = ""):
    """Validate account dict returned by integration matches SEP-58 spec."""
    if not isinstance(account, dict):
        raise ValueError(f"integration returned non-dict account object{context}")
    required = ("id", "kind", "status", "stellar_asset", "stellar_account", "created_at", "updated_at")
    missing = [f for f in required if f not in account]
    if missing:
        raise ValueError(f"account missing required fields: {', '.join(missing)}{context}")
    if account["kind"] not in VALID_KINDS:
        raise ValueError(f"invalid account kind: {account['kind']}{context}")
    if account["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid account status: {account['status']}{context}")
    validate_kind_fields(account, account["kind"], context)
    if account["status"] == "active" and "instructions" not in account:
        raise ValueError(f"active account must include 'instructions'{context}")


def _validate_create_request(request: Request) -> dict:
    kind = request.data.get("kind")
    if not kind:
        raise ValueError("'kind' is required")
    if kind not in VALID_KINDS:
        raise ValueError(f"'kind' must be one of: {', '.join(VALID_KINDS)}")

    params = {"kind": kind}

    if kind in ("virtual_account", "bank_account"):
        for field in ("country_code", "currency"):
            val = request.data.get(field)
            if not val:
                raise ValueError(f"'{field}' is required for {kind}")
            params[field] = val
        rail = request.data.get("rail")
        if rail:
            params["rail"] = rail
    elif kind == "crypto_address":
        chain_id = request.data.get("chain_id")
        if not chain_id:
            raise ValueError("'chain_id' is required for crypto_address")
        params["chain_id"] = chain_id

    callback = request.data.get("on_change_callback")
    if callback:
        if not callback.startswith("https://"):
            raise ValueError("'on_change_callback' must be an HTTPS URL")
        params["on_change_callback"] = callback

    return params
