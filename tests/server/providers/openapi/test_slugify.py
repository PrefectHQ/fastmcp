"""Tests for the OpenAPI component name slugifier."""

from fastmcp.server.providers.openapi.components import _slugify


def test_slugify_treats_slash_as_a_separator():
    # operationIds are commonly tag-prefixed with a slash (GitHub's REST spec uses
    # "actions/add-custom-labels-..."). The slash must become a separator, not be
    # deleted and glue the tag onto the operation ("actionsadd_custom_labels...").
    assert (
        _slugify("actions/add-custom-labels-to-self-hosted-runner-for-org")
        == "actions_add_custom_labels_to_self_hosted_runner_for_org"
    )


def test_slugify_collapses_all_separators():
    assert _slugify("foo bar-baz.qux/quux") == "foo_bar_baz_qux_quux"
    assert _slugify("a//b") == "a_b"
