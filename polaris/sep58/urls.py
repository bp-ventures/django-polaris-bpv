from django.urls import re_path
from polaris.sep58 import info, accounts

urlpatterns = [
    re_path(r"^info/?$", info.info),
    re_path(r"^accounts/?$", accounts.accounts),
    re_path(r"^accounts/(?P<account_id>[^/]+)/?$", accounts.get_account),
]
