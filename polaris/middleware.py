from zoneinfo import ZoneInfo

from django.utils import timezone


class TimezoneMiddleware:
    """
    .. _timezones: https://docs.djangoproject.com/en/stable/topics/i18n/timezones/

    Adapted from the Django documentation on timezones_. It checks for a
    ``"timezone"`` key stored in the request session and uses it when rendering
    content returned in the response.

    Polaris includes a ``timezone.js`` script that detects the users' UTC offset
    and sends it to the server, which stores a timezone with that offset in the
    user's session. This script is automatically loaded if using a template that
    inherits from ``base.html``.

    However, there is a limitation with this approach. For users's without exising
    sessions, which are identified using a browser cookie, Polaris cannot detect the
    user's timezone prior to rendering the first page of content. This means that
    dates and times shown to on the first page to a new user will be in the default
    timezone specified in your project's settings.

    That is why Django's documentation recommends that you simply ask the user what
    timezone they would like to use instead of attempting to detect it automatically.
    If this approach is taken, simply save the specified timezone in the user's session
    under the ``"timezone"`` key after adding this middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = request.session.get("timezone")
        if tzname:
            timezone.activate(ZoneInfo(tzname))
        else:
            timezone.deactivate()
        return self.get_response(request)

class CrossOriginMiddleware:
    """
    Adds cross-origin headers to ensure proper popup detection in client
    applications like the Stellar Demo Wallet.

    When an anchor's interactive flow runs inside a popup opened by a
    client wallet, browsers enforce cross-origin restrictions that can
    prevent the client from detecting when the popup closes. This
    middleware sets the necessary CORS and COOP/COEP headers on SEP
    endpoint responses.

    If the request includes an Origin header, that origin is reflected
    back (required by the CORS spec when credentials are allowed).
    Cookie SameSite attributes are set to None with Secure to enable
    cross-origin cookie flow.

    Add this middleware to MIDDLEWARE after TimezoneMiddleware.
    """
    
    # These URL patterns should match the ones in the custom middleware
    SEP24_URLS = [
        '/sep31/',
        '/sep24/',
        '/sep6/',
        '/transactions/withdraw',
        '/transactions/deposit', 
        '/transaction',
        '/more_info'
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def should_process_url(self, path):
        """Check if the URL should be processed by this middleware"""
        return any(url_part in path for url_part in self.SEP24_URLS)
        
    def __call__(self, request):
        response = self.get_response(request)
        
        if self.should_process_url(request.path):
            origin = request.META.get('HTTP_ORIGIN')
            if origin:
                response['Access-Control-Allow-Origin'] = origin
                response['Access-Control-Allow-Credentials'] = 'true'
            else:
                response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Origin, Content-Type, Accept, Authorization'
            response['Access-Control-Expose-Headers'] = 'Content-Type, X-Requested-With'
            response['Cross-Origin-Embedder-Policy'] = 'unsafe-none'
            response['Cross-Origin-Opener-Policy'] = 'unsafe-none'
            response['Cross-Origin-Resource-Policy'] = 'cross-origin'
            response['Vary'] = 'Origin'

            for cookie_name in list(response.cookies.keys()):
                response.cookies[cookie_name]['samesite'] = 'None'
                response.cookies[cookie_name]['secure'] = True

            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            response['P3P'] = 'CP="This is not a P3P policy"'
            
        return response