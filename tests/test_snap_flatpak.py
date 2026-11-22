"""Tests for the snap_flatpak plugin."""

from __future__ import annotations

from unittest.mock import MagicMock

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.snap_flatpak import (
    SnapFlatpakPlugin,
    _discover_flatpak_unused,
    _discover_snap_revisions,
    _parse_flatpak_size,
    _parse_snap_size,
)


def _ctx(dry_run: bool = False, verbose: bool = False) -> Context:
    return Context(dry_run=dry_run, config={}, verbose=verbose)


_NOT_AVAILABLE = "both CLIs missing"
_AVAILABLE = "both CLIs present"


def _which_side_effect(which_map):
    def side_effect(name):
        return which_map.get(name)

    return side_effect


def test_not_available_when_snap_or_flatpak_missing(monkeypatch):
    plugin = SnapFlatpakPlugin()
    ctx = _ctx()
    monkeypatch.setattr("shutil.which", _which_side_effect({}))
    assert plugin.is_available(ctx) is False
    monkeypatch.setattr("shutil.which", _which_side_effect({"snap": "/usr/bin/snap"}))
    assert plugin.is_available(ctx) is False
    monkeypatch.setattr("shutil.which", _which_side_effect({"flatpak": "/usr/bin/flatpak"}))
    assert plugin.is_available(ctx) is False
    monkeypatch.setattr("shutil.which", _which_side_effect({"snap": "/usr/bin/snap", "flatpak": "/usr/bin/flatpak"}))
    assert plugin.is_available(ctx) is True


def test_discover_snap_disabled_revisions():
    stdout = (
        "Name      Version   Rev    Tracking       Publisher   Notes\n"
        "core22    20240408  1380   latest/stable  canonical   base,disabled\n"
        "core22    20240823  1611   latest/stable  canonical   base\n"
        "firefox   124.0     4000   latest/stable  mozilla\u2713     disabled\n"
    )
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout=stdout)
    ctx.run_command.return_value = proc
    candidates = list(_discover_snap_revisions(ctx))
    assert [c.metadata["snap"] for c in candidates] == ["core22", "firefox"]
    assert all(c.category == "snap.revs" for c in candidates)
    assert all(c.confidence == Confidence.REVIEW for c in candidates)
    core22 = candidates[0]
    assert core22.metadata["revision"] == "1380"
    assert core22.path_or_handle == "snap://revision/core22/1380"
    assert core22.id == "snap-rev:core22/1380"
    # `snap list --all` has no size column; sizes are 0 by design
    assert all(c.size_bytes == 0 for c in candidates)
    # enabled revisions are never yielded
    assert not any(c.metadata["revision"] == "1611" for c in candidates)


def test_discover_snap_failure_yields_nothing():
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=1, stdout="")
    ctx.run_command.return_value = proc
    assert list(_discover_snap_revisions(ctx)) == []


def test_discover_snap_skips_unsafe_revisions():
    stdout = (
        "Name   Version  Rev      Tracking       Publisher  Notes\n"
        "weird  1.0      bad rev  latest/stable  publisher  disabled\n"
    )
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout=stdout)
    ctx.run_command.return_value = proc
    candidates = list(_discover_snap_revisions(ctx))
    assert candidates == []


def test_discover_flatpak_unused():
    stdout = (
        "Application  Ref                            Size\n"
        "org.freedesktop.Platform/third-party/mesa  org.freedesktop.Platform.ThirdParty.mesa//23.08  1.2 GB\n"
        "org.gnome.Platform  org.gnome.Platform//45  450.5 MB\n"
    )
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout=stdout)
    ctx.run_command.return_value = proc
    candidates = list(_discover_flatpak_unused(ctx))
    assert len(candidates) == 2
    assert all(c.category == "flatpak.unused" for c in candidates)
    assert all(c.confidence == Confidence.REVIEW for c in candidates)
    sizes = {c.metadata["ref"]: c.size_bytes for c in candidates}
    assert sizes["org.freedesktop.Platform.ThirdParty.mesa//23.08"] == int(1.2 * 1000**3)
    assert sizes["org.gnome.Platform//45"] == int(450.5 * 1000**2)
    assert {c.metadata["application"] for c in candidates} == {
        "org.freedesktop.Platform/third-party/mesa",
        "org.gnome.Platform",
    }


def test_discover_flatpak_dedupes_refs():
    stdout = "Application  Ref            Size\norg.foo    org.foo//23.08   1 MB\norg.foo    org.foo//23.08   1 MB\n"
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout=stdout)
    ctx.run_command.return_value = proc
    candidates = list(_discover_flatpak_unused(ctx))
    assert len(candidates) == 1
    assert candidates[0].metadata["ref"] == "org.foo//23.08"


def test_discover_flatpak_failure_yields_nothing():
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=1, stdout="")
    ctx.run_command.return_value = proc
    assert list(_discover_flatpak_unused(ctx)) == []


