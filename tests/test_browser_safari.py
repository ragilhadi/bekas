"""Tests for browser_safari plugin."""

import platform

import pytest

from bekas.models import Context
from bekas.plugins.browser_safari import BrowserSafariPlugin


def test_not_available_on_non_darwin():
    if platform.system().lower() == "darwin":
        pytest.skip("macOS-only test")
    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_plugin_properties():
    p = BrowserSafariPlugin()
    assert p.name == "browser.safari"
    assert p.description
    assert p.capabilities.platforms == ("darwin",)
