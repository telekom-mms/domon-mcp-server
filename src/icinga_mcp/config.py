from __future__ import annotations

from pathlib import Path
from typing import Annotated, TypeVar

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from icinga_mcp.errors import ConfigError


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
