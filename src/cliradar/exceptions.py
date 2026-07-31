"""Domain exceptions exposed by CLIRadar."""


class CLIRadarError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(CLIRadarError):
    """Configuration is missing, malformed, or unsafe."""


class DeviceConnectionError(CLIRadarError):
    """The SSH connection or interactive device session failed."""


class ScanError(CLIRadarError):
    """The command discovery scan could not complete."""
