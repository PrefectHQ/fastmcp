"""The URL guards in front of the OAuth proxy say no to everything outside
what they were configured to allow.

Three guards, checked against outside references (`ipaddress`, three URL
parsers, `parse_qsl`) where one exists, and against the documented rules
where none does:

- `ssrf`: an address the blocklist refuses stays refused however it is
  wrapped in IPv6, `validate_url` fails only with `SSRFError`, and the
  connection a validated URL makes goes where validation looked.
- `redirect_validation`: a URI that matches a pattern lands, by every parser
  a client or browser might use, on the pattern's own host with no userinfo;
  an exact pattern matches only itself; and every client redirect carries
  each response parameter once.
- `cimd`: a document's scopes never widen the server's, and cache headers
  never crash a fetch. (Its redirect URIs go through the same patterns.)
"""

import asyncio
import ipaddress
from datetime import UTC, datetime
from email.utils import format_datetime
from unittest.mock import patch
from urllib.parse import parse_qsl, urlparse

import httpx2
from hypothesis import assume, event, given, note
from hypothesis import strategies as st
from pydantic import AnyUrl, ValidationError

from fastmcp.server.auth import ssrf
from fastmcp.server.auth.cimd import CIMDClientManager, CIMDFetcher
from fastmcp.server.auth.redirect_validation import (
    build_client_redirect,
    is_loopback_host,
    matches_allowed_pattern,
)

from .known import still_open

# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------

# Ranges the blocklist must refuse, drawn directly so most examples land in
# one instead of in the public internet.
BLOCKED_V4 = [
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
]


@st.composite
def blocked_v4(draw) -> ipaddress.IPv4Address:
    net = draw(st.sampled_from(BLOCKED_V4))
    offset = draw(st.integers(0, net.num_addresses - 1))
    ip = net[offset]
    # IANA carves a few global addresses out of special ranges (192.0.0.9
    # and .10 are anycast services); those are rightly allowed.
    assume(not ip.is_global or ip.is_multicast)
    return ip


def v6_wrappers(v4: ipaddress.IPv4Address) -> dict[str, ipaddress.IPv6Address]:
    """IPv6 spellings that carry `v4` and are global on their own, so only
    unwrapping the IPv4 inside can block them. (6to4, Teredo and the local
    NAT64 prefix are already non-global as a whole, so they test nothing.)"""
    n = int(v4)
    return {
        "mapped": ipaddress.IPv6Address(f"::ffff:{v4}"),
        "compatible": ipaddress.IPv6Address(n),
        "translated": ipaddress.IPv6Address(f"::ffff:0:{v4}"),
        "nat64": ipaddress.IPv6Address(f"64:ff9b::{v4}"),
        "isatap": ipaddress.IPv6Address((0x2600 << 112) | (0x00005EFE << 32) | n),
    }


def test_every_wrapper_is_global_around_a_public_address():
    """Guards the list above: a wrapper the blocklist refuses for its own
    prefix would pass the property below without checking any unwrapping."""
    public = ipaddress.IPv4Address("8.8.8.8")
    for form, v6 in v6_wrappers(public).items():
        assert v6.is_global, form


@given(blocked_v4(), st.data())
def test_ipv6_never_unblocks_a_blocked_ipv4(v4, data):
    assert not ssrf.is_ip_allowed(str(v4))
    form, v6 = data.draw(st.sampled_from(sorted(v6_wrappers(v4).items())))
    event(f"form={form}")
    note(f"{v4} as {form}: {v6}")
    assert not ssrf.is_ip_allowed(str(v6)), f"{form} spelling of {v4} is allowed"


@given(st.one_of(st.ip_addresses(v=4), st.ip_addresses(v=6), blocked_v4()))
def test_spelling_does_not_change_the_verdict(ip):
    spellings = {str(ip), ip.exploded, ip.exploded.upper(), str(ip).upper()}
    verdicts = {s: ssrf.is_ip_allowed(s) for s in spellings}
    note(verdicts)
    assert len(set(verdicts.values())) == 1
    if verdicts[str(ip)]:
        assert ip.is_global and not ip.is_multicast


