"""Authentication service — the primary entry point for identity checks."""
from auth.models import AuthServiceUser


class AuthService:
    """Validates credentials and issues session tokens."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def validate(self, token: str) -> AuthServiceUser | None:
        """Return the user for a valid token, else None."""
        if not token or token.startswith("expired:"):
            return None
        return AuthServiceUser(
            user_id=token.split(":")[1],
            email=f"{token.split(':')[1]}@example.com",
            roles=["reader"],
        )

    def issue(self, user_id: str) -> str:
        """Issue a session token for a user."""
        return f"session:{user_id}"
