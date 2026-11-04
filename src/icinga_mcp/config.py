from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from icinga_mcp.errors import ConfigError
from icinga_mcp.timeparse import parse_duration


class IcingaSettings(BaseSettings):
    """Connection settings for the Icinga 2 REST API, read from ``ICINGA_*`` variables."""

    model_config = SettingsConfigDict(env_prefix="ICINGA_", extra="ignore")

    url: str = Field(description="Base URL of the Icinga 2 API, e.g. https://master:5665")
    user: str = Field(min_length=1)
    password: SecretStr
    ca_cert: Path | None = Field(
        default=None,
        description="CA bundle for the API certificate. Setting it enables TLS verification.",
    )
    tls_server_name: str | None = Field(
        default=None,
        description="Host name expected in the server certificate, if it differs from the URL.",
    )
    verify_tls: bool | None = Field(
        default=None,
        description="Force TLS verification on/off. Default: on if ca_cert is set, else off.",
    )
    timeout: float = Field(default=30.0, gt=0)

    @property
    def tls_verification_enabled(self) -> bool:
        if self.verify_tls is not None:
            return self.verify_tls
        return self.ca_cert is not None

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with https:// (or http://)")
        if value.endswith("/v1"):
            value = value[: -len("/v1")]
        return value

    @field_validator("password")
    @classmethod
    def _password_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _check_paths(self) -> IcingaSettings:
        if self.ca_cert is not None and not self.ca_cert.is_file():
            raise ValueError(f"ICINGA_CA_CERT file not found: {self.ca_cert}")
        return self


class ServerSettings(BaseSettings):
    """MCP server behaviour, read from ``ICINGA_MCP_*`` variables."""

    model_config = SettingsConfigDict(env_prefix="ICINGA_MCP_", extra="ignore")

    toolsets: Annotated[list[str] | None, NoDecode] = Field(
        default=None, description="Comma-separated toolsets to enable (default: all)."
    )
    disabled_tools: Annotated[list[str], NoDecode] = Field(
        default_factory=list, description="Comma-separated tool names to hide."
    )
    read_only: bool = Field(default=True, description="Hide all tools that change Icinga.")
    author: str | None = Field(
        default=None,
        description="Author recorded in Icinga for acknowledgements, comments and downtimes. "
        "Required when write tools are enabled.",
    )
    require_elicitation: bool = Field(
        default=False,
        description="Refuse write tools for clients that cannot ask the user for confirmation "
        "(instead of the two-step confirm=true fallback).",
    )
    max_targets: int = Field(
        default=20, ge=1, le=500, description="Maximum number of objects per write tool call."
    )
    max_downtime: str = Field(default="14d", description="Maximum length of a downtime.")
    max_history: str = Field(default="31d", description="Maximum time window of one history query.")

    @field_validator("author")
    @classmethod
    def _blank_author_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None

    @field_validator("max_downtime", "max_history")
    @classmethod
    def _valid_duration(cls, value: str) -> str:
        parse_duration(value)
        return value

    @property
    def max_downtime_seconds(self) -> int:
        return parse_duration(self.max_downtime)

    @property
    def max_history_seconds(self) -> int:
        return parse_duration(self.max_history)

    @field_validator("toolsets", "disabled_tools", mode="before")
    @classmethod
    def _split_comma_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("toolsets")
    @classmethod
    def _empty_means_all(cls, value: list[str] | None) -> list[str] | None:
        return value or None


class IcingaWebSettings(BaseSettings):
    """Icinga Web 2 access (alert history), read from ``ICINGAWEB_*`` variables."""

    model_config = SettingsConfigDict(env_prefix="ICINGAWEB_", extra="ignore")

    url: str = Field(description="Base URL of Icinga Web 2, e.g. https://icinga.example.com")
    user: str = Field(min_length=1)
    password: SecretStr
    history_backend: Literal["icingadb", "monitoring"] = "icingadb"
    ca_cert: Path | None = None
    verify_tls: bool = Field(
        default=True, description="Icinga Web usually has a publicly trusted certificate."
    )
    timeout: float = Field(default=60.0, gt=0)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with https:// (or http://)")
        return value

    @field_validator("password")
    @classmethod
    def _password_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _check_paths(self) -> IcingaWebSettings:
        if self.ca_cert is not None and not self.ca_cert.is_file():
            raise ValueError(f"ICINGAWEB_CA_CERT file not found: {self.ca_cert}")
        return self


SettingsT = TypeVar("SettingsT", bound=BaseSettings)


def _load(cls: type[SettingsT]) -> SettingsT:
    """Instantiate settings from the environment, turning validation errors into ConfigError."""
    prefix = str(cls.model_config.get("env_prefix", ""))
    try:
        return cls()
    except ValidationError as exc:
        problems = "; ".join(
            f"{prefix}{'_'.join(str(p) for p in err['loc']).upper() or 'CONFIG'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None


def load_icinga_settings() -> IcingaSettings:
    return _load(IcingaSettings)


def load_server_settings() -> ServerSettings:
    return _load(ServerSettings)


def load_icingaweb_settings() -> IcingaWebSettings | None:
    """Icinga Web access is optional: ``None`` if ``ICINGAWEB_URL`` is not set."""
    if not os.environ.get("ICINGAWEB_URL", "").strip():
        return None
    return _load(IcingaWebSettings)
