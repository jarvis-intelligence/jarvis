"""Tests for AuthService."""
import pytest
from auth.service import AuthService


class TestAuthService:
    def test_validate_valid_token(self):
        svc = AuthService("test-secret")
        user = svc.validate("session:user42")
        assert user is not None
        assert user.user_id == "user42"

    def test_validate_expired_token(self):
        svc = AuthService("test-secret")
        assert svc.validate("expired:user42") is None

    def test_issue_and_validate(self):
        svc = AuthService("test-secret")
        token = svc.issue("user99")
        user = svc.validate(token)
        assert user is not None
        assert user.user_id == "user99"
