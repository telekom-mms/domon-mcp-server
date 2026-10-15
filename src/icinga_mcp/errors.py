class IcingaMCPError(Exception):
    """Base class for all icinga-mcp errors."""


class ConfigError(IcingaMCPError):
    """Invalid or incomplete configuration."""


class IcingaConnectionError(IcingaMCPError):
    """The Icinga API could not be reached (network, TLS, timeout)."""


class IcingaAPIError(IcingaMCPError):
    """The Icinga API answered with an error status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Icinga API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class IcingaAuthError(IcingaAPIError):
    """401 - wrong user or password."""


class IcingaPermissionError(IcingaAPIError):
    """403 - the ApiUser lacks a permission."""


class IcingaNotFoundError(IcingaAPIError):
    """404 - object or endpoint not found."""
