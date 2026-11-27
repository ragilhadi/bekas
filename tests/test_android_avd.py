"""Tests for the android_avd plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.android_avd import AndroidAvdPlugin, _parse_version


def _ctx(dry_run: bool = False) -> Context:
    return Context(dry_run=dry_run, config={})


def test_not_available_when_no_avd_or_flutter(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plugin = AndroidAvdPlugin()
    assert plugin.is_available(_ctx()) is False
    assert list(plugin.discover(_ctx())) == []


def test_discover_avd_images_and_ini_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    avd_root = tmp_path / ".android" / "avd"
    image = avd_root / "Pixel_7_API_34.avd"
    (image / "snapshots").mkdir(parents=True)
    (image / "snapshots" / "default-boot.png").write_text("snapshot data")
    (avd_root / "Pixel_7_API_34.ini").write_text("[config]\n")
    plugin = AndroidAvdPlugin()
    candidates = list(plugin.discover(_ctx()))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "android.avd"
    assert c.confidence == Confidence.REVIEW
    assert c.path_or_handle == str(image)
    assert c.size_bytes > 0
    assert c.metadata["sidecar"] == str(avd_root / "Pixel_7_API_34.ini")


def test_discover_ignores_ini_without_image(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    avd_root = tmp_path / ".android" / "avd"
    avd_root.mkdir(parents=True)
    (avd_root / "Pixel.ini").write_text("[config]\n")
    plugin = AndroidAvdPlugin()
    assert list(plugin.discover(_ctx())) == []


def test_discover_multiple_avds(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    avd_root = tmp_path / ".android" / "avd"
    (avd_root / "One.avd").mkdir(parents=True)
    (avd_root / "Two.avd").mkdir(parents=True)
    plugin = AndroidAvdPlugin()
    candidates = list(plugin.discover(_ctx()))
    assert len(candidates) == 2
    assert all(c.confidence == Confidence.REVIEW for c in candidates)
    assert [c.metadata["name"] for c in candidates] == ["One", "Two"]


def test_old_flutter_sdk_only_with_multiple_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    flutter = tmp_path / "flutter"
    (flutter / "3.7.0" / "bin").mkdir(parents=True)
    (flutter / "3.7.0" / "bin" / "flutter").write_text("script")
    (flutter / "3.10.0" / "bin").mkdir(parents=True)
    (flutter / "3.10.0" / "bin" / "flutter").write_text("script")
    plugin = AndroidAvdPlugin()
    candidates = [c for c in plugin.discover(_ctx()) if c.category == "flutter.sdk"]
    assert len(candidates) == 1
    assert candidates[0].path_or_handle.endswith("3.7.0")
    assert candidates[0].confidence == Confidence.REVIEW


def test_single_flutter_sdk_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    flutter = tmp_path / "flutter"
    (flutter / "3.10.0" / "bin").mkdir(parents=True)
    (flutter / "3.10.0" / "bin" / "flutter").write_text("script")
    plugin = AndroidAvdPlugin()
    candidates = [c for c in plugin.discover(_ctx()) if c.category == "flutter.sdk"]
    assert candidates == []


def test_current_marker_suppresses_flutter_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    flutter = tmp_path / "flutter"
    (flutter / "current" / "bin").mkdir(parents=True)
    (flutter / "current" / "bin" / "flutter").write_text("script")
    (flutter / "3.7.0" / "bin").mkdir(parents=True)
    (flutter / "3.7.0" / "bin" / "flutter").write_text("script")
    plugin = AndroidAvdPlugin()
    candidates = [c for c in plugin.discover(_ctx()) if c.category == "flutter.sdk"]
    assert candidates == []


def test_remove_avd_deletes_image_and_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    avd_root = tmp_path / ".android" / "avd"
    image = avd_root / "Pixel.avd"
    image.mkdir(parents=True)
    (image / "snapshot.dat").write_text("data")
    ini = avd_root / "Pixel.ini"
    ini.write_text("[config]\n")
    plugin = AndroidAvdPlugin()
    c = Candidate(
        id="android-avd:Pixel.avd",
        category="android.avd",
        size_bytes=4,
        path_or_handle=str(image),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"path": str(image), "sidecar": str(ini), "name": "Pixel"},
    )
    result = plugin.remove(c, _ctx())
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed > 0
    assert result.log == "Deleted"
    assert not image.exists()
    assert not ini.exists()


def test_remove_old_flutter_sdk(tmp_path, monkeypatch):
    sdk = tmp_path / "flutter" / "3.7.0"
    (sdk / "bin").mkdir(parents=True)
    (sdk / "bin" / "flutter").write_text("script")
    (sdk / "version").write_text("3.7.0")
    plugin = AndroidAvdPlugin()
    c = Candidate(
        id=f"flutter-sdk:{sdk}",
        category="flutter.sdk",
        size_bytes=6,
        path_or_handle=str(sdk),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"path": str(sdk), "name": "3.7.0"},
    )
    result = plugin.remove(c, _ctx())
    assert result.success is True
    assert result.bytes_freed >= 6
    assert not sdk.exists()


def test_remove_dry_run_leaves_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    avd_root = tmp_path / ".android" / "avd"
    image = avd_root / "Pixel.avd"
    image.mkdir(parents=True)
    (image / "snapshot.dat").write_text("data")
    ini = avd_root / "Pixel.ini"
    ini.write_text("[config]\n")
    (avd_root / "keepme.avd").mkdir()
    plugin = AndroidAvdPlugin()
    c = Candidate(
        id="android-avd:Pixel.avd",
        category="android.avd",
        size_bytes=4,
        path_or_handle=str(image),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"path": str(image), "sidecar": str(ini), "name": "Pixel"},
    )
    result = plugin.remove(c, _ctx(dry_run=True))
    assert result.success is True
    assert result.log == "dry-run"
    assert result.bytes_freed == 0
    assert image.exists()
    assert ini.exists()
    assert (avd_root / "keepme.avd").exists()


def test_remove_missing_path_fails(tmp_path):
    plugin = AndroidAvdPlugin()
    c = Candidate(
        id="android-avd:Ghost.avd",
        category="android.avd",
        size_bytes=0,
        path_or_handle=str(tmp_path / "ghost.avd"),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={},
    )
    result = plugin.remove(c, _ctx())
    assert result.success is False
    assert result.log == "Path does not exist"


def test_supports_undo_and_quarantine():
    plugin = AndroidAvdPlugin()
    assert plugin.supports_undo() is False
    assert plugin.supports_quarantine() is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [("3.10.0", (3, 10, 0)), ("flutter-2.10", (2, 10)), ("not-a-version", None), ("", None)],
)
def test_parse_version(name, expected):
    assert _parse_version(name) == expected
