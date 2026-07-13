from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.domain.artifacts import DebControlFact
from edgeapt.errors import ValidationError
from edgeapt.workflows.repackage import repackage_project
from edgeapt.workflows.validate import validate_project
from tests.factories import make_project, write_hello_source


def test_validate_workflow_returns_compiled_plan(tmp_path: Path) -> None:
    write_hello_source(tmp_path)

    result = validate_project(
        project=make_project(tmp_path),
        skip_ubuntu_conflicts=True,
    )

    assert result.source_count == 1
    assert len(result.plan.builds) == 1
    assert len(result.plan.publications) == 1


def test_repackage_rejects_candidate_with_wrong_deb_key(tmp_path: Path) -> None:
    write_hello_source(tmp_path)
    project = make_project(
        tmp_path,
        deb_tools=_WrongControlDebTools(),
    )

    with pytest.raises(ValidationError, match="Package mismatch"):
        repackage_project(project=project)

    assert not project.paths.lock_path.exists()
    assert not tuple(project.paths.packages_dir.rglob("*.deb"))


class _WrongControlDebTools:
    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: object,
        description: str,
        homepage: str | None,
        section: str,
        multi_arch: str | None,
        depends: tuple[str, ...],
        output: Path,
        work_dir: Path,
    ) -> None:
        output.write_bytes(b"candidate")

    def read_control(self, path: Path) -> DebControlFact:
        return DebControlFact(
            package="wrong-package",
            version="0.1.0-1",
            architecture="amd64",
        )
