from django.urls import path

from polaris.sep45.views import SEP45Auth


urlpatterns = [path("auth", SEP45Auth.as_view())]
