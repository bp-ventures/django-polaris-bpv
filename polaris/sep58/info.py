from logging import getLogger

from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from polaris.integrations import registered_external_account_integration as eai
from polaris.sep58 import VALID_KINDS, validate_kind_fields
from polaris.utils import render_error_response

logger = getLogger(__name__)


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
    validate_kind_fields(offering, kind, context)
