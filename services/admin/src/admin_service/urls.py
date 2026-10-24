"""Маршруты админки. Health-пробы обслуживает ``HealthMiddleware`` (до проверки Host)."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.staticfiles.views import serve
from django.urls import include, path, re_path

from admin_service.core import auth_views, converters
from admin_service.core.forms import IdentityLoginForm

admin.site.site_header = "Guessverse — админка"
admin.site.site_title = "Guessverse"
admin.site.index_title = "Разделы"
admin.site.login_form = IdentityLoginForm

# До include(): маршруты разделов используют конвертеры modekey, isodate и roomcode.
converters.register()

urlpatterns = [
    # Раньше admin.site.urls: те же пути (и имена admin:login/logout ведут сюда же),
    # но с лимитом попыток входа по IP и отзывом сессии identity при выходе.
    path("login/", auth_views.login),
    path("logout/", auth_views.logout),
    path("catalog/", include("admin_service.apps.catalog.urls")),
    path("users/", include("admin_service.apps.users.urls")),
    path("game/modes/", include("admin_service.apps.game_config.urls")),
    path("game/daily/", include("admin_service.apps.game_daily.urls")),
    path("collections/", include("admin_service.apps.collections.urls")),
    path("matches/", include("admin_service.apps.matches.urls")),
    path("rating/", include("admin_service.apps.rating.urls")),
    path("progression/", include("admin_service.apps.progression.urls")),
    path("shop/", include("admin_service.apps.shop.urls")),
    path("ugc/", include("admin_service.apps.ugc.urls")),
    path("broadcasts/", include("admin_service.apps.notifications.urls")),
    path("social/", include("admin_service.apps.social.urls")),
    # Статика django-admin прямо из пакетов (без collectstatic и отдельного сервера):
    # админка внутренняя и малонагруженная, а общий Dockerfile не меняем (D7).
    re_path(r"^static/(?P<path>.*)$", serve, {"insecure": True}),
    path("", admin.site.urls),
]
