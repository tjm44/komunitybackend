
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from django.views.generic.base import RedirectView

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=settings.STATIC_URL + 'images/favicon.png')),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('', include('wallet.urls')),
    path('api/v1/', include('api_v1.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('dashboard/crm/', include('crm.urls')),
]

if settings.DEBUG:
    urlpatterns.append(path("__reload__/", include("django_browser_reload.urls")))
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
