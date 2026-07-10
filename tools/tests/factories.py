from __future__ import annotations

from edgeapt.models import RepackageConfig
from edgeapt.models import RepackageMetadata
from edgeapt.models import ArtifactFact
from edgeapt.models import DebKey
from edgeapt.models import LockedPublication
from edgeapt.models import LockFile
from edgeapt.models import PublishKey
from edgeapt.models import SourceProvenance
from edgeapt.models import SourceConfig
from edgeapt.models import UpstreamConfig
from edgeapt.models import UpstreamFact
from edgeapt.constants import LOCK_SCHEMA
from edgeapt.constants import ROOT
from pathlib import Path


def make_source(
    *,
    source_id: str = "foo",
    package: str | None = None,
    template: str = "edgeapt.single_binary/v1",
    version: str = "v1.2.3",
    revision: int | None = 1,
    suites: tuple[str, ...] = ("noble",),
    arch: str = "amd64",
    url: str = "https://example.invalid/foo",
    sha256: str | None = None,
    extract_path: str | None = None,
    install_path: str = "/usr/bin/foo",
    e2e_command: tuple[str, ...] = ("foo", "--version"),
    allow_ubuntu_package_override: bool = False,
    override_reason: str | None = None,
) -> SourceConfig:
    package_name = package or source_id
    repackage = None
    if template == "edgeapt.single_binary/v1":
        repackage = RepackageConfig(
            type="nfpm",
            install_path=install_path,
            metadata=RepackageMetadata(description=package_name),
        )
    return SourceConfig(
        template=template,
        id=source_id,
        package=package_name,
        e2e_command=e2e_command,
        source_file=f"sources/{source_id}.yaml",
        repackage=repackage,
        upstream=(
            UpstreamConfig(
                version=version,
                revision=revision,
                arch=arch,
                suites=suites,
                url=url,
                sha256=sha256,
                extract_path=extract_path,
            ),
        ),
        allow_ubuntu_package_override=allow_ubuntu_package_override,
        override_reason=override_reason,
    )


def make_deb_key(
    *,
    package: str = "foo",
    deb_version: str = "1.2.3-1",
    arch: str = "amd64",
) -> DebKey:
    return DebKey(package=package, deb_version=deb_version, arch=arch)


def make_artifact(
    *,
    deb_key: DebKey | None = None,
    build_plan_digest: str = "sha256:plan",
    path: str | None = None,
    sha256: str = "sha256:artifact",
    size: int = 10,
    created_at: str = "2026-07-05T00:00:00Z",
) -> ArtifactFact:
    key = deb_key or make_deb_key()
    return ArtifactFact(
        deb_key=key,
        build_plan_digest=build_plan_digest,
        upstream_version=key.deb_version,
        revision=1,
        path=path or f"packages/{key.package}/{key.package}_{key.deb_version}_{key.arch}.deb",
        sha256=sha256,
        size=size,
        upstream=UpstreamFact(
            url="https://example.invalid/package.deb",
            sha256="sha256:upstream",
            size=size,
        ),
        created_at=created_at,
    )


def make_publication(
    *,
    deb_key: DebKey | None = None,
    suite: str = "noble",
    component: str = "main",
    source_id: str | None = None,
    commands: tuple[tuple[str, ...], ...] | None = None,
) -> LockedPublication:
    key = deb_key or make_deb_key()
    resolved_source_id = source_id or key.package
    return LockedPublication(
        key=PublishKey(
            suite=suite,
            component=component,
            package=key.package,
            deb_version=key.deb_version,
            arch=key.arch,
        ),
        artifact=key,
        provenance=(
            SourceProvenance(
                source_id=resolved_source_id,
                source_file=f"sources/{resolved_source_id}.yaml",
                upstream_index=0,
            ),
        ),
        e2e_commands=commands or ((key.package, "--version"),),
    )


def make_lock(
    *,
    artifacts: tuple[ArtifactFact, ...] | None = None,
    publications: tuple[LockedPublication, ...] | None = None,
    plan_digest: str = "sha256:repo-plan",
) -> LockFile:
    artifact_items = artifacts or (make_artifact(),)
    publication_items = publications or (
        make_publication(deb_key=artifact_items[0].deb_key),
    )
    return LockFile(
        schema=LOCK_SCHEMA,
        generated_at="2026-07-05T00:00:00Z",
        plan_digest=plan_digest,
        artifacts=artifact_items,
        publications=publication_items,
    )


def write_hello_source(root: Path) -> None:
    source = root / "sources" / "hello.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    fixture = (ROOT / "tests" / "fixtures" / "hello-world").as_posix()
    source.write_text(
        "\n".join(
            [
                "template: edgeapt.single_binary/v1",
                "id: hello",
                "package: edgeapt-hello",
                "e2e_command: [edgeapt-hello]",
                "repackage:",
                "  type: nfpm",
                "  install_path: /usr/bin/edgeapt-hello",
                "  metadata:",
                '    description: "EdgeAPT test fixture"',
                "upstream:",
                "  - version: v0.1.0",
                "    revision: 1",
                "    arch: amd64",
                "    suites: [noble]",
                f'    url: "{fixture}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
