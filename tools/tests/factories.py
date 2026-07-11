from __future__ import annotations

from edgeapt.domain.artifacts import ArtifactFact
from edgeapt.domain.artifacts import UpstreamFact
from edgeapt.domain.keys import DebKey
from edgeapt.domain.keys import PublishKey
from edgeapt.domain.lock import LockedPublication
from edgeapt.domain.lock import LockFile
from edgeapt.domain.planning import SourceProvenance
from edgeapt.constants import LOCK_SCHEMA
from edgeapt.constants import ROOT
from edgeapt.infrastructure.deb import DefaultDebTools
from edgeapt.infrastructure.fetcher import DefaultFetcher
from pathlib import Path
from edgeapt.project import EdgeAptProject, ProjectPaths
from edgeapt.templates.base import DebTools, Fetcher
from edgeapt.templates.base import SourceDocument, SourceTemplate
from edgeapt.templates.registry import DEFAULT_TEMPLATES, TemplateRegistry


def make_project(
    root: Path,
    *,
    templates: TemplateRegistry = DEFAULT_TEMPLATES,
    fetcher: Fetcher | None = None,
    deb_tools: DebTools | None = None,
) -> EdgeAptProject:
    return EdgeAptProject(
        paths=ProjectPaths(root),
        templates=templates,
        fetcher=fetcher or DefaultFetcher(),
        deb_tools=deb_tools or DefaultDebTools(),
    )


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
) -> SourceTemplate:
    package_name = package or source_id
    upstream: dict[str, object] = {
        "version": version,
        "arch": arch,
        "suites": list(suites),
        "url": url,
    }
    if sha256 is not None:
        upstream["sha256"] = sha256
    raw: dict[str, object] = {
        "template": template,
        "id": source_id,
        "package": package_name,
        "e2e_command": list(e2e_command),
        "allow_ubuntu_package_override": allow_ubuntu_package_override,
        "upstream": [upstream],
    }
    if override_reason is not None:
        raw["override_reason"] = override_reason
    if template == "edgeapt.single_binary/v1":
        upstream["revision"] = revision
        if extract_path is not None:
            upstream["extract_path"] = extract_path
        raw["repackage"] = {
            "type": "nfpm",
            "install_path": install_path,
            "metadata": {"description": package_name},
        }
    template_type = DEFAULT_TEMPLATES.resolve(template)
    return template_type.model_validate(raw)


def make_document(source: SourceTemplate) -> SourceDocument:
    return SourceDocument(
        source=source,
        source_file=f"sources/{source.id}.yaml",
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
