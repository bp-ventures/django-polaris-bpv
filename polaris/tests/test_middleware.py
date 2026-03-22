import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from polaris.middleware import CrossOriginMiddleware


def make_middleware(response=None):
    """Create a CrossOriginMiddleware with a simple get_response callable."""
    if response is None:
        response = HttpResponse("ok")

    def get_response(request):
        return response

    return CrossOriginMiddleware(get_response)


@pytest.fixture
def rf():
    return RequestFactory()


class TestCrossOriginMiddlewareOrigin:
    def test_origin_reflected_when_present(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test", HTTP_ORIGIN="https://demo-wallet.stellar.org")
        response = middleware(request)

        assert response["Access-Control-Allow-Origin"] == "https://demo-wallet.stellar.org"
        assert response["Access-Control-Allow-Credentials"] == "true"

    def test_wildcard_when_no_origin(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test")
        response = middleware(request)

        assert response["Access-Control-Allow-Origin"] == "*"
        assert not response.has_header("Access-Control-Allow-Credentials")

    def test_non_sep_url_no_headers(self, rf):
        middleware = make_middleware()
        request = rf.get("/some/other/path", HTTP_ORIGIN="https://example.com")
        response = middleware(request)

        assert not response.has_header("Access-Control-Allow-Origin")
        assert not response.has_header("Access-Control-Allow-Credentials")


class TestCrossOriginMiddlewareCookies:
    def test_cookie_samesite_set_to_none(self, rf):
        resp = HttpResponse("ok")
        resp.set_cookie("sessionid", "abc123")
        resp.set_cookie("csrftoken", "xyz789")
        middleware = make_middleware(resp)
        request = rf.get("/sep24/test")
        response = middleware(request)

        for cookie_name in ["sessionid", "csrftoken"]:
            assert response.cookies[cookie_name]["samesite"] == "None"
            assert response.cookies[cookie_name]["secure"] is True

    def test_no_cookies_no_error(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test")
        response = middleware(request)
        assert len(response.cookies) == 0


class TestCrossOriginMiddlewareHeaders:
    def test_coop_coep_headers(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test")
        response = middleware(request)

        assert response["Cross-Origin-Opener-Policy"] == "unsafe-none"
        assert response["Cross-Origin-Embedder-Policy"] == "unsafe-none"
        assert response["Cross-Origin-Resource-Policy"] == "cross-origin"

    def test_vary_origin(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test")
        response = middleware(request)

        assert response["Vary"] == "Origin"

    def test_cache_control_headers(self, rf):
        middleware = make_middleware()
        request = rf.get("/sep24/test")
        response = middleware(request)

        assert "no-store" in response["Cache-Control"]
        assert response["Pragma"] == "no-cache"

    @pytest.mark.parametrize("path", [
        "/sep24/test",
        "/sep31/test",
        "/sep6/test",
        "/transactions/withdraw",
        "/transactions/deposit",
        "/transaction",
        "/more_info",
    ])
    def test_all_sep_urls_processed(self, rf, path):
        middleware = make_middleware()
        request = rf.get(path)
        response = middleware(request)

        assert response.has_header("Access-Control-Allow-Origin")
