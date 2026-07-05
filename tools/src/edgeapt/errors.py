from __future__ import annotations


class EdgeAptError(Exception):
    """Base exception for expected EdgeAPT failures."""


class ValidationError(EdgeAptError):
    """Raised when source or lock data is invalid."""


class CommandError(EdgeAptError):
    """Raised when an external command fails."""