PUBLIC_IPS = ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]

hosts = st.one_of(
    st.sampled_from(["example.com", "a.example", "EXAMPLE.com", "x-y.example."]),
    st.from_regex(r"[a-z0-9]{1,8}(\.[a-z0-9]{1,8}){0,2}", fullmatch=True),
    st.ip_addresses().map(lambda ip: f"[{ip}]" if ip.version == 6 else str(ip)),
)
ports = st.one_of(
    st.just(""),
    st.integers(0, 70000).map(lambda p: f":{p}"),
    st.sampled_from([":", ":-1", ": 443", ":+443", ":0x1bb", ":٤٤٣", ":443a"]),
)
URL_CHARS = st.sampled_from(list("abc/._-~%?#&=@:;\\ ") + ["%2e", "%2F", "é", "\t"])
url_tail = st.lists(URL_CHARS, max_size=12).map("".join)


@st.composite
def fetch_urls(draw) -> str:
    scheme = draw(st.sampled_from(["https", "https", "http", "HTTPS", "ftp", ""]))
    userinfo = draw(st.sampled_from(["", "", "user@", "u:p@", "@"]))
    return f"{scheme}://{userinfo}{draw(hosts)}{draw(ports)}/{draw(url_tail)}"


def _validate(url: str, ips: list[str]) -> ssrf.ValidatedURL:
    async def resolve(hostname: str, port: int = 443) -> list[str]:
        return ips

    # Pinned mode only: proxy-trust mode skips resolution by design.
    with (
        patch.object(ssrf, "resolve_hostname", resolve),
        patch.object(ssrf.fastmcp.settings, "ssrf_trust_proxy", False),
    ):
        return asyncio.run(ssrf.validate_url(url))


@given(st.one_of(fetch_urls(), st.text(max_size=40)), st.sampled_from(PUBLIC_IPS))
def test_validate_url_fails_only_with_ssrf_error(url, ip):
    note(url)
    try:
        _validate(url, [ip])
    except ssrf.SSRFError:
        event("refused")
    else:
        event("accepted")


@given(fetch_urls(), st.lists(st.sampled_from(PUBLIC_IPS), min_size=1, unique=True))
def test_pinned_request_goes_where_validation_looked(url, ips):
    try:
        validated = _validate(url, ips)
    except ssrf.SSRFError:
        return
    note(f"{url} -> {validated}")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert validated.port == (443 if parsed.port is None else parsed.port)
    assert 1 <= validated.port <= 65535
    expected_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    targets = ssrf._build_fetch_targets(validated)
    assert [t.host_header for t in targets] == [validated.hostname] * len(ips)
    for ip, target in zip(validated.resolved_ips, targets, strict=True):
        dialed = httpx2.URL(target.url)
        note(f"dials {dialed!r}")
        assert ipaddress.ip_address(dialed.host) == ipaddress.ip_address(ip)
        assert (dialed.port or 443) == validated.port
        assert dialed.raw_path.decode() == (expected_path or "/") or (
            # httpx percent-encodes what urlparse left raw; the bytes on the
            # wire must still decode to the same path.
            httpx2.URL(f"https://h{expected_path or '/'}").raw_path == dialed.raw_path
        )


# ---------------------------------------------------------------------------
# Redirect URIs
# ---------------------------------------------------------------------------

labels = st.from_regex(r"[a-z][a-z0-9-]{0,6}", fullmatch=True)


