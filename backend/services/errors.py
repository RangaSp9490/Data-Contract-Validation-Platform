"""Domain exceptions. Routers never need try/except: main.py maps these to HTTP."""
from typing import Any, Optional


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.details = details


class BadRequestError(ServiceError):
    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class PayloadTooLargeError(ServiceError):
    status_code = 413
