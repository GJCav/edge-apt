from __future__ import annotations

import attrs

from edgeapt.constants import ROOT
from edgeapt.domain.planning import RepoPlan
from edgeapt.infrastructure.ubuntu_index import ensure_no_ubuntu_package_conflicts
from edgeapt.project import EdgeAptProject, create_project
from edgeapt.workflows.planning import compile_project_plan


@attrs.define(kw_only=True, frozen=True)
class ValidationResult:
    source_count: int
    plan: RepoPlan


def validate_project(
    *,
    project: EdgeAptProject | None = None,
    skip_ubuntu_conflicts: bool = False,
) -> ValidationResult:
    active_project = project or create_project(ROOT)
    planning = compile_project_plan(active_project)
    if not skip_ubuntu_conflicts:
        ensure_no_ubuntu_package_conflicts(
            planning.plan.publications,
            index_dir=active_project.paths.ubuntu_index_dir,
        )
    return ValidationResult(
        source_count=planning.source_count,
        plan=planning.plan,
    )
