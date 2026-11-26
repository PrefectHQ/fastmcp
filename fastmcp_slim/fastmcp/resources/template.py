"""Resource template functionality."""

from __future__ import annotations

import functools
import inspect
import logging
import re
from collections.abc import Callable, Collection
from types import UnionType
from typing import Annotated, Any, ClassVar, Union, get_args, get_origin
from urllib.parse import parse_qs, quote, unquote, unquote_plus

from mcp_types import Annotations, Icon
from mcp_types import ResourceTemplate as SDKResourceTemplate
from pydantic import (
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    validate_call,
)
from pydantic.json_schema import SkipJsonSchema

from fastmcp.exceptions import ValidationError
from fastmcp.resources.base import (
    Resource,
    ResourceResult,
    convert_raw_to_resource_result,
)
from fastmcp.resources.security import (
    INHERIT_SECURITY,
    InheritSecurity,
    ResourceSecurity,
)
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.mime import resolve_ui_mime_type
from fastmcp.utilities.types import get_cached_typeadapter


def extract_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names from RFC 6570 query expressions.

    Both the `{?param1,param2}` form and the `{&param}` continuation form
    contribute names, and a template may carry several query expressions.
    The explode modifier is stripped, so `{?tags*}` yields `{"tags"}`. Use
    `extract_exploded_query_params` to find which names carried it.
    """
    names: set[str] = set()
    for match in re.finditer(r"\{[?&]([^}]+)\}", uri_template):
        names.update(p.strip().removesuffix("*") for p in match.group(1).split(","))
    return names


def _is_list_only(annotation: Any) -> bool:
    """Whether an annotation accepts only lists, optionally wrapped or nullable."""
    if annotation is list:
        return True
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is Annotated:
        return _is_list_only(get_args(annotation)[0])
    if origin in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        return bool(members) and all(_is_list_only(arg) for arg in members)
    return False


def _accepts_exploded_query(annotation: Any) -> bool:
    """Whether an annotation explicitly accepts the list produced by explode."""
    if (
        annotation is Any
        or annotation is object
        or annotation is inspect.Parameter.empty
        or isinstance(annotation, str)
    ):
        return True
    if annotation is list:
        return True
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is Annotated:
        return _accepts_exploded_query(get_args(annotation)[0])
    if origin in (Union, UnionType):
        return any(_accepts_exploded_query(arg) for arg in get_args(annotation))
    return False


def extract_exploded_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names declared with the RFC 6570 explode modifier.

    `{?tags*}` (or `{&tags*}`) marks `tags` as repeatable — `?tags=a&tags=b`
    collects into a list rather than collapsing to the first value.
    """
    exploded: set[str] = set()
    for match in re.finditer(r"\{[?&]([^}]+)\}", uri_template):
        names = [p.strip() for p in match.group(1).split(",")]
        exploded.update(n.removesuffix("*") for n in names if n.endswith("*"))
    return exploded


def _has_literal_query(uri_template: str) -> bool:
    """Whether the template writes `?` as literal text, outside a `{?...}` block."""
    return uri_template.count("?") > uri_template.count("{?")


# RFC 3986 reserved characters, which stay as written in a template literal.
_RESERVED = ":/?#[]@!$&'()*+,;="
_PCT_TRIPLET = re.compile(r"%[0-9A-Fa-f]{2}")
_HEX_DIGITS = "0123456789abcdefABCDEF"


def encode_literal(text: str) -> str:
    """Percent-encode literal template text per RFC 6570 section 3.1.

    A template may be written with characters the URI grammar does not allow —
    a non-ASCII `ucschar` (`file:///docs/café/`) or a space — and section 3.1
    requires those to be UTF-8 percent-encoded in the expanded URI. Reserved
    and unreserved characters are structural and stay as written, and existing
    `%XX` triplets pass through so an already-encoded literal is not encoded
    twice.

    A resource URI reaches the server as an `AnyUrl`, which percent-encodes it,
    so the encoded form is what matching has to line up with.
    """
    out: list[str] = []
    last = 0
    for match in _PCT_TRIPLET.finditer(text):
        out.append(quote(text[last : match.start()], safe=_RESERVED))
        out.append(match.group())
        last = match.end()
    out.append(quote(text[last:], safe=_RESERVED))
    return "".join(out)


def _literal_pattern(text: str) -> str:
    """Regex for a literal run that matches it either as written or percent-encoded.

    `AnyUrl` percent-encodes some characters that section 3.1 requires encoding
    (non-ASCII, a space in a hierarchical path) but leaves others as written
    (`|`, `^`, `\\`, a stray `%`, a space in an opaque path), and in-process
    reads skip `AnyUrl` entirely. Each character that encoding would change
    therefore matches in both forms, with the hex digits in either case.
    """

    def chars(run: str) -> str:
        out = []
        for ch in run:
            encoded = quote(ch, safe=_RESERVED)
            if encoded == ch:
                out.append(re.escape(ch))
            else:
                out.append(f"(?:{re.escape(ch)}|(?i:{re.escape(encoded)}))")
        return "".join(out)

    pattern: list[str] = []
    last = 0
    for match in _PCT_TRIPLET.finditer(text):
        pattern.append(chars(text[last : match.start()]))
        pattern.append(f"(?i:{re.escape(match.group())})")
        last = match.end()
    pattern.append(chars(text[last:]))
    return "".join(pattern)


def template_param_key(name: str) -> str:
    """Normalize a template variable name into the key used for matched params.

    Regex group names must be valid Python identifiers, so every character a
    legal template variable may carry that an identifier cannot (`$`, `.`, a
    leading digit, ...) becomes `_`. Hyphens already normalized this way.
    Consumers that map matched params back onto route parameters (OpenAPI
    resource templates) use the same function for the lookup.
    """
    key = re.sub(r"\W", "_", name)
    if not key.isidentifier():
        # \w also matches Unicode word characters that identifiers reject
        # (e.g. `²`), so fall back to ASCII-only sanitization.
        key = re.sub(r"[^a-zA-Z0-9_]", "_", key)
    if not key or key[0].isdigit():
        key = f"_{key}"
    return key


def _template_placeholders(
    template: str,
) -> list[tuple[str, str, str, bool, bool]]:
    """Path placeholders of a template, in order.

    Each entry is ``(logical name, regex group, body pattern, in query,
    wildcard)``. The logical name is the variable name without a trailing
    ``*`` wildcard marker, so ``{tenant}`` and ``{tenant*}`` are the same
    parameter. Query-expression placeholders (``{?...}`` / ``{&...}``) are
    stripped first: they are read from the query string, not captured by the
    path pattern.
    """
    template_without_query = re.sub(r"\{[?&][^}]+\}", "", template)
    parts = re.split(r"(\{[^}]+\})", template_without_query)
    placeholders: list[tuple[str, str, str, bool, bool]] = []
    # Past a literal `?` a placeholder is a query value: it may be empty, may
    # hold `/`, and ends at the next `&`.
    in_query = False
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            wildcard = name.endswith("*")
            if in_query:
                group = template_param_key(name.removesuffix("*"))
                body = "[^&]*"
            elif name.endswith("*"):
                group = template_param_key(name[:-1])
                body = ".+"
            else:
                group = template_param_key(name)
                body = "[^/]+"
            placeholders.append(
                (name.removesuffix("*"), group, body, in_query, wildcard)
            )
        else:
            in_query = in_query or "?" in part
    return placeholders


