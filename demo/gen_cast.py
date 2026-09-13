#!/usr/bin/env python3
"""Generate an asciinema .cast file for the grep vs findReferences demo."""
import json, sys

events = []  # (offset_sec, type, data)

def add(offset: float, data: str):
    events.append((offset, "o", data))

# ── Scene 1: grep ────────────────────────────────────────────────────
add(0.0, "\x1b[2J\x1b[1;1H")
add(0.3, "\x1b[35m# Without jarvis: grep matches strings, not symbols\x1b[0m\n\n")
add(0.8, "\x1b[32m$\x1b[0m grep -rn \"AuthService\" .\n")
add(1.5, "./auth/service.py:5:class AuthService:\n")
add(1.6, "./auth/__init__.py:2:from auth.service import AuthService\n")
add(1.7, "./auth/__init__.py:4:__all__ = [\"AuthService\", \"AuthServiceUser\"]\n")
add(1.8, "./auth/models.py:5:# AuthServiceUser is the session identity model — distinct from AuthService\n")
add(1.9, "./docs/design.md:3:The `AuthService` class handles credential validation and session\n")
add(2.0, "./docs/design.md:6:> Note: \"AuthService\" also appears in README.md — grep matches it too.\n")
add(2.1, "./README.md:4:grep. The `AuthService` symbol appears in code files and in this README\n")
add(2.2, "./api/routes.py:1:\"\"\"HTTP route handlers — call AuthService for auth checks.\"\"\"\n")
add(2.3, "./api/routes.py:7:    _auth_service: AuthService | None = None\n")
add(2.4, "./tests/test_auth.py:1:\"\"\"Tests for AuthService.\"\"\"\n")
add(2.5, "./tests/test_auth.py:3:from auth.service import AuthService\n")
add(2.6, "./tests/test_auth.py:6:class TestAuthService:\n")
add(2.7, "./tests/test_auth.py:8:        svc = AuthService(\"test-secret\")\n")
add(2.8, "./tests/test_auth.py:14:        svc = AuthService(\"test-secret\")\n")
add(2.9, "./tests/test_auth.py:18:        svc = AuthService(\"test-secret\")\n")
add(3.5, "\n\x1b[33m# → 17 hits: comments, markdown, AuthServiceUser, imports…\x1b[0m\n")
add(4.5, "\x1b[2J\x1b[1;1H")

# ── Scene 2: findReferences ──────────────────────────────────────────
add(4.8, "\x1b[35m# With jarvis: findReferences returns exact occurrences\x1b[0m\n\n")
add(5.3, "\x1b[32m$\x1b[0m ../findrefs.sh fixture AuthService\n")
add(6.2, "  AuthService → \x1b[32m10 exact occurrence(s):\x1b[0m\n")
add(6.5, "    auth/service.py:5:7        ← definition\n")
add(6.7, "    auth/__init__.py:2:26\n")
add(6.9, "    api/routes.py:2:26\n")
add(7.1, "    api/routes.py:5:16\n")
add(7.3, "    api/routes.py:8:38\n")
add(7.5, "    api/routes.py:11:25\n")
add(7.7, "    tests/test_auth.py:3:26\n")
add(7.9, "    tests/test_auth.py:8:15\n")
add(8.1, "    tests/test_auth.py:14:15\n")
add(8.3, "    tests/test_auth.py:18:15\n")
add(8.8, "  freshness: fresh · commit 17f4259\n")
add(9.5, "\n\x1b[35m# Only real code references. No markdown. No comments. No AuthServiceUser.\x1b[0m\n")

header = {
    "version": 2,
    "width": 80,
    "height": 24,
    "timestamp": 0,
    "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
}
out = sys.argv[1] if len(sys.argv) > 1 else "demo/demo.cast"
with open(out, "w") as f:
    f.write(json.dumps(header) + "\n")
    for offset, etype, data in events:
        f.write(json.dumps([offset, etype, data]) + "\n")
print(f"cast written: {out}")
