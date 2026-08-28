"""Domain errors — mapped to 400/403/404/409 by the API layer."""

from __future__ import annotations


class DomainError(Exception):
    http_status = 400


class NotFoundError(DomainError):
    http_status = 404


class ForbiddenError(DomainError):
    http_status = 403


class ConflictError(DomainError):
    http_status = 409
