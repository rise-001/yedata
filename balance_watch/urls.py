from django.urls import path
from monitor import views

urlpatterns = [
    path('api/login',         views.api_login),
    path('api/logout',        views.api_logout),
    path('api/session',       views.api_session),
    path('api/sites',         views.api_sites),
    path('api/check',         views.api_check),
    path('api/usage/token',   views.api_token_usage),
    path('api/check-many',    views.api_check_many),
    path('api/notify-test',   views.api_notify_test),
    path('api/scheduler',     views.api_scheduler),
    path('api/health',        views.api_health),
    path('',                  views.serve_static, {'path': ''}),
    path('<path:path>',       views.serve_static),
]