def _repeat_plan(template: str) -> dict[str, list[str]]:
    """Capture names for repeated occurrences of a parameter.

    Maps a first-occurrence group to one separate capture name per later
    occurrence, in order. Every repeat gets its own capture group, never a
    backreference: a backreference would demand identical raw spellings,
    but two occurrences of one value can legitimately differ on the wire —
    a wildcard occurrence emits ``a/b`` where a simple one emits ``a%2Fb``,
    and percent-hex case is insignificant (``a%2Fb`` and ``a%2fb`` decode
    to the same value). `match_uri_template` compares the captures after
    decoding instead, and re-splits the span when the greedy captures
    chose an unequal split (see `_resplit_slots`).

    Repeats are keyed by the logical variable name, not the sanitized group:
    distinct names that normalize to the same group (``{x.y}`` and ``{x_y}``)
    are a collision, not a repeat, and still make the template unmatchable.
    Separate-capture names avoid every group name the template already uses,
    so a parameter literally named ``<group>__repeat_1`` cannot collide with
    a generated one.
    """
    placeholders = _template_placeholders(template)
    used = {group for _, group, _, _, _ in placeholders}
    seen: set[str] = set()
    counts: dict[str, int] = {}
    plan: dict[str, list[str]] = {}
    for logical, group, _body, _in_query, _wildcard in placeholders:
        if logical not in seen:
            seen.add(logical)
            continue
        index = counts.get(logical, 0) + 1
        counts[logical] = index
        name = f"{group}__repeat_{index}"
        while name in used:
            index += 1
            counts[logical] = index
            name = f"{group}__repeat_{index}"
        used.add(name)
        plan.setdefault(group, []).append(name)
    return plan


def _repeated_group_names(template: str) -> dict[str, list[str]]:
    """Separate-capture names for repeated occurrences, per first group.

    `match_uri_template` compares these captures after decoding and drops
    them from the returned parameters.
    """
    return _repeat_plan(template)


def _named_captures(template: str) -> list[tuple[str, str, str]]:
    """Every named capture in `build_regex`'s pattern, in template order.

    Each entry is ``(group, body, logical name)``: the first occurrence of
    a parameter followed by its repeats. Bodies are the regex fragments
    `build_regex` uses, so a re-split candidate can be checked against the
    same constraints the pattern enforces.
    """
    plan = _repeat_plan(template)
    placeholders = _template_placeholders(template)
    captures: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    repeat_index: dict[str, int] = {}
    for logical, group, body, _in_query, _wildcard in placeholders:
        if logical not in seen:
            seen.add(logical)
            captures.append((group, body, logical))
            continue
        names = plan.get(group, [])
        index = repeat_index.get(logical, 0)
        repeat_index[logical] = index + 1
        if index < len(names):
            captures.append((names[index], body, logical))
    return captures


def _literal_max_len(text: str) -> int:
    """Longest raw spelling of a template literal (see `_literal_pattern`).

    Each percent-triplet in the text is exactly 3 characters; any other
    character is 1 character as written or 3 per UTF-8 byte as its
    percent-encoding (`é` is two bytes, so `%C3%A9` is 6 characters).
    """
    length = 0
    last = 0
    for match in _PCT_TRIPLET.finditer(text):
        for ch in text[last : match.start()]:
            length += (
                1 if quote(ch, safe=_RESERVED) == ch else 3 * len(ch.encode("utf-8"))
            )
        length += 3
        last = match.end()
    for ch in text[last:]:
        length += 1 if quote(ch, safe=_RESERVED) == ch else 3 * len(ch.encode("utf-8"))
    return length


def _capture_delimiters(template: str) -> list[str]:
    """Literal template text between consecutive named captures.

    Aligned with `_named_captures`: entry ``i`` is the literal between
    captures ``i`` and ``i + 1``. The text is taken from the template, not
    from a match: a literal can reach the wire in several spellings (a raw
    character or its percent-encoding, in either hex case), and the greedy
    match may have chosen a different spelling — or a wrong span — so the
    spelling found between two captures cannot be trusted. Callers compile
    each entry with `_literal_pattern`, which accepts every spelling.
    """
    template_without_query = re.sub(r"\{[?&][^}]+\}", "", template)
    parts = re.split(r"(\{[^}]+\})", template_without_query)
    delimiters: list[str] = []
    pending = ""
    seen_placeholder = False
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            if seen_placeholder:
                delimiters.append(pending)
            seen_placeholder = True
            pending = ""
        else:
            pending += part
    return delimiters


def _literal_has_clean_boundaries(text: str) -> bool:
    """Whether a literal's matches always start and end on unit boundaries.

    The suffix and decoded-length prunes in `_resplit_slots` read a piece's
    decoded value from a slice of the decoded span, which is only valid
    when the delimiter match starts and ends outside any `%XX` triple or
    multi-byte sequence. A delimiter match can straddle a triple only
    where a `%` meets hex digits: a bare `%` at the end of the literal
    (or one followed by a hex digit) can be the `%` of a triple the next
    piece's hex digits complete — a `%` literal next to a value like
    `41` reads as `%41` — and a literal whose first two characters are
    hex digits can be the tail of a triple the previous piece's trailing
    `%` starts. A `%XX` triplet written in the literal matches as a
    unit, and a hex digit anywhere else in a literal cannot land inside
    a triple on its own (the character before it would have to match
    `%`), so those are safe. A span whose delimiters include an unsafe
    literal uses the plain bounded search instead, without those prunes.
    """
    triplets = [match.span() for match in _PCT_TRIPLET.finditer(text)]

    def _in_triplet(index: int) -> bool:
        return any(start <= index < end for start, end in triplets)

    for index, char in enumerate(text):
        if _in_triplet(index):
            continue
        if char == "%":
            if index == len(text) - 1 or text[index + 1] in _HEX_DIGITS:
                return False
        elif (
            char in _HEX_DIGITS
            and index == 0
            and (len(text) == 1 or text[1] in _HEX_DIGITS)
        ):
            return False
    return True


def _literal_has_decoded_boundaries(text: str) -> bool:
    """Whether a literal's matches always start and end on decoded
    (UTF-8 character) boundaries.

    A decoded slice of the span is only a piece's decoded value when
    the piece starts and ends at a character boundary. A literal whose
    first unit is a `%XX` triple holding a UTF-8 continuation byte
    (0x80-0xBF) can start a match in the middle of a character (the
    lead byte sits in the previous piece), and one whose last unit is a
    triple holding a lead byte (0xC2-0xF4) can end one there (the
    continuations sit in the next piece). An empty literal matches
    anywhere, so it never has boundaries. A raw character is always
    complete, and a triple holding any other byte decodes to a whole
    character (or a replacement character) on its own.
    """
    if not text:
        return False
    triplets = list(_PCT_TRIPLET.finditer(text))
    first = triplets[0] if triplets and triplets[0].start() == 0 else None
    if first is not None and 0x80 <= int(first.group()[1:], 16) <= 0xBF:
        return False
    last = triplets[-1] if triplets and triplets[-1].end() == len(text) else None
    return not (last is not None and 0xC2 <= int(last.group()[1:], 16) <= 0xF4)


