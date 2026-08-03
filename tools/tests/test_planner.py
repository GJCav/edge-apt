from __future__ import annotations

import pytest

from edgeapt.constants import ROOT, SOURCES_DIR
from edgeapt.errors import ValidationError
from edgeapt.infrastructure.source_loader import load_source_documents
from edgeapt.templates.deb_upstream_v1 import DebUpstreamV1
from edgeapt.workflows.planning import build_repo_plan
from edgeapt.templates.base import SourceTemplate
from tests.factories import make_document, make_source


def test_expands_suites_and_merges_one_build() -> None:
    plan = _plan(make_source(suites=("noble", "jammy")))

    assert len(plan.builds) == 1
    assert [item.key.suite for item in plan.publications] == ["jammy", "noble"]
    assert {item.deb_key for item in plan.publications} == {plan.builds[0].deb_key}


def test_merges_identical_claims_and_keeps_provenance_and_commands() -> None:
    first = make_source(source_id="foo-common", package="foo")
    second = make_source(
        source_id="foo-noble",
        package="foo",
        e2e_commands=(("foo", "version"),),
    )

    plan = _plan(second, first)

    assert len(plan.builds) == 1
    assert len(plan.publications) == 1
    publication = plan.publications[0]
    assert [item.source_id for item in publication.provenance] == [
        "foo-common",
        "foo-noble",
    ]
    assert [claim.commands for claim in publication.e2e_claims] == [
        (("foo", "--version"),),
        (("foo", "version"),),
    ]


def test_rejects_conflicting_publish_key() -> None:
    first = make_source(source_id="foo-first", package="foo")
    second = make_source(
        source_id="foo-second",
        package="foo",
        install_path="/usr/local/bin/foo",
    )

    with pytest.raises(ValidationError, match="conflicting build plans for PublishKey"):
        _plan(first, second)


def test_rejects_same_deb_key_with_different_suite_plans() -> None:
    jammy = make_source(source_id="foo-jammy", package="foo", suites=("jammy",))
    noble = make_source(
        source_id="foo-noble",
        package="foo",
        suites=("noble",),
        install_path="/usr/local/bin/foo",
    )

    with pytest.raises(ValidationError, match="conflicting build plans for DebKey"):
        _plan(jammy, noble)


def test_rejects_conflicting_override_policy() -> None:
    first = make_source(source_id="foo-first", package="foo")
    second = make_source(
        source_id="foo-second",
        package="foo",
        allow_ubuntu_package_override=True,
        override_reason="Use EdgeAPT build.",
    )

    with pytest.raises(ValidationError, match="conflicting Ubuntu override policy"):
        _plan(first, second)


def test_plan_is_independent_of_source_order() -> None:
    first = make_source(source_id="foo-first", package="foo", suites=("jammy",))
    second = make_source(source_id="foo-second", package="foo", suites=("noble",))

    forward = _plan(first, second)
    reverse = _plan(second, first)

    assert forward == reverse


def test_current_sources_chezmoi_upstreams_are_preserved() -> None:
    documents = load_source_documents(SOURCES_DIR, root=ROOT)
    plan = build_repo_plan(documents)

    chezmoi_document = next(
        document for document in documents if document.source.id == "chezmoi"
    )
    assert isinstance(chezmoi_document.source, DebUpstreamV1)

    expected_versions = {item.version for item in chezmoi_document.source.upstream}
    expected_suites_by_version = {
        item.version: set(item.suites) for item in chezmoi_document.source.upstream
    }

    actual_build_versions = {
        item.deb_key.deb_version for item in plan.builds if item.deb_key.package == "chezmoi"
    }
    actual_suites_by_version: dict[str, set[str]] = {}
    for publication in plan.publications:
        if publication.deb_key.package != "chezmoi":
            continue
        version = publication.deb_key.deb_version
        actual_suites_by_version.setdefault(version, set()).add(publication.key.suite)

    assert actual_build_versions == expected_versions
    assert set(actual_suites_by_version) == expected_versions
    assert actual_suites_by_version == expected_suites_by_version


def _plan(*sources: SourceTemplate):
    return build_repo_plan(tuple(make_document(source) for source in sources))
