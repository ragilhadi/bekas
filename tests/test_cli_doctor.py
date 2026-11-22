"""Tests for CLI doctor command."""

from bekas.cli import cli


def test_cli_doctor(runner, monkeypatch):
    result = runner.invoke(cli, ["doctor"])
    # doctor exits with code based on check results; just assert it runs
    assert result.exit_code in (0, 1)
    assert "bekas doctor" in result.output


def test_cli_doctor_json(runner, monkeypatch):
    result = runner.invoke(cli, ["doctor", "--json"])
    assert result.exit_code in (0, 1)
    assert "{" in result.output or "}" in result.output or "checks" in result.output.lower()


def test_cli_doctor_skip(runner, monkeypatch):
    result = runner.invoke(cli, ["doctor", "--skip", "docker"])
    assert result.exit_code in (0, 1)


def test_cli_doctor_fix_dry_run_changes_nothing(runner, monkeypatch):
    """--fix --dry-run reports actions but never mutates state."""
    import bekas.doctor as doc

    called = []

    def fake_collect(dry_run=False):
        called.append(dry_run)
        return [doc.FixAction("make_dir", "would create {data_dir}/quarantine")], []

    monkeypatch.setattr(doc, "collect_fixes", fake_collect)
    result = runner.invoke(cli, ["doctor", "--fix", "--dry-run"])
    assert called == [True]
    assert result.exit_code == 0
    assert "dry run" in result.output
    assert "make_dir" in result.output


def test_cli_doctor_fix_json(runner, monkeypatch):
    import json

    import bekas.doctor as doc

    monkeypatch.setattr(doc, "collect_fixes", lambda dry_run=False: ([doc.FixAction("fix_permissions", "chmod")], []))
    result = runner.invoke(cli, ["doctor", "--fix", "--json"])
    data = json.loads(result.output)
    assert data["dry_run"] is False
    assert data["actions"][0]["name"] == "fix_permissions"


def test_collect_fixes_repairs_missing_quarantine_dir(monkeypatch, tmp_path):
    """collect_fixes recreates a deleted quarantine directory."""
    import bekas.config as cfg
    import bekas.doctor as doc

    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(cfg, "_data_dir", lambda: ddir)
    monkeypatch.setattr(doc, "data_dir", cfg.data_dir)  # doctor imported the symbol directly
    # Ensure quarantine dir exists first (quarantine_dir() creates it), then delete it
    (ddir / "quarantine").mkdir()
    (ddir / "quarantine").rmdir()
    actions, problems = doc.collect_fixes(dry_run=False)
    assert (ddir / "quarantine").is_dir()
    assert any(a.name == "make_dir" for a in actions)


def test_collect_fixes_tightens_loose_permissions(monkeypatch, tmp_path):
    """Loose permissions on runs.db are reported and fixed."""
    import stat

    import bekas.config as cfg
    import bekas.doctor as doc

    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(cfg, "_data_dir", lambda: ddir)
    monkeypatch.setattr(doc, "data_dir", cfg.data_dir)  # doctor imported the symbol directly
    db = ddir / "runs.db"
    db.write_text("")
    db.chmod(0o666)
    actions, problems = doc.collect_fixes(dry_run=False)
    mode = db.stat().st_mode & 0o777
    assert not (mode & 0o077), f"permissions still loose: {stat.filemode(mode)}"
    assert any(a.name == "fix_permissions" for a in actions)
    assert any("loose permissions" in p for p in problems)


def test_collect_fixes_dry_run_makes_no_change(monkeypatch, tmp_path):
    """dry_run reports the same findings without chmod or mkdir."""
    import bekas.config as cfg
    import bekas.doctor as doc

    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(cfg, "_data_dir", lambda: ddir)
    monkeypatch.setattr(doc, "data_dir", cfg.data_dir)  # doctor imported the symbol directly
    db = ddir / "bekas.lock"
    db.write_text("")
    db.chmod(0o644)
    actions, problems = doc.collect_fixes(dry_run=True)
    assert db.stat().st_mode & 0o777 == 0o644  # unchanged
    assert any(a.name == "fix_permissions" for a in actions)
    assert any("loose permissions" in p for p in problems)


def test_collect_fixes_corrupt_db_is_backed_up_not_deleted(monkeypatch, tmp_path):
    """An unreadable runs DB is quarantined under a .corrupt name, never deleted."""
    import bekas.config as cfg
    import bekas.doctor as doc

    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(cfg, "_data_dir", lambda: ddir)
    monkeypatch.setattr(doc, "data_dir", cfg.data_dir)  # doctor imported the symbol directly
    db = ddir / "runs.db"
    db.write_bytes(b"this is not sqlite at all" * 8)
    actions, problems = doc.collect_fixes(dry_run=False)
    assert not db.exists()
    backups = list(ddir.glob("runs.corrupt.*"))
    assert backups, "corrupt DB was not preserved"
    assert any(a.name == "fix_runs_db" for a in actions)
