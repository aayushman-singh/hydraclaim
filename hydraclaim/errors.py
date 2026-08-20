"""Typed failures shared by HydraClaim boundaries."""

from __future__ import annotations


class GraphIntegrityError(ValueError):
    """Raised when graph relations would violate claim integrity."""


class ValidationError(ValueError):
    """Raised when an input does not satisfy a public data contract."""