def _decoded_lengths(span_text: str) -> tuple[list[int], list[bool]] | None:
    """Decoded length of every prefix of a span, plus whether each prefix
    ends on a UTF-8 character boundary, or None if either cannot be
    computed. The count at a raw position is the length of `unquote` of
    that prefix; it is built in one forward pass so splitting a long
    value does not decode every prefix from scratch. Positions inside a
    `%XX` triple count the partial triple as literal characters, exactly
    as `unquote` leaves it (`%C` is two characters, `%C3` is one
    replacement character), so the counts can dip at a triple boundary —
    they are not monotonic. A prefix ends on a character boundary when
    the incremental decoder holds no pending bytes there: a prefix
    ending after `%C3` is not one, even though the triple is complete,
    because that byte is still half of an `é` — its decode is a
    replacement character where the full span has the `é`. The counts
    are spot-checked against `unquote` at a few positions; a mismatch
    means the map cannot be trusted, so the caller falls back to
    decoding pieces directly.
    """
    import codecs

    if "%" not in span_text:
        # No percent-escapes: unquote is the identity, so every prefix
        # decodes to itself and every position is a character boundary.
        return list(range(len(span_text) + 1)), [True] * (len(span_text) + 1)
    try:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        counts = [0] * (len(span_text) + 1)
        char_boundary = [True] * (len(span_text) + 1)
        completed = 0
        index = 0
        length = len(span_text)
        while index < length:
            char = span_text[index]
            if (
                char == "%"
                and index + 2 < length
                and span_text[index + 1] in _HEX_DIGITS
                and span_text[index + 2] in _HEX_DIGITS
            ):
                # A triple contributes one byte; its partial prefixes are
                # literal characters to unquote.
                counts[index + 1] = counts[index] + 1
                counts[index + 2] = counts[index] + 2
                completed += len(
                    decoder.decode(bytes([int(span_text[index + 1 : index + 3], 16)]))
                )
                pending = decoder.getstate()[0]
                counts[index + 3] = completed + len(pending.decode("utf-8", "replace"))
                char_boundary[index + 3] = not pending
                # Inside the triple the prefix ends mid-byte, so it is
                # not a character boundary either.
                char_boundary[index + 1] = False
                char_boundary[index + 2] = False
                index += 3
                continue
            for byte in char.encode("utf-8"):
                completed += len(decoder.decode(bytes([byte])))
            pending = decoder.getstate()[0]
            counts[index + 1] = completed + len(pending.decode("utf-8", "replace"))
            char_boundary[index + 1] = not pending
            index += 1
        for position in (length // 3, length // 2, (2 * length) // 3):
            if counts[position] != len(unquote(span_text[:position])):
                return None
        return counts, char_boundary
    except (UnicodeError, ValueError, IndexError):
        return None


class _SearchBudgetExceeded(Exception):
    """The re-split search exceeded its step budget (see `_resplit_slots`)."""


# Upper bound on the work `_resplit_slots` spends on one span. The search
# enumerates delimiter placements, which grows with the span length, and
# resource lookups run synchronously — a non-matching URI must not block
# the event loop for seconds. Realistic templates and URIs need only a
# few hundred steps; past the budget the span is treated as unmatchable.
# Each step is now O(1)-ish (decoded lengths come from a precomputed map,
# comparisons are decoded-slice compares, delimiter matching is C-level),
# so 50,000 steps bounds the wall time to roughly a tenth of a second
# while still accepting valid values of ten to twenty thousand characters
# in adjacent repeats, which need one step per raw position to reach the
# decoded midpoint.
_RESPLIT_STEP_BUDGET = 50_000


def _resplit_slots(
    span_text: str,
    slots: list[tuple[str, str, str]],
    delimiters: list[tuple[re.Pattern[str], int, int]],
    *,
    exact_boundaries: bool,
    substring_prune: bool,
) -> list[tuple[str, int, int]] | None:
    """Find a consistent split of a span holding repeated parameters.

    When a value contains the delimiter between occurrences, a greedy
    split can be unequal even though an equal one exists, and the
    occurrences may spell the value differently (`{tenant*}` emits ``a/b``
    where `{tenant}` emits ``a%2Fb``), so equality has to be judged after
    decoding. Every way of placing the delimiters inside the span is tried
    in order, including re-splitting parameters that only separate the
    repeats: a greedy capture of an intervening parameter can itself be
    wrong, so its piece is re-chosen too. Delimiters are the template literals between
    captures, matched with `_literal_pattern`, so any spelling of a
    literal (raw or percent-encoded, either hex case) is a valid split
    point — the spelling the greedy match happened to choose is not the
    only one. Every spelling length is tried at each position: a literal
    can match a shorter spelling first (`%` before `%25`), and only
    trying every length finds the split the template actually allows. A candidate is accepted only when every piece satisfies its
    capture's body and every repeated parameter in the span decodes to
    the same value at all its occurrences — a split that repairs one
    parameter must not break another. The span's outer boundaries never
    move, so captures outside it are unaffected, and acceptance rests on
    bodies plus decoded equality, so this can never accept a URI the
    template forbids. A repeated parameter's value is kept as the raw
    span of its defining occurrence, and decoded lengths come from a
    precomputed map over the span (`_decoded_lengths`), so comparing
    occurrences and checking lengths never decodes a prefix from scratch
    — splitting a long value costs one forward pass, not one decode per
    candidate split.

    Once the last slot's parameter value is known, the decoded span must
    end with it, so branches whose value cannot appear at the end are
    pruned before the intervening slots are enumerated. A decoded-length
    check prunes the long adjacent-repeat case (`{x}{x}` with a long
    value), where the split can only be the decoded midpoint, without
    enumerating every position: the pieces' decoded lengths plus the
    delimiters' must sum to the decoded span's length, so once a piece's
    value is known the remaining slots must be able to account for
    exactly what is left. Both prunes, and the slice comparisons, are
    only sound when every delimiter match starts and ends on a
    percent-triple boundary — `exact_boundaries` (see
    `_literal_has_clean_boundaries`) says so. When it is false the
    search runs without them: a literal containing a bare `%`, or
    starting or ending in a hex digit, can match inside a value's
    `%XX` triple, so the decoded arithmetic would be wrong there, and
    the plain bounded search is the correct fallback. A third prune
    applies once a repeated parameter's value is set: every later
    occurrence decodes to the same value, so the value must appear in
    the decoded remainder, and a split whose value never appears later
    is rejected without enumerating the intervening slots — without it
    a long intervening parameter before a repeat exhausts the budget
    on ordinary valid URIs. That check needs decoded (UTF-8
    character) boundaries as well (`substring_prune`, see
    `_literal_has_decoded_boundaries`); otherwise it is skipped.

    Returns one ``(piece, start, end)`` entry per slot, with span-relative
    offsets, or None. The search is bounded by `_RESPLIT_STEP_BUDGET`
    steps; exceeding it also returns None.
    """
    bodies = [body for _, body, _ in slots]
    repeated = {
        logical
        for _, _, logical in slots
        if sum(1 for _, _, other in slots if other == logical) > 1
    }
    constrained = repeated
    steps = [0]
    # The decoded span, for the suffix prune below: the last slot's piece
    # is a suffix of the span, so once its parameter's value is known the
    # decoded span must end with that value. Without this check the search
    # enumerates every split of the intervening slots for each wrong first
    # piece, which exhausts the budget on ordinary valid URIs whose
    # repeated value is long and full of delimiters.
    decoded_span = unquote(span_text)
    decoded_len = len(decoded_span)
    # The prunes are sound when delimiter matches land on decoded
    # boundaries. The literal-based guard says so, but so does the span
    # itself: a span with no `%XX` triples has no triple for a match to
    # land inside, so every position is a decoded boundary regardless
    # of the literal — a single-hex-digit delimiter like `a` disables
    # the prunes only when the span actually contains a triple.
    if _PCT_TRIPLET.search(span_text) is None:
        exact_boundaries = True
        substring_prune = True
    # Decoded length of every raw position, for O(1) piece lengths and
    # slice comparisons. Only built (and only trusted) when the
    # delimiters' matches are guaranteed to land on unit boundaries;
    # otherwise the search below decodes pieces directly and skips the
    # prunes that read the map. It is also built for a short span when
    # the literal guard did not pass, so a concrete check of this span
    # (below) can decide instead of the conservative literal guard.
    decoded_map = (
        _decoded_lengths(span_text)
        if exact_boundaries or len(span_text) <= 8192
        else None
    )
    dec_len_at: list[int] | None = None
    decoded_boundary: list[bool] | None = None
    if decoded_map is not None:
        dec_len_at, char_boundary = decoded_map
        if len(dec_len_at) != len(span_text) + 1 or dec_len_at[-1] != decoded_len:
            dec_len_at = None
        else:
            # A decoded slice of the span is only a piece's decoded value
            # when both of the piece's ends fall on a decoded boundary:
            # outside any `%XX` triple, and at a UTF-8 character boundary
            # (a split after `%C3` is past a complete triple but still
            # half of an `é`). An empty delimiter can place a split
            # inside a triple or mid-character, and there the slice is a
            # different string of the same length, so comparisons fall
            # back to decoding the pieces directly when either end is not
            # a boundary.
            decoded_boundary = [
                char_boundary[position] for position in range(len(span_text) + 1)
            ]
            for triple in _PCT_TRIPLET.finditer(span_text):
                decoded_boundary[triple.start() + 1] = False
                decoded_boundary[triple.start() + 2] = False
    exact = dec_len_at is not None

    def _delimiter_matches_are_clean() -> bool:
        """Whether every non-empty delimiter match in this span starts
        and ends on a decoded boundary.

        A match starting or ending inside a triple or mid-character
        makes the piece on that side decode to something other than
        its decoded slice, which would make the prunes unsound. Only
        positions that are not decoded boundaries can be such a start
        or end, so only those are tested, and the test is bounded.
        """
        if decoded_boundary is None:
            return False
        non_boundary = [p for p, ok in enumerate(decoded_boundary) if not ok]
        if not non_boundary:
            return True
        calls = [0]
        for pattern, max_len, _decoded_len in delimiters:
            if max_len == 0:
                continue
            for position in non_boundary:
                for end in range(position, min(len(span_text), position + max_len) + 1):
                    calls[0] += 1
                    if calls[0] > 50_000:
                        return False
                    if pattern.fullmatch(span_text, position, end) is not None:
                        return False
                for start in range(max(0, position - max_len), position):
                    calls[0] += 1
                    if calls[0] > 50_000:
                        return False
                    if pattern.fullmatch(span_text, start, position) is not None:
                        return False
        return True

    if exact and not exact_boundaries:
        # Concrete check: the literal guard is conservative — a
        # delimiter like `a` only lands inside a triple when this span
        # actually puts one there. If no delimiter match in this span
        # starts or ends off a decoded boundary, the prunes are sound
        # for it and the map stays trusted; otherwise the map is
        # dropped and the search falls back to decoding pieces
        # directly. Empty delimiters match anywhere, so the substring
        # prune is only upgraded when every delimiter is non-empty.
        # The check itself is bounded: past its call budget it gives
        # up and the literal guard stands.
        if not _delimiter_matches_are_clean():
            dec_len_at = None
            decoded_boundary = None
            exact = False
        elif all(max_len > 0 for _, max_len, _ in delimiters):
            substring_prune = True

    def piece_decoded_len(start: int, end: int) -> int:
        if exact:
            assert dec_len_at is not None
            return dec_len_at[end] - dec_len_at[start]
        return len(unquote(span_text[start:end]))

    def pieces_equal(
        first_start: int, first_end: int, second_start: int, second_end: int
    ) -> bool:
        """Whether two raw pieces of this span decode to the same value.

        With the map, both pieces are slices of the decoded span, so the
        comparison is a C-level slice compare — but only when both pieces
        start and end on decoded boundaries. Otherwise both are decoded
        directly.
        """
        if exact:
            assert dec_len_at is not None
            assert decoded_boundary is not None
            if (
                decoded_boundary[first_start]
                and decoded_boundary[first_end]
                and decoded_boundary[second_start]
                and decoded_boundary[second_end]
            ):
                return (
                    decoded_span[dec_len_at[first_start] : dec_len_at[first_end]]
                    == decoded_span[dec_len_at[second_start] : dec_len_at[second_end]]
                )
        return unquote(span_text[first_start:first_end]) == unquote(
            span_text[second_start:second_end]
        )

    def check_budget() -> None:
        steps[0] += 1
        if steps[0] > _RESPLIT_STEP_BUDGET:
            raise _SearchBudgetExceeded

    def _feasible(
        from_index: int,
        decoded_consumed: int,
        targets: dict[str, tuple[int, int]],
    ) -> bool:
        """Whether slots[from_index:] can account for the decoded span left.

        A repeated parameter with a known value contributes exactly that
        value's length; any other slot contributes at least 1 character
        unless its body allows empty. When every remaining slot has a
        known value the total is exact, so the leftover must match it.
        The check is only meaningful when piece boundaries are unit
        boundaries, so without the map it always passes.
        """
        if not exact:
            return True
        assert dec_len_at is not None
        remaining = decoded_len - decoded_consumed
        minimum = 0
        exact_total = True
        for j in range(from_index, len(slots)):
            logical = slots[j][2]
            if logical in targets:
                target_start, target_end = targets[logical]
                minimum += dec_len_at[target_end] - dec_len_at[target_start]
            elif bodies[j] == "[^&]*":
                exact_total = False
            else:
                minimum += 1
                exact_total = False
            if j < len(slots) - 1:
                minimum += delimiters[j][2]
        if remaining < minimum:
            return False
        return not exact_total or remaining == minimum

    def _adjacent_pair_can_start(index: int, pos: int) -> bool:
        """Whether the span from `pos` can start with two adjacent
        occurrences of the same parameter.

        Slots[index] and slots[index+1] hold the same parameter, so the
        span from `pos` must start with that parameter's value, a
        delimiter, and the same value again. Every way of ending the
        first occurrence is tried, but the second occurrence is checked
        by a decoded-slice compare instead of being enumerated — so a
        long parameter before the pair does not re-enumerate the pair
        for every one of its splits. The scan costs no steps (it is a
        pre-check, not the search), so it only runs when the remaining
        span is short enough for it to stay cheap, and only when
        delimiter matches land on decoded boundaries (see
        `substring_prune`); otherwise it passes and the search decides.
        """
        if not substring_prune or not exact:
            return True
        assert dec_len_at is not None
        if decoded_len - dec_len_at[pos] > 2048:
            return True
        delimiter, max_len, _decoded_len = delimiters[index]
        start = pos
        while start <= len(span_text):
            found = delimiter.search(span_text, start)
            if found is None:
                break
            end_first = found.start()
            value = decoded_span[dec_len_at[pos] : dec_len_at[end_first]]
            for end in range(end_first, min(len(span_text), end_first + max_len) + 1):
                if delimiter.fullmatch(span_text, end_first, end) is None:
                    continue
                second_start = dec_len_at[end]
                if decoded_span[second_start : second_start + len(value)] == value:
                    return True
            start = end_first + 1
        return False

    # Candidate splits for the slot before the first adjacent repeated
    # pair, found by scanning the span once for two adjacent equal
    # single pieces. The anchored-pair pre-check costs O(pieces) per
    # intervening split, so it stops helping once the span passes a
    # few thousand characters and valid URIs are rejected; this scan
    # finds the pair's start up front instead, so the slot before the
    # pair only tries splits that put it there. A pair holding a
    # multi-piece value is invisible to the scan, so the full search
    # still runs when the constrained pass finds nothing.
    pair_index: int | None = None
    pair_ends: list[tuple[int, int]] | None = None
    for j in range(len(slots) - 1):
        if (
            j >= 1
            and slots[j][2] == slots[j + 1][2]
            and slots[j][2] not in {s[2] for s in slots[:j]}
        ):
            pair_index = j
            break
    if pair_index is not None:
        pair_delimiter, _pair_max, _pair_decoded = delimiters[pair_index]
        pair_body = slots[pair_index][1]
        pair_matches = list(pair_delimiter.finditer(span_text))
        piece_starts = [0, *[m.end() for m in pair_matches]]
        piece_ends = [m.start() for m in pair_matches] + [len(span_text)]
        starts: set[int] = set()
        for i in range(len(piece_starts) - 1):
            piece = span_text[piece_starts[i] : piece_ends[i]]
            nxt = span_text[piece_starts[i + 1] : piece_ends[i + 1]]
            if re.fullmatch(pair_body, piece) is not None and unquote(piece) == unquote(
                nxt
            ):
                starts.add(piece_starts[i])
        if starts:
            prev_delimiter, prev_max, _prev_decoded = delimiters[pair_index - 1]
            ends: list[tuple[int, int]] = []
            for s in sorted(starts):
                for length in range(1, prev_max + 1):
                    start = s - length
                    if (
                        start >= 0
                        and prev_delimiter.fullmatch(span_text, start, s) is not None
                    ):
                        ends.append((start, s))
            if ends:
                pair_ends = sorted(ends)

    def search(
        index: int,
        pos: int,
        targets: dict[str, tuple[int, int]],
        decoded_consumed: int,
    ) -> list[tuple[str, int, int]] | None:
        check_budget()
        if exact:
            assert dec_len_at is not None
            assert decoded_boundary is not None
            last_logical = slots[-1][2]
            if last_logical in targets:
                target_start, target_end = targets[last_logical]
                if decoded_boundary[target_start] and decoded_boundary[target_end]:
                    target_len = dec_len_at[target_end] - dec_len_at[target_start]
                    if (
                        decoded_span[dec_len_at[target_start] : dec_len_at[target_end]]
                        != decoded_span[decoded_len - target_len :]
                    ):
                        return None
        if not _feasible(index, decoded_consumed, targets):
            return None
        if (
            index + 1 < len(slots)
            and slots[index][2] == slots[index + 1][2]
            and slots[index][2] not in targets
            and not _adjacent_pair_can_start(index, pos)
        ):
            return None
        if index == len(slots) - 1:
            piece = span_text[pos:]
            if re.fullmatch(bodies[index], piece) is None:
                return None
            logical = slots[index][2]
            if logical in constrained and logical in targets:
                target_start, target_end = targets[logical]
                if not pieces_equal(pos, len(span_text), target_start, target_end):
                    return None
            return [(piece, pos, len(span_text))]
        delimiter, max_len, _decoded_len = delimiters[index]

        def attempt_split(
            start: int, allowed_ends: set[int] | None
        ) -> list[tuple[str, int, int]] | None:
            # Length first: the piece's decoded length, the target
            # comparison and the feasibility check all read the map in
            # O(1), so an infeasible split is rejected before the piece
            # is copied and regex-scanned — otherwise every candidate
            # costs O(piece) and a long span is quadratic in its length
            # even though the step budget bounds the number of candidates.
            logical = slots[index][2]
            new_targets = targets
            if logical in constrained:
                if logical in targets:
                    target_start, target_end = targets[logical]
                    if not pieces_equal(pos, start, target_start, target_end):
                        return None
                else:
                    new_targets = {**targets, logical: (pos, start)}
            child_consumed = (
                decoded_consumed + piece_decoded_len(pos, start) + delimiters[index][2]
            )
            if not _feasible(index + 1, child_consumed, new_targets):
                return None
            if (
                exact
                and substring_prune
                and logical in constrained
                and logical not in targets
            ):
                # Every later occurrence of this parameter decodes to
                # the same value, so the value must appear in the
                # decoded remainder — otherwise no split under this
                # piece can resolve, and the whole subtree under the
                # intervening slots is pruned without enumerating it.
                # The remainder is read from the delimiter's start (a
                # superset of what follows it), which keeps the check
                # a necessary condition. Without it a long intervening
                # parameter before a repeat exhausts the budget on
                # ordinary valid URIs, because each wrong split of it
                # re-enumerates the repeat's subtree.
                assert dec_len_at is not None
                value = decoded_span[dec_len_at[pos] : dec_len_at[start]]
                if decoded_span.find(value, dec_len_at[start]) == -1:
                    return None
            piece = span_text[pos:start]
            if re.fullmatch(bodies[index], piece) is not None:
                for end in range(start, min(len(span_text), start + max_len) + 1):
                    if allowed_ends is not None and end not in allowed_ends:
                        continue
                    check_budget()
                    if delimiter.fullmatch(span_text, start, end) is None:
                        continue
                    rest = search(index + 1, end, new_targets, child_consumed)
                    if rest is not None:
                        return [(piece, pos, start), *rest]
            return None

        if pair_ends is not None and pair_index is not None and index == pair_index - 1:
            # The pair's starts were scanned up front; only splits
            # that put the pair at one of them can resolve, so the
            # intervening slot is tried at those splits alone instead
            # of at every delimiter position in the span.
            for start, end in pair_ends:
                check_budget()
                result = attempt_split(start, {end})
                if result is not None:
                    return result
            return None
        start = pos
        while start <= len(span_text):
            check_budget()
            found = delimiter.search(span_text, start)
            if found is None:
                break
            start = found.start()
            result = attempt_split(start, None)
            if result is not None:
                return result
            start += 1
        return None

    try:
        result = search(0, 0, {}, 0)
        if result is not None:
            return result
    except _SearchBudgetExceeded:
        pass
    if pair_ends is not None:
        # The pair holds a multi-piece value the single-piece scan
        # cannot see, so retry without the constraint and a fresh
        # step budget.
        pair_ends = None
        steps[0] = 0
        try:
            return search(0, 0, {}, 0)
        except _SearchBudgetExceeded:
            return None
    return None


@functools.lru_cache(maxsize=4096)
def build_regex(template: str) -> re.Pattern[str] | None:
    """Build regex pattern for URI template, handling RFC 6570 syntax.

    Supports:
    - `{var}` - simple path parameter
    - `{var*}` - wildcard path parameter (captures multiple segments)
    - `{?var1,var2}` / `{&var}` - query parameters (ignored in path matching)
    - `?key={var}` - a query written out literally; `{var}` is that key's value

    Parameter names are normalized to valid Python identifiers through
    `template_param_key` so matched groups are usable and OpenAPI names
    like `{$top}` or `1id` still match instead of disabling the template.

    A parameter named more than once captures each occurrence separately
    (see `_repeat_plan`); `match_uri_template` compares the decoded values,
    so a repeated parameter matches only when every occurrence holds the
    same value. Captures are never backreferences: two occurrences of one
    value can legitimately differ on the wire, since a wildcard occurrence
    emits ``a/b`` where a simple one emits ``a%2Fb`` and percent-hex case
    is insignificant.

    Returns None if the template produces an invalid regex (e.g. two
    parameter names that normalize to the same group).
    """
    return _build_regex(template)


def _build_regex(template: str) -> re.Pattern[str] | None:
    # Remove query parameter syntax for path matching
    template_without_query = re.sub(r"\{[?&][^}]+\}", "", template)

    parts = re.split(r"(\{[^}]+\})", template_without_query)
    pattern = ""
    repeat_plan = _repeat_plan(template)
    repeat_queues = {first: list(names) for first, names in repeat_plan.items()}
    seen_logical: set[str] = set()
    # Past a literal `?` a placeholder is a query value: it may be empty, may
    # hold `/`, and ends at the next `&`.
    in_query = False
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            if in_query:
                group = template_param_key(name.removesuffix("*"))
                body = "[^&]*"
            elif name.endswith("*"):
                name = name[:-1]
                group = template_param_key(name)
                body = ".+"
            else:
                group = template_param_key(name)
                body = "[^/]+"
            logical = part[1:-1].removesuffix("*")
            if logical in seen_logical:
                group = repeat_queues[group].pop(0)
            else:
                seen_logical.add(logical)
            pattern += f"(?P<{group}>{body})"
        else:
            pattern += _literal_pattern(part)
            in_query = in_query or "?" in part
    try:
        return re.compile(f"^{pattern}$")
    except re.error:
        return None


def _match_repeated_path(uri_path: str, uri_template: str) -> dict[str, str] | None:
    """Match a URI path against a template with a repeated parameter.

    The compiled regex for such a template backtracks combinatorially on
    non-matching URIs (each occurrence is an independent greedy capture),
    so the regex is not run at all here: the whole path is resolved by one
    bounded `_resplit_slots` call over every capture, between the
    template's leading and trailing literals. A repeated parameter matches
    only when every occurrence decodes to the same value; occurrences may
    spell the value differently (`{tenant*}` emits ``a/b`` where
    `{tenant}` emits ``a%2Fb``) and percent-hex case is insignificant.
    Acceptance rests on capture bodies plus decoded equality, so this can
    never accept a URI the template forbids. Only the first occurrence of
    each parameter is returned.
    """
    if build_regex(uri_template) is None:
        return None
    captures = _named_captures(uri_template)
    template_without_query = re.sub(r"\{[?&][^}]+\}", "", uri_template)
    parts = re.split(r"(\{[^}]+\})", template_without_query)
    leading = parts[0]
    trailing = parts[-1]
    end = len(uri_path)
    if trailing:
        match = re.search(f"(?:{_literal_pattern(trailing)})$", uri_path)
        if match is None:
            return None
        end = match.start()
    delimiter_literals = _capture_delimiters(uri_template)
    delimiters = [
        (
            re.compile(_literal_pattern(literal)),
            _literal_max_len(literal),
            len(unquote(literal)),
        )
        for literal in delimiter_literals
    ]
    # The prunes in `_resplit_slots` read decoded values from slices of
    # the decoded span, which is only sound when delimiter matches land
    # on unit boundaries (see `_literal_has_clean_boundaries`).
    exact_boundaries = all(
        _literal_has_clean_boundaries(literal) for literal in delimiter_literals
    )
    # The substring prune also needs every delimiter match to land on a
    # decoded (UTF-8 character) boundary, so a repeated value's decoded
    # slice and the decoded remainder it is searched in are both exact.
    substring_prune = exact_boundaries and all(
        _literal_has_decoded_boundaries(literal) for literal in delimiter_literals
    )
    # A leading literal can match in several spellings of different
    # lengths (`%` or `%25`); try each, shortest first like the regex
    # alternation order, and keep the first span that resolves.
    starts = [0]
    if leading:
        leading_pattern = re.compile(_literal_pattern(leading))
        starts = [
            candidate
            for candidate in range(min(len(uri_path), _literal_max_len(leading)) + 1)
            if leading_pattern.fullmatch(uri_path, 0, candidate) is not None
        ]
        if not starts:
            return None
    for start in starts:
        if start > end:
            continue
        pieces = _resplit_slots(
            uri_path[start:end],
            captures,
            delimiters,
            exact_boundaries=exact_boundaries,
            substring_prune=substring_prune,
        )
        if pieces is None:
            continue
        params = {
            group: unquote(piece)
            for (group, _body, _logical), (piece, _piece_start, _piece_end) in zip(
                captures, pieces, strict=True
            )
        }
        for repeat_names in _repeated_group_names(uri_template).values():
            for repeat_name in repeat_names:
                params.pop(repeat_name, None)
        return params
    return None


def match_uri_template(
    uri: str,
    uri_template: str,
    *,
    list_params: Collection[str] = (),
    regex: re.Pattern[str] | None = None,
) -> dict[str, Any] | None:
    """Match URI against template and extract both path and query parameters.

    Supports RFC 6570 URI templates:
    - Path params: `{var}`, `{var*}`
    - Query params: `{?var1,var2}`, `{&var}`, `{?list*}` (repeated keys);
      several query expressions in one template are all read

    `list_params` names non-exploded query params that hold lists. Per RFC 6570
    section 3.2.8 their value is comma-joined, with literal commas separating
    items and `%2C` inside an item, so they are split before decoding.

    `regex` is the template's compiled path pattern when the caller already
    holds it; otherwise it is built from `uri_template` through a bounded cache.
    For a template with a repeated parameter the pattern is not run (see
    `_match_repeated_path`); it is only rebuilt to reject invalid templates.
    """
    # A fragment is not part of the resource identity, so drop it before
    # splitting off the query. A template that spells out a literal `#` is
    # matched against the full URI.
    if "#" not in uri_template:
        uri = uri.partition("#")[0]
    # Likewise a template that spells out a literal `?` (`search://items?q={q}`)
    # carries its query in the path pattern, so there is nothing to split off.
    if _has_literal_query(uri_template):
        uri_path, query_string = uri, ""
    else:
        uri_path, _, query_string = uri.partition("?")

    # Match path parameters. A template with a repeated parameter is
    # matched by a single bounded search (see `_match_repeated_path`):
    # the compiled regex for such a template backtracks combinatorially
    # on non-matching URIs, so it is not run at all. All other templates
    # use the regex.
    repeat_groups = _repeated_group_names(uri_template)
    params: dict[str, Any]
    if repeat_groups:
        matched = _match_repeated_path(uri_path, uri_template)
        if matched is None:
            return None
        params = dict(matched)
    else:
        if regex is None:
            regex = build_regex(uri_template)
        if regex is None:
            return None
        match = regex.match(uri_path)
        if not match:
            return None
        params = {k: unquote(v) for k, v in match.groupdict().items()}

    # Extract query parameters if present in URI and template
    if query_string:
        query_param_names = extract_query_params(uri_template)
        # keep_blank_values=True preserves empty values (e.g. ?format=)
        # so callers can distinguish "explicitly empty" from "missing".
        parsed_query = parse_qs(query_string, keep_blank_values=True)

        exploded = extract_exploded_query_params(uri_template)
        raw_query: dict[str, str] = {}
        for pair in query_string.split("&"):
            raw_name, _, raw_value = pair.partition("=")
            raw_query.setdefault(unquote_plus(raw_name), raw_value)

        for name in query_param_names:
            if name in parsed_query:
                # Normalize hyphens to underscores to match Python param names.
                # Don't overwrite path params that were already extracted.
                key = name.replace("-", "_")
                if key not in params:
                    # An exploded `{?name*}` param keeps every repetition, a
                    # list `{?name}` param splits its first value on commas, and
                    # a plain `{?name}` param is a scalar, so take the first.
                    if name in exploded:
                        params[key] = parsed_query[name]
                    elif name in list_params:
                        raw = raw_query.get(name, "")
                        params[key] = (
                            [unquote_plus(item) for item in raw.split(",")]
                            if raw
                            else []
                        )
                    else:
                        params[key] = parsed_query[name][0]

    return params


def expand_uri_template(uri_template: str, params: dict[str, Any]) -> str:
    """Expand a URI template with parameters — inverse of `match_uri_template`.

    Supports the same RFC 6570 subset:
    - Path params: `{var}`, `{var*}`
    - Query params: `{?var1,var2}`, `{&var}` (multiple blocks allowed)
    """
    # Literal runs carry their own encoding (RFC 6570 3.1); placeholders are
    # left alone here and encoded with their substituted values below.
    result = "".join(
        part if part.startswith("{") and part.endswith("}") else encode_literal(part)
        for part in re.split(r"(\{[^}]+\})", uri_template)
    )

    # Replace {name} and {name*} path placeholders, percent-encoding the
    # substituted values so the result round-trips through match_uri_template
    # (which unquotes captured groups). Simple {name} placeholders match a
    # single segment ([^/]+), so reserved characters including "/" are encoded;
    # wildcard {name*} placeholders may span segments, so "/" is preserved.
    #
    # Params are keyed the way match_uri_template returns them — sanitized
    # through template_param_key — but templates may spell the variable with
    # hyphens or characters an identifier cannot carry (e.g. {$top}), so each
    # placeholder resolves against the exact name, the hyphenated form, and
    # the sanitized form, in that order. A placeholder with no matching param
    # stays literal.
    def substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        wildcard = token.endswith("*")
        name = token[:-1] if wildcard else token
        for candidate in (name, name.replace("-", "_"), template_param_key(name)):
            if candidate in params:
                value_str = str(params[candidate])
                return quote(value_str, safe="/" if wildcard else "")
        return match.group(0)

    result = re.sub(r"\{([^?&}][^}]*)\}", substitute, result)

    # Expand {?param1,param2,...} and {&param,...} query blocks. The first
    # block that emits parameters opens the query string with "?", and later
    # blocks continue it with "&": emitting "?" for every block would bury
    # the later parameters inside the first parameter's value.
    first_block = re.search(r"\{[?&]", result)
    prefix = result[: first_block.start()] if first_block else result
    query_started = "?" in prefix

    def _expand_query_block(match: re.Match[str]) -> str:
        nonlocal query_started
        names = [n.strip() for n in match.group(1).split(",")]
        parts = []
        for name in names:
            # The template decides the serialization, not the runtime value:
            # `{?tags*}` emits a repeated key, `{?tags}` stays a single value.
            # Keying off the value type instead would expand a list under a
            # plain `{?tags}`, which match_uri_template then reads back as just
            # its first element.
            exploded = name.endswith("*")
            name = name.removesuffix("*")
            underscored = name.replace("-", "_")
            sanitized = template_param_key(name)
            if name in params:
                value = params[name]
            elif underscored in params:
                value = params[underscored]
            elif sanitized in params:
                value = params[sanitized]
            else:
                continue
            if exploded and isinstance(value, (list, tuple)):
                parts.extend(
                    f"{quote(name, safe='')}={quote(str(v), safe='')}" for v in value
                )
            elif isinstance(value, (list, tuple)):
                if value:
                    joined = ",".join(quote(str(v), safe="") for v in value)
                    parts.append(f"{quote(name, safe='')}={joined}")
            else:
                parts.append(f"{quote(name, safe='')}={quote(str(value), safe='')}")
        if parts:
            separator = "&" if query_started else "?"
            query_started = True
            return separator + "&".join(parts)
        return ""

    result = re.sub(r"\{[?&]([^}]+)\}", _expand_query_block, result)

    return result


def forward_uri(uri_template: str, params: dict[str, Any], uri: str) -> str:
    """Build the URI a forwarding layer (mount or proxy) sends to the server behind it.

    The path is expanded from `uri_template` with `params`, but the query string
    is carried over from the incoming `uri` byte for byte. Re-expanding it would
    decode and re-encode values, and the forwarding layer does not know which
    `{?name}` params hold comma-joined lists, so `?tags=a%2Cb,c` would arrive as
    one item instead of two.
    """
    path_template = re.sub(r"\{[?&][^}]+\}", "", uri_template)
    forwarded = expand_uri_template(path_template, params)
    if _has_literal_query(uri_template):
        # The query is literal template text, so the expansion already has it.
        return forwarded
    _, _, query = uri.partition("?")
    return f"{forwarded}?{query}" if query else forwarded


class ResourceTemplate(FastMCPComponent):
    """A template for dynamically creating resources."""

    KEY_PREFIX: ClassVar[str] = "template"

    # Non-exploded `{?name}` query params whose function parameter is a list.
    _list_query_params: frozenset[str] = PrivateAttr(default_factory=frozenset)
    _pattern: tuple[str, re.Pattern[str] | None] | None = PrivateAttr(default=None)

    uri_template: str = Field(
        description="URI template with parameters (e.g. weather://{city}/current)"
    )
    mime_type: str = Field(
        default="text/plain", description="MIME type of the resource content"
    )
    parameters: dict[str, Any] = Field(
        description="JSON schema for function parameters"
    )
    annotations: Annotations | None = Field(
        default=None, description="Optional annotations about the resource's behavior"
    )
    auth: SkipJsonSchema[AuthCheck | list[AuthCheck] | None] = Field(
        default=None,
        description="Authorization checks for this resource template",
        exclude=True,
    )
    security: SkipJsonSchema[ResourceSecurity | None | InheritSecurity] = Field(
        default=INHERIT_SECURITY,
        description=(
            "Path-safety policy for extracted parameters. INHERIT_SECURITY "
            "(default) inherits the server-wide default; None disables "
            "screening; a ResourceSecurity instance applies that explicit "
            "policy."
        ),
        exclude=True,
    )

    def resolve_security(
        self, server_default: ResourceSecurity | None
    ) -> ResourceSecurity | None:
        """Resolve the effective security policy for this template.

        A per-component ``security`` overrides the server default.
        ``INHERIT_SECURITY`` (the field default) inherits ``server_default``;
        an explicit ``None`` disables screening for this template.
        """
        if isinstance(self.security, InheritSecurity):
            return server_default
        return self.security

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri_template={self.uri_template!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY,
    ) -> FunctionResourceTemplate:
        return FunctionResourceTemplate.from_function(
            fn=fn,
            uri_template=uri_template,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
            auth=auth,
            security=security,
        )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    def matches(self, uri: str) -> dict[str, Any] | None:
        """Check if URI matches template and extract parameters."""
        regex = self._compiled_pattern()
        if regex is None:
            return None
        return match_uri_template(
            uri,
            self.uri_template,
            list_params=self._list_query_params if "?" in uri else (),
            regex=regex,
        )

    def _compiled_pattern(self) -> re.Pattern[str] | None:
        """This template's path pattern, built once per `uri_template`.

        Reads match every template in turn, and pydantic's private-attribute
        access costs ~0.3us, so the pattern lives in the private dict directly.
        """
        private = self.__pydantic_private__
        assert private is not None
        cached = private.get("_pattern")
        if cached is None or cached[0] != self.uri_template:
            cached = (self.uri_template, build_regex(self.uri_template))
            private["_pattern"] = cached
        return cached[1]

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content."""
        raise NotImplementedError(
            "Subclasses must implement read() or override create_resource()"
        )

    def convert_result(self, raw_value: Any) -> ResourceResult:
        """Convert a raw result to ResourceResult.

        This is used in two contexts:
        1. In _read() to convert user function return values to ResourceResult
        2. In tasks_result_handler() to convert Docket task results to ResourceResult

        Handles ResourceResult passthrough and converts raw values using
        ResourceResult's normalization. The template's own ``mime_type`` is
        forwarded so that reads match the MIME type the template advertises
        in ``resources/templates/list``.
        """
        return convert_raw_to_resource_result(
            raw_value, mime_type=self.mime_type, meta=self.meta
        )

    async def _read(self, uri: str, params: dict[str, Any]) -> ResourceResult:
        """Server entry point for template reads.

        The server calls this instead of create_resource()/read() directly so
        subclasses can customize dispatch (e.g. FastMCPProviderResourceTemplate
        delegates to child-server middleware).
        """
        resource = await self.create_resource(uri, params)
        result = await resource.read()
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters.

        The base implementation does not support background tasks.
        Use FunctionResourceTemplate for task support.
        """
        raise NotImplementedError(
            "Subclasses must implement create_resource(). "
            "Use FunctionResourceTemplate for task support."
        )

    def to_mcp_template(
        self,
        **overrides: Any,
    ) -> SDKResourceTemplate:
        """Convert the resource template to an SDKResourceTemplate."""

        return SDKResourceTemplate(
            name=overrides.get("name", self.name),
            uri_template=overrides.get("uriTemplate", self.uri_template),
            description=overrides.get("description", self.description),
            mime_type=overrides.get("mimeType", self.mime_type),
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
            ),
        )

    @classmethod
    def from_mcp_template(cls, mcp_template: SDKResourceTemplate) -> ResourceTemplate:
        """Creates a FastMCP ResourceTemplate from a raw MCP ResourceTemplate object."""
        # Note: This creates a simple ResourceTemplate instance. For function-based templates,
        # the original function is lost, which is expected for remote templates.
        return cls(
            uri_template=mcp_template.uri_template,
            name=mcp_template.name,
            description=mcp_template.description,
            mime_type=mcp_template.mime_type or "text/plain",
            parameters={},  # Remote templates don't have local parameters
        )

    @property
    def key(self) -> str:
        """The globally unique lookup key for this template."""
        base_key = self.make_key(self.uri_template)
        return f"{base_key}@{self.version or ''}"

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "resource_template",
            "fastmcp.provider.type": "LocalProvider",
        }


