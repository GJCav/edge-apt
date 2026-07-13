from __future__ import annotations

from collections.abc import Iterable

import attrs

from edgeapt.domain.lock import LockedPublication, LockFile
from edgeapt.domain.planning import (
    BuildUnit,
    Publication,
    PublicationE2EClaim,
    RepoPlan,
)
from edgeapt.errors import ValidationError


def normalize_source_ids(source_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(source_ids)))


def plan_source_ids(plan: RepoPlan) -> frozenset[str]:
    return frozenset(
        provenance.source_id
        for build in plan.builds
        for provenance in build.provenance
    )


def build_matches_scope(build: BuildUnit, source_ids: tuple[str, ...]) -> bool:
    selected = frozenset(source_ids)
    return any(item.source_id in selected for item in build.provenance)


def validate_source_scope(
    source_ids: tuple[str, ...],
    *,
    available: Iterable[str],
) -> None:
    unknown = sorted(set(source_ids) - set(available))
    if unknown:
        raise ValidationError(f"unknown source scope: {', '.join(unknown)}")


def locked_publications(plan: RepoPlan) -> tuple[LockedPublication, ...]:
    return tuple(
        LockedPublication(
            key=publication.key,
            artifact=publication.deb_key,
            e2e_claims=publication.e2e_claims,
        )
        for publication in plan.publications
    )


def scoped_lock(lock: LockFile, source_ids: tuple[str, ...]) -> LockFile:
    selected = frozenset(source_ids)
    publications: list[LockedPublication] = []
    for publication in lock.publications:
        claims = tuple(
            claim
            for claim in publication.e2e_claims
            if claim.provenance.source_id in selected
        )
        if claims:
            publications.append(attrs.evolve(publication, e2e_claims=claims))
    artifact_keys = {publication.artifact for publication in publications}
    return attrs.evolve(
        lock,
        artifacts=tuple(
            artifact for artifact in lock.artifacts if artifact.deb_key in artifact_keys
        ),
        publications=tuple(publications),
    )


def publication_source_ids(
    publication: LockedPublication | Publication,
) -> frozenset[str]:
    return frozenset(item.source_id for item in publication.provenance)


def validate_scoped_update(
    *,
    plan: RepoPlan,
    desired_publications: tuple[LockedPublication, ...],
    previous_lock: LockFile,
    source_ids: tuple[str, ...],
) -> None:
    selected = frozenset(source_ids)
    previous_artifacts = {item.deb_key: item for item in previous_lock.artifacts}
    current_builds = {item.deb_key: item for item in plan.builds}
    violations: list[str] = []

    for key, build in current_builds.items():
        previous = previous_artifacts.get(key)
        if previous is not None and previous.build_plan_digest == build.plan_digest:
            continue
        owners = {item.source_id for item in build.provenance}
        outside = sorted(owners - selected)
        if outside:
            violations.append(
                f"build ({key.package}, {key.deb_version}, {key.arch}) belongs to "
                f"unselected source(s): {', '.join(outside)}"
            )

    previous_publications = {item.key: item for item in previous_lock.publications}
    current_publications = {item.key: item for item in desired_publications}
    for key in sorted(set(previous_publications) | set(current_publications)):
        previous = previous_publications.get(key)
        current = current_publications.get(key)
        if previous == current:
            continue
        previous_claims: set[PublicationE2EClaim] = (
            set(previous.e2e_claims) if previous is not None else set()
        )
        current_claims: set[PublicationE2EClaim] = (
            set(current.e2e_claims) if current is not None else set()
        )
        changed_claims = previous_claims ^ current_claims
        owners: set[str] = {
            claim.provenance.source_id for claim in changed_claims
        }
        if not owners:
            if previous is not None:
                owners.update(publication_source_ids(previous))
            if current is not None:
                owners.update(publication_source_ids(current))
        outside = sorted(owners - selected)
        if outside:
            violations.append(
                f"publication ({key.suite}, {key.component}, {key.package}, "
                f"{key.deb_version}, {key.arch}) changed for unselected source(s): "
                f"{', '.join(outside)}"
            )

    current_keys = set(current_builds)
    for key in sorted(set(previous_artifacts) - current_keys):
        owners = {
            provenance.source_id
            for publication in previous_lock.publications
            if publication.artifact == key
            for provenance in publication.provenance
        }
        outside = sorted(owners - selected)
        if outside:
            violations.append(
                f"removed build ({key.package}, {key.deb_version}, {key.arch}) "
                f"belongs to unselected source(s): {', '.join(outside)}"
            )

    if violations:
        raise ValidationError(
            "scoped update contains changes outside the selected sources:\n"
            + "\n".join(f"- {item}" for item in violations)
        )
