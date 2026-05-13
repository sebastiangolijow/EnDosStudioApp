from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "StickerApp Admin"
admin.site.site_title = "StickerApp"
admin.site.index_title = "Operations"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path(
        "api/v1/",
        include(
            [
                path("", include("apps.core.urls")),
                path("auth/", include("apps.users.auth_urls")),
                path("users/", include("apps.users.urls")),
                path("orders/", include("apps.orders.urls")),
                path("payments/", include("apps.payments.urls")),
                path("products/", include("apps.products.urls")),
                path("categories/", include("apps.products.category_urls")),
                path("discounts/", include("apps.discounts.urls")),
            ]
        ),
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
