from __future__ import annotations

from edgeapt.workflows.scope import normalize_source_ids, scoped_lock
from tests.factories import make_artifact, make_deb_key, make_lock, make_publication


def test_normalize_source_ids_sorts_and_deduplicates() -> None:
    assert normalize_source_ids(("foo", "bar", "foo")) == ("bar", "foo")


def test_scoped_lock_keeps_only_selected_claims_and_artifacts() -> None:
    shared = make_deb_key(package="shared")
    other = make_deb_key(package="other")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=shared), make_artifact(deb_key=other)),
        publications=(
            make_publication(
                deb_key=shared,
                source_commands=(
                    ("foo", (("shared", "--foo"),)),
                    ("bar", (("shared", "--bar"),)),
                ),
            ),
            make_publication(deb_key=other, source_id="other"),
        ),
    )

    selected = scoped_lock(lock, ("foo",))

    assert [item.deb_key.package for item in selected.artifacts] == ["shared"]
    assert len(selected.publications) == 1
    assert [
        claim.provenance.source_id
        for claim in selected.publications[0].e2e_claims
    ] == ["foo"]
