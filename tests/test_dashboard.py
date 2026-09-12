"""Tests for jarvis.dashboard and config.dashboard_port (mirrors convention)."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis import config


def test_dashboard_port_default(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_PORT", raising=False)
    assert config.dashboard_port() == 6080


def test_dashboard_port_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "9911")
    assert config.dashboard_port() == 9911


def test_dashboard_port_invalid_degrades(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "not-a-port")
    assert config.dashboard_port() == 6080
    assert "JARVIS_DASHBOARD_PORT" in capsys.readouterr().err


def test_dashboard_port_out_of_range_degrades(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "70000")
    assert config.dashboard_port() == 6080
    assert "JARVIS_DASHBOARD_PORT" in capsys.readouterr().err


def test_forget_repo_removes_registration(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import index_cli
    from jarvis.registry import Registry

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("demo", str(tmp_path), "python", "abc123", "indexed")
    registry.close()

    ok, message = index_cli.forget_repo("demo")
    assert ok and message == "forgot demo"

    registry = Registry(config.data_dir() / "registry.db")
    assert registry.get("demo") is None
    registry.close()


def test_forget_repo_unknown_slug_fails_cleanly(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import index_cli

    ok, message = index_cli.forget_repo("nope")
    assert not ok and "no such repo" in message
