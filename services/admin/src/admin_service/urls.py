"""Маршруты админки. Health-пробы обслуживает ``HealthMiddleware`` (до проверки Host)."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.staticfiles.views import serve
from django.urls import include, path, re_path

from admin_service.core import auth_views
from admin_service.core.forms import IdentityLoginForm

admin.site.site_header = "Guessverse — админка"
admin.site.site_title = "Guessverse"
admin.site.index_title = "Разделы"
admin.site.login_form = IdentityLoginForm

urlpatterns = [
    # Раньше admin.site.urls: те же пути (и имена admin:login/logout ведут сюда же),
    # но с лимитом попыток входа по IP и отзывом сессии identity при выходе.
    path("login/", auth_views.login),
    path("logout/", auth_views.logout),
    path("catalog/", include("admin_service.apps.catalog.urls")),
    path("users/", include("admin_service.apps.users.urls")),
    # Статика django-admin прямо из пакетов (без collectstatic и отдельного сервера):
    # админка внутренняя и малонагруженная, а общий Dockerfile не меняем (D7).
    re_path(r"^static/(?P<path>.*)$", serve, {"insecure": True}),
    path("", admin.site.urls),
]
