"""Stable geometry error identifiers for CLI and HTTP consumers."""


class GeometryError(ValueError):
    def __init__(self, message, *, code="invalid_geometry", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
