"""
Auth views.

dj-rest-auth's stock LoginView / LogoutView / TokenRefreshView are mounted
in auth_urls.py directly — no overrides on day 1. If we add throttling
or custom token claims later, subclasses go here.
"""
