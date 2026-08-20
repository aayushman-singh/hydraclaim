"""Typed failures shared by HydraClaim boundaries."""

from __future__ import annotations


class GraphIntegrityError(ValueError):
    """Raised when graph relations would violate claim integrity."""


class ValidationError(ValueError):
    """Raised when an input does not satisfy a public data contract."""


class PipelineInputError(ValidationError):
    """Raised when a pipeline document does not match the session contract."""