@st.composite
def patterns(draw) -> str:
    """Allowlist entries the docs describe: exact, port, subdomain and path
    wildcards, loopback, custom schemes."""
    scheme = draw(st.sampled_from(["https", "https", "http", "myapp"]))
    if draw(st.integers(0, 2)) == 0:
        host = draw(st.sampled_from(["localhost", "127.0.0.1", "[::1]"]))
    else:
        host = ".".join(draw(st.lists(labels, min_size=2, max_size=3)))
        if draw(st.integers(0, 3)) == 0:
            host = f"*.{host}"
    port = draw(st.sampled_from(["", "", ":*", ":8080", ":443"]))
    path = "/" + "/".join(draw(st.lists(labels, max_size=3)))
    shape = draw(st.integers(0, 3))
    if shape == 0:
        path = path.rstrip("/") + "/*"
    elif shape == 1:
        path = draw(st.sampled_from(["/", "/*"]))
    query = draw(st.sampled_from(["", "", "?x=1", "?a=b&c"]))
    return f"{scheme}://{host}{port}{path}{query}"


def _pattern_host(pattern: str) -> str:
    netloc = urlparse(pattern).netloc
    host = netloc[1 : netloc.index("]")] if netloc.startswith("[") else netloc
    return host.rsplit(":", 1)[0] if ":" in host and "[" not in netloc else host


def _host_allowed(host: str | None, pattern_host: str) -> bool:
    if host is None:
        return False
    host, pattern_host = host.lower().strip("[]"), pattern_host.lower()
    if pattern_host.startswith("*."):
        return host.endswith(pattern_host[1:]) and host != pattern_host[2:]
    return host == pattern_host


ATTACK = st.sampled_from(
    [
        "@evil.example",
        "\\@evil.example",
        "\\evil.example",
        "%40evil.example",
        ".evil.example",
        "evil.example",
        "#@evil.example",
        "?@evil.example",
        "/../",
        "/%2e%2e/",
        ":@evil.example",
        "%00",
        "\t",
        "//evil.example",
        "",
    ]
)


# What has followed a trusted host in real allowlist bypasses.
AFTER_HOST = st.sampled_from(
    [
        "\\@evil.example",
        "@evil.example",
        ".evil.example",
        "%40evil.example",
        "\\evil.example",
        ":@evil.example",
    ]
)


@st.composite
def candidates(draw, pattern: str) -> str:
    """URIs built from a pattern: fill the wildcards, then splice in
    spellings that have broken redirect allowlists elsewhere."""
    uri = pattern
    # A subdomain wildcard filled with a label, with nothing, or with a bare
    # prefix that only a careless suffix check would accept.
    uri = uri.replace("*.", draw(st.sampled_from(["", "evil", "x."])), 1)
    uri = uri.replace(":*", f":{draw(st.integers(1, 65535))}", 1)
    uri = uri.replace("*", "/".join(draw(st.lists(labels, max_size=2))), 1)
    scheme, rest = uri.split("://", 1)
    split = len(rest) if "/" not in rest else rest.index("/")
    authority, path = rest[:split], rest[split:]
    where = draw(
        # The host boundary is where bypasses live, so it gets the most draws.
        st.sampled_from(
            [
                "none",
                "userinfo",
                "after host",
                "after host",
                "after host",
                "path",
                "anywhere",
            ]
        )
    )
    attack = draw(ATTACK)
    if where == "none":
        pass
    elif where == "userinfo":
        authority = attack.lstrip("\\@") + "@" + authority if attack else authority
    elif where == "after host":
        authority += draw(AFTER_HOST)
    elif where == "path":
        path = attack + path
    uri = f"{scheme}://{authority}{path}"
    if where == "anywhere":
        cut = draw(st.integers(0, len(uri)))
        uri = uri[:cut] + attack + uri[cut:]
    return uri


def _hosts_seen(uri: str) -> dict[str, tuple[str | None, bool]]:
    """(host, has userinfo) as three different URL parsers read `uri`."""
    seen: dict[str, tuple[str | None, bool]] = {}
    parsed = urlparse(uri)
    seen["urlparse"] = (parsed.hostname, parsed.username is not None)
    try:
        url = AnyUrl(uri)
        seen["whatwg"] = (url.host, url.username is not None)
    except ValidationError:
        pass
    try:
        hx = httpx2.URL(uri)
        seen["httpx"] = (hx.host or None, bool(hx.userinfo))
    except (httpx2.InvalidURL, ValueError):
        pass
    return seen


