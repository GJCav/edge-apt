from __future__ import annotations

import pytest

from edgeapt.constants import ROOT, SOURCES_DIR
from edgeapt.errors import ValidationError
from edgeapt.infrastructure.source_loader import load_source_documents
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


def test_current_sources_keep_expected_plan_digest() -> None:
    documents = load_source_documents(SOURCES_DIR, root=ROOT)
    plan = build_repo_plan(documents)

    assert len(plan.builds) == 26
    assert len(plan.publications) == 93
    assert plan.plan_digest == (
        "sha256:530b747bfe3bc1090a7272f47fa108924e5232dd8d93cc9f44db748b8d585d44"
    )


def _plan(*sources: SourceTemplate):
    return build_repo_plan(tuple(make_document(source) for source in sources))
