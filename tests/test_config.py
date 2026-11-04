from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from icinga_mcp.config import IcingaSettings, IcingaWebSettings, load_icinga_settings
from icinga_mcp.errors import ConfigError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "ICINGA_URL",
        "ICINGA_USER",
        "ICINGA_PASSWORD",
        "ICINGA_CA_CERT",
        "ICINGA_TLS_SERVER_NAME",
        "ICINGA_VERIFY_TLS",
        "ICINGA_TIMEOUT",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_loads_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("dummy")
    monkeypatch.setenv("ICINGA_URL", "https://master:5665/v1/")
    monkeypatch.setenv("ICINGA_USER", "icinga-mcp")
    monkeypatch.setenv("ICINGA_PASSWORD", "pw")
    monkeypatch.setenv("ICINGA_CA_CERT", str(ca))
    monkeypatch.setenv("ICINGA_TLS_SERVER_NAME", "master-0.svc")
    monkeypatch.setenv("ICINGA_TIMEOUT", "5")

    settings = load_icinga_settings()

    assert settings.url == "https://master:5665"
    assert settings.ca_cert == ca
    assert settings.tls_server_name == "master-0.svc"
    assert settings.timeout == 5
    assert settings.tls_verification_enabled is True
    assert settings.password.get_secret_value() == "pw"
    assert "pw" not in repr(settings)


def test_missing_password_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICINGA_URL", "https://master:5665")
    monkeypatch.setenv("ICINGA_USER", "u")
    with pytest.raises(ConfigError, match="ICINGA_PASSWORD"):
        load_icinga_settings()


def test_empty_password_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICINGA_URL", "https://master:5665")
    monkeypatch.setenv("ICINGA_USER", "u")
    monkeypatch.setenv("ICINGA_PASSWORD", "")
    with pytest.raises(ConfigError, match="ICINGA_PASSWORD: Value error, must not be empty"):
        load_icinga_settings()


def test_missing_url_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICINGA_USER", "u")
    monkeypatch.setenv("ICINGA_PASSWORD", "p")
    with pytest.raises(ConfigError, match="ICINGA_URL"):
        load_icinga_settings()


def test_rejects_url_without_scheme() -> None:
    with pytest.raises(ValueError, match="https://"):
        IcingaSettings(url="master:5665", user="u", password="p")


def test_rejects_missing_ca_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ICINGA_CA_CERT"):
        IcingaSettings(url="https://m", user="u", password="p", ca_cert=tmp_path / "nope.pem")


def test_icingaweb_history_backend_defaults_to_icingadb() -> None:
    settings = IcingaWebSettings(url="https://web.example", user="u", password="p")
    assert settings.history_backend == "icingadb"


def test_tls_verification_defaults(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("dummy")

    def make(**kwargs: Any) -> IcingaSettings:
        return IcingaSettings(url="https://m", user="u", password="p", **kwargs)

    assert make().tls_verification_enabled is False
    assert make(ca_cert=ca).tls_verification_enabled is True
    assert make(verify_tls=True).tls_verification_enabled is True
    assert make(ca_cert=ca, verify_tls=False).tls_verification_enabled is False
