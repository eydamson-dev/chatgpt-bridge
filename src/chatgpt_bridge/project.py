"""Explicit write path: create one empty ChatGPT project.

Nothing here decides whether creation is allowed. The CLI prompts and the MCP
tool requires `confirm=true`; this module validates the name and performs the
creation after that decision. Renaming, deleting, pinning, icons, memory, files,
and instructions are deliberately not reachable from here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from chatgpt_bridge.adapters.chatgpt import ProjectRef

PROJECT_NAME_MAX = 100


class ProjectError(RuntimeError):
    """The project was not created (validation or page failure)."""


class ProjectSource(Protocol):
    def create_project(self, name: str) -> ProjectRef: ...


SessionFactory = Callable[[], AbstractContextManager[ProjectSource]]


def create_project(name: str, *, session: SessionFactory | None = None) -> ProjectRef:
    """Create one empty project with the given name."""
    wanted = validate_name(name)
    if session is None:
        raise ProjectError("creating a project requires a browser session")
    with session() as source:
        return source.create_project(wanted)


def validate_name(name: str) -> str:
    """Return the name to create, or raise before anything reaches the browser."""
    wanted = name.strip()
    if not wanted:
        raise ProjectError("project name is empty")
    if len(wanted) > PROJECT_NAME_MAX:
        raise ProjectError(f"project name is longer than {PROJECT_NAME_MAX} characters")
    return wanted
