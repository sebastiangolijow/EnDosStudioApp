"""Shared paginators.

`StandardPageNumberPagination` is the project default. It accepts a
`?page_size=N` query param so the admin orders screen can request 25 /
50 / 100 per page on demand without having to switch to a different
endpoint. Capped at `max_page_size` to prevent abuse (a malicious
client can't request page_size=1_000_000 and DOS the DB).
"""

from rest_framework.pagination import PageNumberPagination


class StandardPageNumberPagination(PageNumberPagination):
    """PageNumberPagination with per-request page_size and a sane cap."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
