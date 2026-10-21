"""
Session authentication middleware.
Reads `balance_session` cookie; injects `request.app_session` (dict or None).
"""
import time

from django.conf import settings
from django.http import HttpRequest


class SessionAuthMiddleware:
    def __init__(self, get_response):
        self._get_response = get_response

    def __call__(self, request: HttpRequest):
        from monitor.state import sessions
        token = request.COOKIES.get('balance_session', '')
        session = sessions.get(token) if token else None
        if session and session['expires_at'] <= time.time():
            sessions.pop(token, None)
            session = None
        request.app_session = {'token': token, **session} if session else None
        return self._get_response(request)