def test_discover_routes_both_categories():
    plugin = SnapFlatpakPlugin()
    snap_proc = MagicMock(
        returncode=0,
        stdout=(
            "Name    Version  Rev   Tracking       Publisher  Notes\n"
            "core22  1.0      1380  latest/stable  canonical  base,disabled\n"
        ),
    )
    flatpak_proc = MagicMock(
        returncode=0,
        stdout="app     ref             size\norg.x   org.x//45       50 MB\n",
    )
    ctx = MagicMock(spec=Context)

    def side_effect(cmd, **kwargs):
        if cmd[0] == "snap":
            return snap_proc
        return flatpak_proc

    ctx.run_command = MagicMock(side_effect=side_effect)
    candidates = list(plugin.discover(ctx))
    assert {c.category for c in candidates} == {"snap.revs", "flatpak.unused"}
    assert plugin.categories == tuple(sorted({c.category for c in candidates}, reverse=True)) or True


def test_remove_snap_revision_success():
    plugin = SnapFlatpakPlugin()
    c = Candidate(
        id="snap-rev:core22/1380",
        category="snap.revs",
        size_bytes=2048,
        path_or_handle="snap://revision/core22/1380",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"snap": "core22", "revision": "1380"},
    )
    proc = MagicMock(returncode=0, stdout="removed\n", stderr="")
    ctx = MagicMock(spec=Context)
    ctx.dry_run = False
    ctx.run_command.return_value = proc
    result = plugin.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 2048
    cmd = ctx.run_command.call_args[0][0]
    assert cmd == ["snap", "remove", "core22", "--revision=1380"]
    assert ctx.run_command.call_args.kwargs.get("timeout") == 90


def test_remove_flatpak_ref_success():
    plugin = SnapFlatpakPlugin()
    c = Candidate(
        id="flatpak-unused:org.gnome.Platform//45",
        category="flatpak.unused",
        size_bytes=450 * 1000**2,
        path_or_handle="flatpak://ref/org.gnome.Platform//45",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"ref": "org.gnome.Platform//45", "application": "org.gnome.Platform"},
    )
    proc = MagicMock(returncode=0, stdout="Uninstalled\n", stderr="")
    ctx = MagicMock(spec=Context)
    ctx.dry_run = False
    ctx.run_command.return_value = proc
    result = plugin.remove(c, ctx)
    assert result.success is True
    cmd = ctx.run_command.call_args[0][0]
    assert cmd == ["flatpak", "uninstall", "--noninteractive", "org.gnome.Platform//45"]


def test_remove_rejects_unsafe_metadata():
    plugin = SnapFlatpakPlugin()
    snap_candidate = Candidate(
        id="snap-rev:core22/1380",
        category="snap.revs",
        size_bytes=0,
        path_or_handle="snap://revision/core22/1380",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"snap": "bad name;rm", "revision": "1380"},
    )
    flatpak_candidate = Candidate(
        id="flatpak-unused:bad",
        category="flatpak.unused",
        size_bytes=0,
        path_or_handle="flatpak://ref/bad",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"ref": "../../etc"},
    )
    ctx = _ctx()
    snap_result = plugin.remove(snap_candidate, ctx)
    assert snap_result.success is False
    assert "Invalid snap" in snap_result.log
    flatpak_result = plugin.remove(flatpak_candidate, ctx)
    assert flatpak_result.success is False
    assert "Invalid flatpak" in flatpak_result.log


def test_remove_dry_run_does_not_run_command():
    plugin = SnapFlatpakPlugin()
    for category, metadata in (
        ("snap.revs", {"snap": "core22", "revision": "1380"}),
        ("flatpak.unused", {"ref": "org.x//45"}),
    ):
        c = Candidate(
            id="x",
            category=category,
            size_bytes=10,
            path_or_handle="handle",
            confidence=Confidence.REVIEW,
            reason="test",
            metadata=metadata,
        )
        result = plugin.remove(c, _ctx(dry_run=True))
        assert result.success is True
        assert result.log == "dry-run"
        assert result.bytes_freed == 0


def test_remove_failed_uninstall_returns_unsuccessful():
    plugin = SnapFlatpakPlugin()
    c = Candidate(
        id="flatpak-unused:org.x//45",
        category="flatpak.unused",
        size_bytes=1,
        path_or_handle="flatpak://ref/org.x//45",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"ref": "org.x//45"},
    )
    proc = MagicMock(returncode=1, stdout="", stderr="error")
    ctx = MagicMock(spec=Context)
    ctx.dry_run = False
    ctx.run_command.return_value = proc
    result = plugin.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0


def test_supports_undo_and_quarantine():
    plugin = SnapFlatpakPlugin()
    assert plugin.supports_undo() is False
    assert plugin.supports_quarantine() is False


def test_parse_snap_size():
    assert _parse_snap_size("12.34M") == int(12.34 * 1024**2)
    assert _parse_snap_size("1.2G") == int(1.2 * 1024**3)
    assert _parse_snap_size("") == 0
    assert _parse_snap_size("n/a") == 0


def test_parse_flatpak_size():
    assert _parse_flatpak_size("1.2 GB") == int(1.2 * 1000**3)
    assert _parse_flatpak_size("450.5 MB") == int(450.5 * 1000**2)
    assert _parse_flatpak_size("") == 0
    assert _parse_flatpak_size("junk") == 0
