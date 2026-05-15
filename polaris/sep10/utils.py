from jwt import decode
from jwt.exceptions import InvalidTokenError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from polaris import settings
from polaris.sep10.token import SEP10Token
from polaris.utils import render_error_response


def check_auth(request, func, *args, **kwargs):
    """
    Check SEP 10 authentication in a request.
    Else call the original view function.
    """
    try:
        token = validate_jwt_request(request)
    except (ValueError, TypeError) as e:
        if "sep6/" in request.path:
            return Response({"type": "authentication_required"}, status=403)
        else:
            return render_error_response(str(e), status_code=status.HTTP_403_FORBIDDEN)
    return func(token, request, *args, **kwargs)


def validate_sep10_token():
    """Decorator to validate the SEP 10 token in a request."""

    def decorator(view):
        def wrapper(request, *args, **kwargs):
            return check_auth(request, view, *args, **kwargs)

        return wrapper

    return decorator


def validate_jwt_request(request: Request) -> SEP10Token:
    """
    Validate the JSON web token in a request and return the source account address.

    Tries ``SERVER_JWT_KEY`` (SEP-10) first, then falls back to ``SEP45_JWT_SECRET``
    (SEP-45) when sep-45 is active. The returned :class:`SEP10Token` is the same
    shape regardless of which protocol signed the JWT.

    :raises ValueError: invalid JWT
    """
    # While the SEP 24 spec calls the authorization header "Authorization", django middleware
    # renames this as "HTTP_AUTHORIZATION". We check this header for the JWT.
    jwt_header = request.headers.get("Authorization")
    if not jwt_header:
        raise ValueError("JWT must be passed as 'Authorization' header")
    bad_format_error = ValueError(
        "'Authorization' header must be formatted as 'Bearer <token>'"
    )
    if "Bearer" not in jwt_header:
        raise bad_format_error
    try:
        encoded_jwt = jwt_header.split(" ")[1]
    except IndexError:
        raise bad_format_error
    if not encoded_jwt:
        raise bad_format_error

    payload = _decode_jwt(encoded_jwt)
    try:
        return SEP10Token(payload)
    except ValueError as e:
        raise ValueError(f"SEP-10 token error: {str(e)}")


def _decode_jwt(encoded_jwt: str) -> dict:
    last_error: InvalidTokenError | None = None
    if settings.SERVER_JWT_KEY:
        try:
            return decode(encoded_jwt, settings.SERVER_JWT_KEY, algorithms=["HS256"])
        except InvalidTokenError as exc:
            last_error = exc
    if "sep-45" in settings.ACTIVE_SEPS and settings.SEP45_JWT_SECRET:
        try:
            return decode(
                encoded_jwt, settings.SEP45_JWT_SECRET, algorithms=["HS256"]
            )
        except InvalidTokenError as exc:
            last_error = exc
    if last_error is None:
        raise ValueError("SEP-10 token error: no JWT secret configured")
    raise ValueError(f"SEP-10 token error: unable to decode jwt{last_error}")