class FunctionResourceTemplate(ResourceTemplate):
    """A template for dynamically creating resources."""

    fn: SkipJsonSchema[Callable[..., Any]]

    # Resolved parameter hints, so string annotations from
    # `from __future__ import annotations` coerce like real ones in read().
    _param_hints: dict[str, Any] = PrivateAttr(default_factory=dict)

    async def _read(self, uri: str, params: dict[str, Any]) -> ResourceResult:
        """Optimized server entry point that skips ephemeral resource creation.

        For FunctionResourceTemplate, we can call read() directly instead of
        creating a temporary resource, which is more efficient.
        """
        # Call read() directly, skip resource creation
        result = await self.read(arguments=params)
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters."""

        async def resource_read_fn() -> str | bytes | ResourceResult:
            # Call function and check if result is a coroutine
            result = await self.read(arguments=params)
            return result

        return Resource.from_function(
            fn=resource_read_fn,
            uri=uri,
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
            tags=self.tags,
            annotations=self.annotations,
            meta=self.meta,
            title=self.title,
            icons=self.icons,
            auth=self.auth,
        )

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content."""
        # Type coercion for query parameters (which arrive as strings)
        kwargs = arguments.copy()
        sig = inspect.signature(self.fn)
        for param_name, param_value in list(kwargs.items()):
            if param_name in sig.parameters and isinstance(param_value, str):
                param = sig.parameters[param_name]
                annotation = self._param_hints.get(param_name, param.annotation)

                if annotation is inspect.Parameter.empty or annotation is str:
                    continue

                # An unconvertible parameter is a client error, not a server fault
                if annotation is int or annotation is float:
                    try:
                        kwargs[param_name] = annotation(param_value)
                    except ValueError:
                        raise ValidationError(
                            f"Invalid {annotation.__name__} value for {param_name}: {param_value!r}",
                            log_level=logging.WARNING,
                        ) from None
                elif annotation is bool:
                    lower = param_value.lower()
                    if lower in ("true", "1", "yes"):
                        kwargs[param_name] = True
                    elif lower in ("false", "0", "no"):
                        kwargs[param_name] = False
                    else:
                        raise ValidationError(
                            f"Invalid boolean value for {param_name}: {param_value!r}",
                            log_level=logging.WARNING,
                        )

        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally
        if is_coroutine_function(self.fn):
            result = await self.fn(**kwargs)
        else:
            # Run sync functions in threadpool to avoid blocking the event loop
            result = await call_sync_fn_in_threadpool(self.fn, **kwargs)
            # Handle sync wrappers that return awaitables (e.g., partial(async_fn))
            if inspect.isawaitable(result):
                result = await result

        return result

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY,
    ) -> FunctionResourceTemplate:
        """Create a template from a function."""

        func_name = name or getattr(fn, "__name__", None) or fn.__class__.__name__
        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Reject functions with positional-only parameters or *args
        # (**kwargs is allowed because the URI will define the parameter names)
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                raise ValueError(
                    "Functions with positional-only parameters are not "
                    "supported as resource templates because URI template "
                    "parameters are passed by name. Replace them with standard "
                    "parameters that can be passed as keywords."
                )
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError(
                    "Functions with *args are not supported as resource templates"
                )

        # Extract path and query parameters from URI template.
        # Allow hyphens in names and normalize to underscores so they
        # match Python function parameter names.
        raw_path_params = set(re.findall(r"{([\w-]+)(?:\*)?}", uri_template))
        raw_query_params = extract_query_params(uri_template)

        # Refuse RFC 6570 level-3 operators other than the query forms
        # ({?...} and {&...}): the path matcher cannot capture them, so a
        # template using one would register but never read correctly.
        unsupported_operators = re.findall(r"\{([+#./;])", uri_template)
        if unsupported_operators:
            ops = ", ".join(sorted(set(unsupported_operators)))
            raise ValueError(
                f"RFC 6570 level-3 operators {{{ops}...}} are not supported. "
                "Use {?param} or {&param} for query parameters or {param} "
                "for path parameters."
            )

        # Validate set expansion: {a,b} (comma without an operator).
        # These are treated as literals in path matching and can't be captured,
        # so refuse them to avoid confusion.
        set_expansions = re.findall(r"\{([a-zA-Z_][\w-]*,[^}]+)\}", uri_template)
        if set_expansions:
            examples = ", ".join(f"{{{s}}}" for s in set_expansions[:2])
            raise ValueError(
                f"Set expansion syntax like {examples} is not supported. "
                "Use {{?param1,param2}} for multiple query parameters."
            )

        # Detect collisions: two raw param names that normalize to the
        # same Python identifier (e.g. {user-id} and {user_id}).
        all_raw = raw_path_params | raw_query_params
        seen: dict[str, str] = {}
        for raw_name in sorted(all_raw):
            normalized = raw_name.replace("-", "_")
            if normalized in seen:
                raise ValueError(
                    f"URI template parameters '{seen[normalized]}' and "
                    f"'{raw_name}' both normalize to '{normalized}'. "
                    f"Use one or the other, not both."
                )
            seen[normalized] = raw_name

        path_params = {p.replace("-", "_") for p in raw_path_params}
        query_params = {p.replace("-", "_") for p in raw_query_params}
        all_uri_params = path_params | query_params

        if not all_uri_params:
            raise ValueError("URI template must contain at least one parameter")

        # Use wrapper to get user-facing parameters (excludes injected params)
        from fastmcp.server.dependencies import (
            transform_context_annotations,
            without_injected_parameters,
        )

        wrapper_fn = without_injected_parameters(fn)
        user_sig = inspect.signature(wrapper_fn)
        func_params = set(user_sig.parameters.keys())

        # Get required and optional function parameters
        required_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }
        optional_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is not inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }

        # Validate RFC 6570 query parameters
        # Query params must be optional (have defaults)
        list_query_params: set[str] = set()
        # Resolve the hints rather than reading the raw signature: under
        # `from __future__ import annotations` every annotation is a string,
        # and `Annotated[...]` wrappers hide the underlying type.
        from fastmcp.tools.function_tool import _resolve_param_hints

        # A callable instance is invoked through its `__call__`, so resolve the
        # hints there; the instance itself carries no parameter annotations.
        hint_target = fn
        if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
            hint_target = fn.__call__
        if isinstance(hint_target, staticmethod):
            hint_target = hint_target.__func__

        try:
            hints = _resolve_param_hints(hint_target)
        except NameError:
            # Pydantic resolves annotations against namespaces this doesn't
            # see, so a name we can't resolve may still be valid. Fall back
            # to the raw form rather than failing a working registration.
            hints = {}

        if query_params:
            invalid_query_params = query_params - optional_params
            if invalid_query_params:
                raise ValueError(
                    f"Query parameters {invalid_query_params} must be optional function parameters with default values"
                )

            # A list-typed query parameter reads `{?tags*}` as repeated keys and
            # `{?tags}` as one comma-joined value (RFC 6570 section 3.2.8).
            exploded = {
                p.replace("-", "_") for p in extract_exploded_query_params(uri_template)
            }
            for param_name in sorted(exploded):
                if param_name not in user_sig.parameters:
                    continue
                annotation = hints.get(
                    param_name, user_sig.parameters[param_name].annotation
                )
                if not _accepts_exploded_query(annotation):
                    raise ValueError(
                        f"Query parameter '{param_name}' uses the RFC 6570 "
                        "explode modifier, so its function parameter must accept "
                        f"a list: use '{{?{param_name}}}' for a scalar value"
                    )
            for param_name in sorted(query_params - exploded):
                if param_name not in user_sig.parameters:
                    continue
                annotation = hints.get(
                    param_name, user_sig.parameters[param_name].annotation
                )
                if _is_list_only(annotation):
                    list_query_params.add(
                        next(
                            name
                            for name in extract_query_params(uri_template)
                            if name.replace("-", "_") == param_name
                        )
                    )

        # Check if required parameters are a subset of the path parameters
        if not required_params.issubset(path_params):
            raise ValueError(
                f"Required function arguments {required_params} must be a subset of the URI path parameters {path_params}"
            )

        # Check if all URI parameters are valid function parameters (skip if **kwargs present)
        if not any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        ):
            if not all_uri_params.issubset(func_params):
                raise ValueError(
                    f"URI parameters {all_uri_params} must be a subset of the function arguments: {func_params}"
                )

        description = description if description is not None else inspect.getdoc(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        wrapper_fn = without_injected_parameters(fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)
        parameters = type_adapter.json_schema()
        parameters = compress_schema(parameters, prune_titles=True)

        # Use validate_call on wrapper for runtime type coercion. URI parameters
        # carry Python parameter names, so accept them for aliased fields too.
        fn = validate_call(config=ConfigDict(validate_by_name=True))(wrapper_fn)

        # Apply ui:// MIME default, then fall back to text/plain
        resolved_mime = resolve_ui_mime_type(uri_template, mime_type)

        template = cls(
            uri_template=uri_template,
            name=func_name,
            version=str(version) if version is not None else None,
            title=title,
            description=description,
            icons=icons,
            mime_type=resolved_mime or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
            annotations=annotations,
            meta=meta,
            auth=auth,
            security=security,
        )
        template._list_query_params = frozenset(list_query_params)
        template._param_hints = hints
        return template
