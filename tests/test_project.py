from __future__ import annotations

from contextlib import contextmanager

import pytest

from chatgpt_bridge.adapters.chatgpt import ProjectRef
from chatgpt_bridge.project import ProjectError, create_project, validate_name


def test_validate_name_trims() -> None:
    assert validate_name("  Trip  ") == "Trip"


@pytest.mark.parametrize("name", ["", "   ", "\n"])
def test_validate_name_rejects_empty(name: str) -> None:
    with pytest.raises(ProjectError, match="empty"):
        validate_name(name)


def test_validate_name_rejects_a_long_name() -> None:
    with pytest.raises(ProjectError, match="longer than"):
        validate_name("x" * 101)


def test_create_project_requires_a_session() -> None:
    with pytest.raises(ProjectError, match="browser session"):
        create_project("Trip")


def test_create_project_uses_the_session() -> None:
    created: list[str] = []

    class _Source:
        def create_project(self, name: str) -> ProjectRef:
            created.append(name)
            return ProjectRef(name=name, project_id="g-p-abc")

    @contextmanager
    def session():
        yield _Source()

    project = create_project("  Trip  ", session=session)  # type: ignore[arg-type]
    assert created == ["Trip"]
    assert project.project_id == "g-p-abc"


def test_create_project_validates_before_the_browser() -> None:
    def session():  # pragma: no cover - must not run
        raise AssertionError("session should not be opened")

    with pytest.raises(ProjectError, match="empty"):
        create_project("  ", session=session)  # type: ignore[arg-type]
