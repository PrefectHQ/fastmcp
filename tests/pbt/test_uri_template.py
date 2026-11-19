"""match_uri_template is the inverse of expand_uri_template.

`roundtrip` draws templates whose literals cannot be confused with an encoded
value and requires match to return exactly the values that were expanded.
`reexpand` allows any variable names; match may then bind differently, but
whatever it returns must expand back to the same URI.
"""

from hypothesis import event, given, note
from hypothesis import strategies as st

from fastmcp.resources.template import expand_uri_template, match_uri_template

from .strategies import DrawnTemplate, uri_templates


def known_divergence(t: DrawnTemplate) -> str | None:
    """Name a divergence already recorded in FINDINGS.md, so the run still
    stops on anything new."""
    for v in t.vars:
        if v.kind == "list" and v.value == [""]:
            return "list-of-one-empty-item"
    return None


def _tally(t: DrawnTemplate) -> None:
    for v in t.vars:
        event(f"kind={v.kind}")
    event(f"vars={len(t.vars)}")


@given(uri_templates())
def test_roundtrip(t: DrawnTemplate):
    _tally(t)
    if known := known_divergence(t):
        event(f"known: {known}")
        return
    uri = expand_uri_template(t.text, t.params)
    note(f"template: {t.text}")
    note(f"params: {t.params}")
    note(f"uri: {uri}")
    matched = match_uri_template(uri, t.text, list_params=t.list_params)
    assert matched is not None, "an expansion does not match its own template"
    assert matched == t.params, "match returned different values"


NAMES = st.sampled_from(["v", "v-", "$v", "v.", "_v", "V", "vé"])


@given(uri_templates(names=NAMES))
def test_reexpand(t: DrawnTemplate):
    _tally(t)
    if known := known_divergence(t):
        event(f"known: {known}")
        return
    uri = expand_uri_template(t.text, t.params)
    note(f"template: {t.text}")
    note(f"uri: {uri}")
    matched = match_uri_template(uri, t.text, list_params=t.list_params)
    if matched is None:
        event("not matched")
        raise AssertionError("an expansion does not match its own template")
    event("matched")
    note(f"matched: {matched}")
    again = expand_uri_template(t.text, matched)
    note(f"re-expanded: {again}")
    assert again == uri, "a match does not expand back to the URI it came from"


@given(st.integers())
async def test_async_given_runs(n: int):
    """pytest-asyncio's auto mode drives async @given tests."""
    assert isinstance(n, int)
