"""Data models for the auth module."""
from dataclasses import dataclass


# AuthServiceUser is the session identity model — distinct from AuthService
# (the service class in service.py). Don't confuse the two in grep results.
@dataclass
class AuthServiceUser:
    user_id: str
    email: str
    roles: list[str]
