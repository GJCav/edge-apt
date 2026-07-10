from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from edgeapt.constants import COMPONENT
from edgeapt.errors import ValidationError
from edgeapt.models import (
    BuildSpec,
    BuildUnit,
    DebKey,
    DebUpstreamBuildSpec,
    FetchSpec,
    Publication,
    PublishClaim,
    PublishKey,
    RepoPlan,
    SingleBinaryBuildSpec,
    SourceConfig,
    SourceProvenance,
    UpstreamConfig,
)
from edgeapt.sources import artifact_version


def build_repo_plan(sources: Iterable[SourceConfig]) -> RepoPlan:
    claims = tuple(_expand_claims(sources))
    publications = _merge_publications(claims)
    builds = _merge_builds(claims)
    plan_data = {
        "builds": [build.to_json() for build in builds],
        "publications": [publication.to_json() for publication in publications],
    }
    return RepoPlan(
        plan_digest=_digest(plan_data),
        builds=builds,
        publications=publications,
    )


def build_spec_digest(spec: BuildSpec) -> str:
    return _digest(spec.to_json())


def _expand_claims(sources: Iterable[SourceConfig]) -> Iterable[PublishClaim]:
    for source in sorted(sources, key=lambda item: item.id):
        for index, upstream in enumerate(source.upstream):
            deb_version = artifact_version(source, upstream)
            deb_key = DebKey(
                package=source.package,
                deb_version=deb_version,
                arch=upstream.arch,
            )
            build_spec = _compile_build_spec(source, upstream)
            provenance = SourceProvenance(
                source_id=source.id,
                source_file=source.source_file,
                upstream_index=index,
            )
            for suite in sorted(set(upstream.suites)):
                yield PublishClaim(
                    key=PublishKey(
                        suite=suite,
                        component=COMPONENT,
                        package=deb_key.package,
                        deb_version=deb_key.deb_version,
                        arch=deb_key.arch,
                    ),
                    build_spec=build_spec,
                    provenance=provenance,
                    e2e_command=source.e2e_command,
                    allow_ubuntu_package_override=source.allow_ubuntu_package_override,
                    override_reason=source.override_reason,
                )


def _compile_build_spec(
    source: SourceConfig,
    upstream: UpstreamConfig,
) -> BuildSpec:
    fetch = FetchSpec(url=upstream.url, sha256=upstream.sha256)
    if source.template == "edgeapt.single_binary/v1":
        if source.repackage is None or upstream.revision is None:
            raise ValidationError(f"{source.id}: incomplete single_binary configuration")
        return SingleBinaryBuildSpec(
            template=source.template,
            upstream_version=upstream.version,
            revision=upstream.revision,
            fetch=fetch,
            extract_path=upstream.extract_path,
            repackage=source.repackage,
        )
    return DebUpstreamBuildSpec(
        template=source.template,
        upstream_version=upstream.version,
        fetch=fetch,
    )


def _merge_publications(claims: Iterable[PublishClaim]) -> tuple[Publication, ...]:
    grouped: dict[PublishKey, list[PublishClaim]] = defaultdict(list)
    for claim in claims:
        grouped[claim.key].append(claim)

    publications: list[Publication] = []
    for key in sorted(grouped):
        items = grouped[key]
        first = items[0]
        _ensure_same_build_spec(key, items)
        override_values = {item.allow_ubuntu_package_override for item in items}
        if len(override_values) != 1:
            raise ValidationError(
                _conflict_message(
                    f"conflicting Ubuntu override policy for PublishKey {_format_publish_key(key)}",
                    items,
                )
            )
        publications.append(
            Publication(
                key=key,
                deb_key=key.deb_key,
                provenance=tuple(sorted({item.provenance for item in items})),
                e2e_commands=tuple(sorted({item.e2e_command for item in items})),
                allow_ubuntu_package_override=first.allow_ubuntu_package_override,
                override_reasons=tuple(
                    sorted(
                        {
                            item.override_reason
                            for item in items
                            if item.override_reason is not None
                        }
                    )
                ),
            )
        )
    return tuple(publications)


def _merge_builds(claims: Iterable[PublishClaim]) -> tuple[BuildUnit, ...]:
    grouped: dict[DebKey, list[PublishClaim]] = defaultdict(list)
    for claim in claims:
        grouped[claim.key.deb_key].append(claim)

    builds: list[BuildUnit] = []
    for key in sorted(grouped):
        items = grouped[key]
        specs = {build_spec_digest(item.build_spec) for item in items}
        if len(specs) != 1:
            raise ValidationError(
                _conflict_message(
                    f"conflicting build plans for DebKey {_format_deb_key(key)}; "
                    "use a different deb_version",
                    items,
                )
            )
        spec = items[0].build_spec
        builds.append(
            BuildUnit(
                deb_key=key,
                build_spec=spec,
                plan_digest=build_spec_digest(spec),
                provenance=tuple(sorted({item.provenance for item in items})),
            )
        )
    return tuple(builds)


def _ensure_same_build_spec(key: PublishKey, items: list[PublishClaim]) -> None:
    if len({build_spec_digest(item.build_spec) for item in items}) != 1:
        raise ValidationError(
            _conflict_message(
                f"conflicting build plans for PublishKey {_format_publish_key(key)}",
                items,
            )
        )


def _conflict_message(summary: str, claims: Iterable[PublishClaim]) -> str:
    locations = sorted(
        {
            f"- {claim.provenance.source_file} "
            f"(source {claim.provenance.source_id}, upstream[{claim.provenance.upstream_index}])"
            for claim in claims
        }
    )
    return "\n".join([summary, *locations])


def _format_publish_key(key: PublishKey) -> str:
    return (
        f"({key.suite}, {key.component}, {key.package}, "
        f"{key.deb_version}, {key.arch})"
    )


def _format_deb_key(key: DebKey) -> str:
    return f"({key.package}, {key.deb_version}, {key.arch})"


def _digest(data: dict[str, Any]) -> str:
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
