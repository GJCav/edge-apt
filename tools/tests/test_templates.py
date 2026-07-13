from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal, cast

import attrs
import pytest
from pydantic import Field, ValidationError as PydanticValidationError

from edgeapt.domain.artifacts import DebControlFact, UpstreamFact
from edgeapt.domain.keys import DebKey
from edgeapt.domain.planning import (
    BuildIntent,
    BuildSpec,
    JsonObject,
    SourceProvenance,
)
from edgeapt.project import EdgeAptProject, ProjectPaths
from edgeapt.infrastructure.archive import DefaultArchiveExtractor
from edgeapt.templates.base import (
    BuildContext,
    FetchResult,
    SourceTemplate,
    TemplateBuildResult,
)
from edgeapt.templates.registry import DEFAULT_TEMPLATES, TemplateRegistry
from edgeapt.workflows.repackage import repackage_project
from tests.factories import make_document, make_source


@pytest.mark.parametrize(
    ("template_id", "version", "expected_digest"),
    [
        (
            "edgeapt.single_binary/v1",
            "v1.2.3",
            "sha256:83d3259b57dee66a9751bacaa4b39b7e38806781259ecf9ebe4035a6371fdf14",
        ),
        (
            "edgeapt.deb_upstream/v1",
            "1.2.3",
            "sha256:33aae9164e82247e959265b8e897924dc1bff0aed642c57a78c074e279477b12",
        ),
    ],
)
def test_template_contract_parses_and_plans_stably(
    template_id: str,
    version: str,
    expected_digest: str,
) -> None:
    from edgeapt.workflows.planning import build_repo_plan

    source = make_source(
        template=template_id,
        version=version,
        revision=None if template_id.endswith("deb_upstream/v1") else 1,
    )

    plan = build_repo_plan((make_document(source),))

    assert type(source) is DEFAULT_TEMPLATES.resolve(template_id)
    assert len(plan.builds) == 1
    assert plan.builds[0].plan_digest == expected_digest


@pytest.mark.parametrize("template_id", DEFAULT_TEMPLATES.template_ids)
def test_template_contract_rejects_unknown_fields(template_id: str) -> None:
    source = make_source(
        template=template_id,
        version="1.2.3" if template_id.endswith("deb_upstream/v1") else "v1.2.3",
        revision=None if template_id.endswith("deb_upstream/v1") else 1,
    )
    raw = source.model_dump()
    raw["unknown"] = True

    with pytest.raises(PydanticValidationError, match="unknown"):
        type(source).model_validate(raw)


@pytest.mark.parametrize("template_id", DEFAULT_TEMPLATES.template_ids)
def test_template_contract_requires_valid_sha256(template_id: str) -> None:
    source = make_source(
        template=template_id,
        version="1.2.3" if template_id.endswith("deb_upstream/v1") else "v1.2.3",
        revision=None if template_id.endswith("deb_upstream/v1") else 1,
    )
    raw = source.model_dump()
    upstream = cast(list[dict[str, object]], raw["upstream"])
    del upstream[0]["sha256"]

    with pytest.raises(PydanticValidationError, match="sha256"):
        type(source).model_validate(raw)

    upstream[0]["sha256"] = "sha256:not-a-digest"
    with pytest.raises(PydanticValidationError, match="sha256"):
        type(source).model_validate(raw)


def test_registry_rejects_duplicate_template_ids() -> None:
    template_type = DEFAULT_TEMPLATES.resolve("edgeapt.single_binary/v1")

    with pytest.raises(ValueError, match="duplicate template id"):
        TemplateRegistry([template_type, template_type])


def test_single_binary_rejects_removed_repackage_type() -> None:
    source = make_source(template="edgeapt.single_binary/v1")
    raw = source.model_dump()
    repackage = cast(dict[str, object], raw["repackage"])
    repackage["type"] = "nfpm"

    with pytest.raises(PydanticValidationError, match="repackage.type"):
        type(source).model_validate(raw)


def test_fake_template_works_through_planner_and_repackage(tmp_path: Path) -> None:
    source_path = tmp_path / "sources" / "fake.yaml"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "\n".join(
            [
                "template: edgeapt.fake/v1",
                "id: fake",
                "package: fake-package",
                "e2e_commands:",
                "  - [fake-package, --version]",
                "version: 2.0-1",
                "arch: amd64",
                "suites: [jammy, noble]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = TemplateRegistry([_FakeTemplate])
    deb_tools = _FakeDebTools(
        DebControlFact(
            package="fake-package",
            version="2.0-1",
            architecture="amd64",
        )
    )

    result = repackage_project(
        project=EdgeAptProject(
            paths=ProjectPaths(tmp_path),
            templates=registry,
            fetcher=_FakeFetcher(),
            archive_extractor=DefaultArchiveExtractor(),
            deb_tools=deb_tools,
        )
    )
    lock = result.lock

    assert len(lock.artifacts) == 1
    assert [item.key.suite for item in lock.publications] == ["jammy", "noble"]
    assert (tmp_path / lock.artifacts[0].path).read_bytes() == b"fake deb"


@attrs.define(kw_only=True, frozen=True)
class _FakeBuildSpec:
    version: str

    @property
    def template_id(self) -> str:
        return _FakeTemplate.template_id

    def to_canonical_data(self) -> JsonObject:
        return cast(
            JsonObject,
            {"template": self.template_id, "version": self.version},
        )


class _FakeTemplate(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.fake/v1"

    template: Literal["edgeapt.fake/v1"]
    version: str
    arch: str
    suites: tuple[str, ...] = Field(min_length=1)

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        return (
            BuildIntent(
                deb_key=DebKey(
                    package=self.package,
                    deb_version=self.version,
                    arch=self.arch,
                ),
                suites=self.suites,
                build_spec=_FakeBuildSpec(version=self.version),
                provenance=provenance,
                e2e_commands=self.e2e_commands,
                allow_ubuntu_package_override=self.allow_ubuntu_package_override,
                override_reason=self.override_reason,
            ),
        )

    @classmethod
    def build(
        cls,
        spec: BuildSpec,
        context: BuildContext,
    ) -> TemplateBuildResult:
        if not isinstance(spec, _FakeBuildSpec):
            raise TypeError(type(spec).__name__)
        candidate = context.work_dir / "fake.deb"
        candidate.write_bytes(b"fake deb")
        return TemplateBuildResult(
            candidate_deb=candidate,
            upstream_version=spec.version,
            upstream=UpstreamFact(
                url="generated:fake",
                sha256="sha256:fake",
                size=len(b"fake deb"),
            ),
        )


class _FakeFetcher:
    def fetch(
        self,
        *,
        url: str,
        sha256: str | None,
        destination: Path,
        root: Path,
    ) -> FetchResult:
        destination.write_bytes(b"download")
        return FetchResult(
            path=destination,
            fact=UpstreamFact(
                url=url,
                sha256="sha256:download",
                size=len(b"download"),
            ),
        )

class _FakeDebTools:
    def __init__(self, control: DebControlFact) -> None:
        self._control = control

    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: DebKey,
        description: str,
        homepage: str | None,
        section: str,
        multi_arch: str | None,
        output: Path,
        work_dir: Path,
    ) -> None:
        output.write_bytes(b"fake deb")

    def read_control(self, path: Path) -> DebControlFact:
        return self._control
