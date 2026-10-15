from __future__ import annotations

import io

import pytest

from icinga_mcp import cli
from icinga_mcp.config import IcingaSettings
from icinga_mcp.permissions import READ_PERMISSIONS
from tests.conftest import Recorder, json_response

STATUS = {
    "results": [
        {
            "name": "IcingaApplication",
            "status": {"icingaapplication": {"app": {"version": "r2.16.5-1", "node_name": "m0"}}},
        }
    ]
}


@pytest.mark.anyio
async def test_check_reports_version_and_permissions(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("GET", "/v1/status/IcingaApplication"): json_response(200, STATUS),
            ("GET", "/v1"): json_response(
                200, {"results": [{"permissions": ["objects/query/*", "status/query", "events/*"]}]}
            ),
        }
    )
    out = io.StringIO()

    code = await cli.run_check(settings, out, transport=rec.transport)

    text = out.getvalue()
    assert code == cli.EXIT_OK
    assert "r2.16.5-1" in text
    assert "NOT verified" in text
    assert "Read tools   : OK" in text
    assert "Write tools  : missing actions/acknowledge-problem" in text
    assert "Not needed   : events/*" in text
    assert "s3cret" not in text


@pytest.mark.anyio
async def test_check_fails_without_read_permissions(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("GET", "/v1/status/IcingaApplication"): json_response(200, STATUS),
            ("GET", "/v1"): json_response(200, {"results": [{"permissions": ["status/query"]}]}),
        }
    )
    code = await cli.run_check(settings, io.StringIO(), transport=rec.transport)
    assert code == cli.EXIT_CONNECTION
    assert "status/query" in READ_PERMISSIONS


def test_main_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ["ICINGA_URL", "ICINGA_USER", "ICINGA_PASSWORD"]:
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["check"]) == cli.EXIT_CONFIG
    assert "invalid configuration" in capsys.readouterr().err


def test_main_without_command_serves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ["ICINGA_URL", "ICINGA_USER", "ICINGA_PASSWORD"]:
        monkeypatch.delenv(name, raising=False)
    # "serve" is the default command; without configuration it must fail before starting.
    assert cli.main([]) == cli.EXIT_CONFIG
    assert "ICINGA_URL" in capsys.readouterr().err


def test_list_tools_needs_no_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ICINGA_URL", raising=False)
    monkeypatch.setenv("ICINGA_MCP_TOOLSETS", "problems")
    assert cli.main(["list-tools"]) == cli.EXIT_OK
    lines = {line.split()[0]: line.split()[-1] for line in capsys.readouterr().out.splitlines()}
    assert lines["list_problems"] == "yes"
    assert lines["get_host"] == "no"


def test_unknown_toolset_is_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ICINGA_MCP_TOOLSETS", "problems,typo")
    assert cli.main(["list-tools"]) == cli.EXIT_CONFIG
    assert "typo" in capsys.readouterr().err
