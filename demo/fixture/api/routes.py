"""HTTP route handlers — call AuthService for auth checks."""
from auth.service import AuthService


_auth_service: AuthService | None = None


def get_auth_service(secret: str) -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(secret)
    return _auth_service


def handle_login(token: str) -> dict:
    svc = get_auth_service("demo-secret")
    user = svc.validate(token)
    if user is None:
        return {"error": "unauthorized"}
    return {"user": user.user_id, "roles": user.roles}
