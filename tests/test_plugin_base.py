"""Tests for Plugin base class defaults."""

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class _MinimalPlugin(Plugin):
    name = "minimal"
    description = "Minimal test plugin."

    def discover(self, ctx):
        yield Candidate(
            id="m:1",
            category="minimal",
            size_bytes=1,
            path_or_handle="/tmp/minimal",
            confidence=Confidence.SAFE,
            reason="test",
        )


def test_default_remove_returns_not_supported():
    p = _MinimalPlugin()
    c = Candidate(
        id="m:1",
        category="minimal",
        size_bytes=1,
        path_or_handle="/tmp/minimal",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is False
    assert "does not support" in result.log


def test_default_quarantine_returns_not_supported():
    p = _MinimalPlugin()
    c = Candidate(
        id="m:1",
        category="minimal",
        size_bytes=1,
        path_or_handle="/tmp/minimal",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.quarantine(c, ctx, "/tmp/quarantine")
    assert isinstance(result, RemovalResult)
    assert result.success is False


def test_default_undo_returns_not_supported():
    p = _MinimalPlugin()
    c = Candidate(
        id="m:1",
        category="minimal",
        size_bytes=1,
        path_or_handle="/tmp/minimal",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.undo(c, ctx, "token")
    assert isinstance(result, RemovalResult)
    assert result.success is False


def test_default_supports_quarantine_is_false():
    p = _MinimalPlugin()
    assert p.supports_quarantine() is False


def test_default_supports_undo_is_false():
    p = _MinimalPlugin()
    assert p.supports_undo() is False


def test_is_available_platform_mismatch(monkeypatch):
    class DarwinOnly(Plugin):
        name = "darwin_only"
        description = "Only on macOS."
        supported_platforms = ["darwin"]

        def discover(self, ctx):
            yield from []

    p = DarwinOnly()
    ctx = Context(dry_run=True, config={})
    # On Linux, this should be False
    import platform
    if platform.system().lower() != "darwin":
        assert p.is_available(ctx) is False
    else:
        assert p.is_available(ctx) is True


def test_is_available_missing_command(monkeypatch):
    class NeedsFoo(Plugin):
        name = "needs_foo"
        description = "Needs foo command."
        requires_commands = ["foobarbaz_not_on_path"]

        def discover(self, ctx):
            yield from []

    p = NeedsFoo()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx) is False
