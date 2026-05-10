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
