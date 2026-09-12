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
