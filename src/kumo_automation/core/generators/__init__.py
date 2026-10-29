"""Config generators (plugin registry).

Importing this package registers the built-in generators. Add a new generator
module + template to extend output in future phases — the core never changes.
"""
from .base import REGISTRY, Generator, generate_all, register  # noqa: F401
from . import kumomta  # noqa: F401  (registers KumoMTA + SMTP-user generators)

__all__ = ["REGISTRY", "Generator", "generate_all", "register"]
