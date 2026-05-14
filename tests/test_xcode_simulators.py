"""Tests for xcode_simulators plugin."""

import platform

import pytest

from bekas.models import Context
from bekas.plugins.xcode_simulators import XcodeSimulatorsPlugin


def test_not_available_on_non_darwin():
    if platform.system().lower() == "darwin":
        pytest.skip("macOS-only test")
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_plugin_properties():
    p = XcodeSimulatorsPlugin()
    assert p.name == "xcode.simulators"
    assert p.description
    assert p.capabilities.platforms == ("darwin",)
    assert "xcrun" in p.capabilities.requires_cli
