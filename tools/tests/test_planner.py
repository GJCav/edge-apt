from __future__ import annotations

import attrs
import pytest

from edgeapt.errors import ValidationError
from edgeapt.models import RepackageConfig
from edgeapt.models import RepackageMetadata
from edgeapt.planner import build_repo_plan
from tests.factories import make_source


def test_expands_suites_and_merges_one_build() -> None:
    plan = build_repo_plan((make_source(suites=("noble", "jammy")),))

    assert len(plan.builds) == 1
    assert [item.key.suite for item in plan.publications] == ["jammy", "noble"]
    assert {item.deb_key for item in plan.publications} == {plan.builds[0].deb_key}


def test_merges_identical_claims_and_keeps_provenance_and_commands() -> None:
    first = make_source(source_id="foo-common", package="foo", suites=("noble",))
    second = attrs.evolve(
        first,
        id="foo-noble",
        source_file="sources/foo-noble.yaml",
        e2e_command=("foo", "version"),
    )

    plan = build_repo_plan((second, first))

    assert len(plan.builds) == 1
    assert len(plan.publications) == 1
    publication = plan.publications[0]
    assert [item.source_id for item in publication.provenance] == [
        "foo-common",
        "foo-noble",
    ]
    assert publication.e2e_commands == (("foo", "--version"), ("foo", "version"))


def test_rejects_conflicting_publish_key() -> None:
    first = make_source(source_id="foo-first", package="foo", suites=("noble",))
    second = attrs.evolve(
        make_source(source_id="foo-second", package="foo", suites=("noble",)),
        source_file="sources/foo-second.yaml",
        repackage=RepackageConfig(
            type="nfpm",
            install_path="/usr/local/bin/foo",
            metadata=RepackageMetadata(description="foo"),
        ),
    )

    with pytest.raises(ValidationError, match="conflicting build plans for PublishKey"):
        build_repo_plan((first, second))


def test_rejects_same_deb_key_with_different_suite_plans() -> None:
    jammy = make_source(source_id="foo-jammy", package="foo", suites=("jammy",))
    noble = attrs.evolve(
        make_source(source_id="foo-noble", package="foo", suites=("noble",)),
        source_file="sources/foo-noble.yaml",
        repackage=RepackageConfig(
            type="nfpm",
            install_path="/usr/local/bin/foo",
            metadata=RepackageMetadata(description="foo"),
        ),
    )

    with pytest.raises(ValidationError, match="conflicting build plans for DebKey"):
        build_repo_plan((jammy, noble))


def test_rejects_conflicting_override_policy() -> None:
    first = make_source(source_id="foo-first", package="foo", suites=("noble",))
    second = attrs.evolve(
        make_source(source_id="foo-second", package="foo", suites=("noble",)),
        source_file="sources/foo-second.yaml",
        allow_ubuntu_package_override=True,
        override_reason="Use EdgeAPT build.",
    )

    with pytest.raises(ValidationError, match="conflicting Ubuntu override policy"):
        build_repo_plan((first, second))


def test_plan_is_independent_of_source_order() -> None:
    first = make_source(source_id="foo-first", package="foo", suites=("jammy",))
    second = attrs.evolve(
        first,
        id="foo-second",
        source_file="sources/foo-second.yaml",
        upstream=(attrs.evolve(first.upstream[0], suites=("noble",)),),
    )

    forward = build_repo_plan((first, second))
    reverse = build_repo_plan((second, first))

    assert forward == reverse
