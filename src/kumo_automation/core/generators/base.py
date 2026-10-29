"""Generator plugin contract + registry.

Each generator turns a ServerProfile into a set of files:
    {relative_remote_path: file_content}
Relative paths are joined onto the remote policy dir during deploy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ServerProfile


class Generator(ABC):
    name: str = "generator"

    def applies_to(self, profile: ServerProfile) -> bool:  # noqa: ARG002
        return True

    @abstractmethod
    def generate(self, profile: ServerProfile) -> dict[str, str]:
        """Return {relative_path: content}."""
        raise NotImplementedError


REGISTRY: list[Generator] = []


def register(cls):
    """Class decorator: instantiate and add to the registry."""
    REGISTRY.append(cls())
    return cls


def generate_all(profile: ServerProfile) -> dict[str, str]:
    files: dict[str, str] = {}
    for gen in REGISTRY:
        if gen.applies_to(profile):
            files.update(gen.generate(profile))
    return files