@given(st.data())
def test_a_match_lands_on_the_patterns_host(data):
    pattern = data.draw(patterns(), label="pattern")
    uri = data.draw(candidates(pattern), label="uri")
    if not matches_allowed_pattern(uri, pattern):
        event("rejected")
        return
    event("matched")
    if urlparse(pattern).scheme == "myapp":
        return  # custom schemes have no host a browser resolves
    want = _pattern_host(pattern)
    for parser, (host, userinfo) in _hosts_seen(uri).items():
        note(f"{parser}: host={host!r} userinfo={userinfo}")
        # Only the browser's reading of userinfo matters; urlparse keeps
        # characters (a tab before `@`) that a browser strips first.
        assert not (userinfo and parser == "whatwg"), "a browser sees userinfo"
        assert _host_allowed(host, want), f"{parser} lands on {host!r}, not {want!r}"


@st.composite
def exact_uris(draw) -> str:
    """A registered redirect URI with no wildcard, in plain ASCII."""
    scheme = draw(st.sampled_from(["https", "http"]))
    host = draw(
        st.one_of(
            st.lists(labels, min_size=2, max_size=3).map(".".join),
            st.sampled_from(["localhost", "127.0.0.1"]),
        )
    )
    port = draw(st.sampled_from(["", ":8443", ":3000"]))
    path = "/" + "/".join(draw(st.lists(labels, max_size=3)))
    query = draw(st.sampled_from(["", "?x=1", "?a=b&c=d"]))
    return f"{scheme}://{host}{port}{path}{query}"


def _root_path_probe() -> bool:
    """https://github.com/PrefectHQ/fastmcp/issues/6689"""
    return matches_allowed_pattern("https://a.example/x", "https://a.example/")


def _changes(uri: str) -> st.SearchStrategy[tuple[str, str]]:
    """Edits that make a URI a different redirect target."""
    p = urlparse(uri)
    base = f"{p.scheme}://{p.netloc}{p.path}"
    q = f"?{p.query}" if p.query else ""
    edits = {
        "added query": base + (q + "&y=2" if q else "?y=2"),
        "changed path": f"{p.scheme}://{p.netloc}{p.path}x{q}",
        "child path": f"{p.scheme}://{p.netloc}{p.path}/x{q}",
        "fragment": uri + "#f",
        "userinfo": f"{p.scheme}://u@{p.netloc}{p.path}{q}",
        "scheme": f"{'http' if p.scheme == 'https' else 'https'}://{p.netloc}{p.path}{q}",
    }
    if q:
        edits["dropped query"] = base
    if p.hostname not in ("localhost", "127.0.0.1"):
        edits["port"] = f"{p.scheme}://{p.hostname}:9{p.port or 0}{p.path}{q}"
        edits["subdomain"] = f"{p.scheme}://x.{p.netloc}{p.path}{q}"
    return st.sampled_from(sorted(edits.items()))


@given(st.data())
def test_an_exact_pattern_matches_only_itself(data):
    uri = data.draw(exact_uris(), label="registered")
    assert matches_allowed_pattern(uri, uri), "a registered URI does not match itself"
    edit, other = data.draw(_changes(uri), label="edit")
    event(edit)
    p = urlparse(uri)
    if (
        edit in ("changed path", "child path")
        and p.path == "/"
        and p.port is None
        and is_loopback_host(p.hostname)
    ):
        # By design since #6703: a portless loopback pattern already accepts
        # any port (RFC 8252 §7.3), so its root path accepts any path too.
        event("by design: portless loopback root accepts any path")
        return
    if (
        edit in ("changed path", "child path")
        and p.path == "/"
        and still_open("#6689", _root_path_probe)
    ):
        event("known: #6689")
        return
    assert not matches_allowed_pattern(other, uri), f"{edit} still matches"


