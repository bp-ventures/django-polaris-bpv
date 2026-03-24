from logging import getLogger

from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from polaris.integrations import registered_external_account_integration as eai
from polaris.utils import render_error_response

logger = getLogger(__name__)

VALID_KINDS = ("crypto_address", "virtual_account", "bank_account")


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def info(request: Request) -> Response:
    offerings = eai.get_offerings(request=request)
    try:
        for i, offering in enumerate(offerings):
            _validate_offering(offering, context=f" (offering index {i})")
    except ValueError as e:
        logger.error(str(e))
        return render_error_response("internal server error", status_code=500)
    return Response({"offerings": offerings})


def _validate_offering(offering: dict, context: str = ""):
    """Validate offering dict matches SEP-58 /info spec."""
    if not isinstance(offering, dict):
        raise ValueError(f"integration returned non-dict offering{context}")
    if "kind" not in offering:
        raise ValueError(f"offering missing 'kind'{context}")
    kind = offering["kind"]
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid offering kind: {kind}{context}")
    if kind in ("virtual_account", "bank_account"):
        for f in ("country_code", "currency", "rail"):
            if f not in offering:
                raise ValueError(f"offering kind={kind} missing '{f}'{context}")
    elif kind == "crypto_address":
        for f in ("chain_id", "network"):
            if f not in offering:
                raise ValueError(f"offering kind=crypto_address missing '{f}'{context}")