param_names = st.sampled_from(["code", "state", "error", "error_description", "x"])
param_values = st.text(
    st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=8
)
query_segments = st.lists(
    st.one_of(
        st.sampled_from(
            [
                "iss=a",
                "iss=b",
                "iss",
                "flag",
                "k=%FF",
                "k=v",
                "=v",
                "",
                "state=s",
                "code=z",
            ]
        ),
        st.from_regex(r"[a-z]{1,3}=[a-z0-9%]{0,4}", fullmatch=True),
    ),
    max_size=4,
)


def _repeated_param_probe() -> bool:
    """https://github.com/PrefectHQ/fastmcp/issues/6690"""
    out = build_client_redirect("https://c.example/cb?state=a", {"state": "b"}, iss="i")
    return out.count("state=") > 1


@given(
    st.sampled_from(
        [
            "https://c.example/cb",
            "http://localhost:3000/cb",
            "myapp://cb",
            "myapp:///cb",
            "com.example.app:/cb",
            "https://c.example",
        ]
    ),
    query_segments,
    st.dictionaries(param_names, param_values, max_size=3),
    param_values.filter(bool),
)
def test_client_redirect_carries_each_parameter_once(base, segments, params, iss):
    registered = base + ("?" + "&".join(segments) if segments else "")
    out = build_client_redirect(registered, params, iss=iss)
    note(f"{registered} -> {out}")
    before, after = urlparse(registered), urlparse(out)
    assert (after.scheme, after.netloc, after.path) == (
        before.scheme,
        before.netloc,
        before.path,
    )
    assert out.startswith(registered.split("?")[0])
    pairs = parse_qsl(after.query, keep_blank_values=True)
    assert [v for k, v in pairs if k == "iss"] == [iss]
    registered_keys = {seg.split("=", 1)[0] for seg in segments}
    for key, value in params.items():
        values = [v for k, v in pairs if k == key]
        if key in registered_keys and still_open("#6690", _repeated_param_probe):
            event("known: #6690")
            assert values[-1:] == [value]
            continue
        assert values == [value], f"{key} appears {len(values)} times"
    # Registered segments the response does not replace stay as written.
    replaced = {"iss", *params}
    kept = [
        s for s in after.query.split("&") if s and s.split("=", 1)[0] not in replaced
    ]
    original = [s for s in segments if s and s.split("=", 1)[0] not in replaced]
    assert kept[: len(original)] == original, "the registered query was rewritten"


# ---------------------------------------------------------------------------
# CIMD
# ---------------------------------------------------------------------------

scopes = st.lists(st.sampled_from(["read", "write", "admin", "a:b", "", " "]))


@given(scopes, st.one_of(st.none(), scopes.map(" ".join)))
def test_document_scopes_never_widen_the_servers(valid, requested):
    manager = CIMDClientManager(valid_scopes=valid)
    out = manager.restrict_scope(requested)
    note(out)
    if out is None:
        return
    assert requested is not None
    granted = out.split(" ")
    assert all(s and s in valid for s in granted)
    assert set(granted) <= set(requested.split(" "))
    assert manager.restrict_scope(out) == out


header_values = st.one_of(
    st.text(max_size=30),
    st.sampled_from(
        [
            "max-age=60",
            "max-age=-5",
            "max-age=1e9",
            "no-store",
            "no-cache",
            "max-age=99999999999999999999",
            "max-age= 7, no-cache",
            "MAX-AGE=3",
        ]
    ),
    st.datetimes(
        min_value=datetime(1, 1, 2),
        max_value=datetime(9999, 12, 30),
        timezones=st.just(UTC),
    ).map(format_datetime),
)


@given(
    st.dictionaries(
        st.sampled_from(["Cache-Control", "cache-control", "Expires", "ETag", "Age"]),
        header_values,
        max_size=3,
    ),
    st.floats(0, 4e9),
)
def test_cache_headers_never_break_a_fetch(headers, now):
    note(headers)
    policy = CIMDFetcher()._parse_cache_policy(headers, now)
    assert policy.freshness_lifetime >= 0
