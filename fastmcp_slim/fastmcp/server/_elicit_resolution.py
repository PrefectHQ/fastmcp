"""Declarative elicitation: fill a parameter by asking the client for it.

PROVISIONAL INTERNAL MODULE — do not import from here.

The public name is `fastmcp.elicitation.Elicit`. This module holds the engine
behind it, and the engine is scheduled to move into the `uncalled-for`
dependency package once that package can *inject* a value from `Annotated`
metadata (today its annotation path runs a dependency for its side effects and
discards the result, so a marker in an annotation cannot fill a parameter).

When that lands, `Elicit` becomes an ordinary `uncalled_for.Dependency`
subclass, the scanning and ordering below is deleted in favour of the engine's
own DAG walk, and only the MCP-specific parts — rendering a question, recording
an answer, digesting, and choosing a transport for the protocol era — stay in
FastMCP. Nothing here is public API and none of it carries a deprecation
guarantee.

A parameter annotated `Annotated[T, Elicit("...")]` is filled by asking the
client instead of by the model, and is hidden from the tool's input schema. How
the ask reaches the user depends on the negotiated protocol:

- 2026-07-28 and later: there is no server-initiated back-channel (SEP-2577), so
  every unanswered question is batched into one `InputRequiredResult` and the
  body does not run. The client answers and re-issues the call; the parameters
  resolve from those answers and the body runs. Answers from earlier rounds ride
  `request_state`, which the framework seals before it reaches the wire.
- 2025-11-25 and earlier: the back-channel exists, so each question is asked
  in-process with `ctx.elicit()` while the call is still open.

The same annotated function works on both. That bridge is the point of the
declarative form: the framework can only choose a transport when the ask is not
already hard-coded into the body's control flow.

Questions are pinned to a digest of exactly what the client was shown, so a
redeploy that rewords a question — or a retry that changes an argument feeding
one — re-asks it rather than silently reusing an answer to a different question.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    Literal,
    TypeVar,
    get_args,
    get_origin,
)

import mcp_types
from pydantic import BaseModel, ValidationError
from uncalled_for import FailedDependency, get_dependency_parameters
from uncalled_for.resolution import resolved_dependencies

from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    ElicitConfig,
    handle_elicit_accept,
    parse_elicit_response_type,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.context import Context

__all__ = [
    "Elicit",
    "ElicitParam",
    "NeedsInput",
    "find_elicit_parameters",
    "resolve_elicitations",
]

logger = get_logger(__name__)

T = TypeVar("T")

#: Bumped when the shape of the `request_state` payload changes. A payload from
#: another version is treated as "no progress yet" — during a rolling upgrade an
#: in-flight call re-asks rather than misreading an older layout.
_STATE_VERSION = 1


class Elicit(Generic[T]):
    """A request for the user to supply a value.

    Used in two positions, meaning the same thing in both.

    As parameter metadata it says the parameter is filled by asking rather than
    by the model, and the annotated type is the schema for the answer — scalars,
    `Literal`s, enums, dataclasses, and models all behave as they do with
    `ctx.elicit()`:

    ```python
    destination: Annotated[str, Elicit("Where would you like to fly?")]
    ```

    Returned from a resolver it is the question that resolver decided to ask.
    A resolver returns `T | Elicit[T]`, so returning a value instead skips the
    question entirely:

    ```python
    def which_airport(destination: str, profile: Profile = Depends(get_profile)) -> Airport | Elicit[Airport]:
        if profile.home_airport:
            return profile.home_airport
        return Elicit(f"Which airport in {destination}?", response_type=Airport)


    airport: Annotated[Airport, Elicit(which_airport)]
    ```

    A parameter with a default is optional: declining or cancelling leaves the
    default in place and the call proceeds. A parameter without one is required,
    and declining it fails the call.

    Args:
        message: The text to show the user, or a resolver that decides. A
            resolver's parameters are filled by name from the call's own
            arguments and from other elicited parameters, which is also what
            orders the asks; it may declare its own `Depends(...)` parameters,
            and it may be sync or async.
        response_type: The type to ask for, exactly as `ctx.elicit()` takes it.
            State it when constructing an `Elicit` inside a resolver, where the
            parameter's annotation is not in view; omitted, the parameter's own
            annotation is used.
        title: Optional label for the wrapped `value` field, for the scalar and
            shorthand forms. Same scope rules as `ctx.elicit()`.
        description: Optional description for the wrapped `value` field.
    """

    def __init__(
        self,
        message: str | Callable[..., Any],
        *,
        response_type: Any = None,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        self.message = message
        self.response_type = response_type
        self.title = title
        self.description = description


class NeedsInput(Exception):
    """Internal: unanswered questions remain, so the body must not run.

    Raised out of parameter resolution and caught by the component that owns the
    call, which turns it into the `InputRequiredResult` that is this leg's
    result. Never reaches user code.
    """

    def __init__(
        self,
        input_requests: dict[str, Any],
        request_state: str,
    ) -> None:
        super().__init__("elicitation input required")
        self.input_requests = input_requests
        self.request_state = request_state


@dataclass(frozen=True)
class ElicitParam:
    """One parameter to be filled by asking, analyzed once at registration."""

    name: str
    marker: Elicit
    response_type: Any
    has_default: bool
    default: Any
    #: Names this parameter's question is built from — tool arguments, other
    #: elicited parameters, or both. Empty for a plain string question.
    depends_on: tuple[str, ...]

    async def resolve(self, values: Mapping[str, Any]) -> Any:
        """Decide what this parameter needs: a value, or a question to ask.

        Returns an `Elicit` when the user has to be asked, and anything else as
        the resolved value. A literal-question marker always returns itself; a
        resolver decides, and may skip the ask by returning a value.

        A resolver may also declare its own `Depends(...)` parameters, which
        resolve the ordinary way.
        """
        if isinstance(self.marker.message, str):
            return self.marker
        bound = {name: values[name] for name in self.depends_on}
        async with resolved_dependencies(self.marker.message, bound) as injected:
            for param_name, value in injected.items():
                # The DI engine reports a dependency it could not build as a
                # sentinel rather than raising, which would otherwise reach the
                # resolver as a nonsense value. The common cause is a dependency
                # that wants one of the call's arguments by name, which the
                # engine cannot supply.
                if isinstance(value, FailedDependency):
                    raise ToolError(
                        f"The resolver for {self.name!r} depends on {param_name!r}, "
                        "which could not be resolved"
                    ) from value.error
            outcome = self.marker.message(**bound, **injected)
            return await outcome if inspect.isawaitable(outcome) else outcome

    def type_for(self, request: Elicit[Any]) -> Any:
        """The type one question asks for.

        Taken from the `Elicit` when it states one — a resolver naming
        `response_type` where the parameter's annotation is out of view — and from
        the parameter's own annotation otherwise.
        """
        if request.response_type is not None:
            return request.response_type
        return self.response_type

    def config(self, request: Elicit[Any]) -> ElicitConfig:
        """Schema and response handling for one question's answer."""
        return parse_elicit_response_type(
            self.type_for(request),
            response_title=request.title,
            response_description=request.description,
        )


def _unwrap_optional(annotation: Any) -> Any:
    """Strip a `None` arm wrapped around an `Annotated`.

    Python 3.10's `get_type_hints` still applies implicit-Optional, so a
    parameter defaulting to `None` comes back as `Optional[Annotated[...]]`
    rather than the `Annotated[...]` that 3.11+ reports. Both spellings mean the
    same optional parameter, so both resolve to the inner annotation.
    """
    if get_origin(annotation) not in (typing.Union, UnionType):
        return annotation
    arms = [arm for arm in get_args(annotation) if arm is not type(None)]
    if len(arms) == 1 and get_origin(arms[0]) is Annotated:
        return arms[0]
    return annotation


def _elicit_marker(annotation: Any) -> Elicit | None:
    """The `Elicit` marker in an `Annotated[...]`, if there is one."""
    annotation = _unwrap_optional(annotation)
    if get_origin(annotation) is not Annotated:
        return None
    return next((m for m in get_args(annotation)[1:] if isinstance(m, Elicit)), None)


def _contains_elicit(annotation: Any) -> bool:
    """True when an `Elicit` marker is nested somewhere inside `annotation`."""
    if get_origin(annotation) is Annotated:
        return any(isinstance(m, Elicit) for m in get_args(annotation)[1:])
    return any(_contains_elicit(arg) for arg in get_args(annotation))


def _response_type(annotation: Any) -> Any:
    """The type to elicit, given the full `Annotated[...]` annotation.

    A `None` arm carries the optional-parameter case (`Annotated[str | None,
    Elicit(...)] = None`) and is dropped: the user is asked for a `str`, and the
    `None` is what a decline leaves behind. Any other metadata in the
    `Annotated` is preserved so `Field(...)` constraints still shape the schema.
    """
    type_arg = get_args(_unwrap_optional(annotation))[0]
    if get_origin(type_arg) in (typing.Union, UnionType):
        arms = [a for a in get_args(type_arg) if a is not type(None)]
        if len(arms) == 1:
            return arms[0]
        if arms:
            return typing.Union[tuple(arms)]  # noqa: UP007
    return type_arg


def find_elicit_parameters(fn: Callable[..., Any]) -> dict[str, ElicitParam]:
    """Find and validate every `Annotated[T, Elicit(...)]` parameter of `fn`.

    The returned mapping is in resolution order: a parameter whose question is
    built from another elicited parameter comes after it.

    Raises:
        TypeError: If a marker is buried in a union rather than applied to the
            parameter directly, if a question callable asks for something that is
            neither a tool argument nor another elicited parameter, or if the
            questions form a cycle.
    """
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except (NameError, TypeError) as e:
        # Annotations that cannot be resolved (a `from __future__ import
        # annotations` module naming something out of scope) carry no marker we
        # can see. Matching the DI engine's own tolerance, treat the function as
        # having none rather than failing every tool with an odd annotation.
        logger.debug("Could not read annotations of %r: %s", _fn_name(fn), e)
        return {}

    signature = inspect.signature(fn)
    found: dict[str, ElicitParam] = {}
    for name, parameter in signature.parameters.items():
        annotation = hints.get(name)
        marker = _elicit_marker(annotation)
        if marker is None:
            # Flag rather than silently ignore a marker that cannot take effect,
            # e.g. `Annotated[str, Elicit(...)] | None`.
            if annotation is not None and _contains_elicit(annotation):
                raise TypeError(
                    f"Parameter {name!r} of {_fn_name(fn)!r} wraps Elicit(...) in a "
                    "union; annotate the parameter directly as "
                    "Annotated[T, Elicit(...)]"
                )
            continue
        has_default = parameter.default is not inspect.Parameter.empty
        found[name] = ElicitParam(
            name=name,
            marker=marker,
            response_type=_response_type(annotation),
            has_default=has_default,
            default=parameter.default if has_default else None,
            depends_on=_message_parameters(marker, name, fn),
        )

    if not found:
        return {}

    available = set(signature.parameters)
    for spec in found.values():
        _check_declared_type(spec, _fn_name(fn))
        for dependency in spec.depends_on:
            if dependency not in available:
                raise TypeError(
                    f"The question for parameter {spec.name!r} of {_fn_name(fn)!r} "
                    f"asks for {dependency!r}, which is not a parameter of the "
                    "function; a question can only be built from the call's own "
                    "arguments or from other elicited parameters"
                )
    return _in_resolution_order(found, _fn_name(fn))


def _declared_response_type(fn: Callable[..., Any]) -> Any | None:
    """The `T` a resolver declares in an `Elicit[T]` return arm, if it declares one.

    A resolver annotated `-> Airport | Elicit[Airport]` states the type it asks
    for at its own definition, which is the type the parameter must accept.
    Returns `None` when the resolver says nothing usable — an unannotated
    resolver, or a bare `Elicit` with no parameter.
    """
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except (NameError, TypeError):
        return None
    returns = hints.get("return")
    if returns is None:
        return None
    arms = (
        get_args(returns)
        if get_origin(returns) in (typing.Union, UnionType)
        else (returns,)
    )
    for arm in arms:
        if get_origin(arm) is Elicit:
            args = get_args(arm)
            return args[0] if args else None
    return None


def _check_declared_type(spec: ElicitParam, fn_name: str) -> None:
    """Reject a resolver whose declared `Elicit[T]` contradicts its parameter.

    Both are visible at registration, so a disagreement is caught at import
    rather than surfacing as a validation failure on the answer.

    Raises:
        TypeError: If the two types disagree.
    """
    if isinstance(spec.marker.message, str):
        return
    declared = _declared_response_type(spec.marker.message)
    if declared is None or declared == spec.response_type:
        return
    raise TypeError(
        f"The resolver for parameter {spec.name!r} of {fn_name!r} declares it "
        f"elicits {declared!r}, but the parameter is annotated "
        f"{spec.response_type!r}. Make the two agree."
    )


def _message_parameters(
    marker: Elicit, name: str, fn: Callable[..., Any]
) -> tuple[str, ...]:
    """Names a resolver needs filled by name; empty for a literal question.

    A resolver's own `Depends(...)` parameters are left out: those are resolved
    by the DI engine when the resolver runs, not matched against the call's
    arguments.
    """
    if isinstance(marker.message, str):
        return ()
    try:
        question_signature = inspect.signature(marker.message)
    except (TypeError, ValueError) as e:
        raise TypeError(
            f"The question for parameter {name!r} of {_fn_name(fn)!r} is a callable "
            "whose signature could not be read"
        ) from e
    injected = get_dependency_parameters(marker.message)
    return tuple(p for p in question_signature.parameters if p not in injected)


def _in_resolution_order(
    specs: dict[str, ElicitParam], fn_name: str
) -> dict[str, ElicitParam]:
    """Order the parameters so each comes after the ones its question needs."""
    ordered: dict[str, ElicitParam] = {}
    visiting: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in ordered:
            return
        if name in visiting:
            cycle = " -> ".join((*trail, name))
            raise TypeError(
                f"The elicited parameters of {fn_name!r} form a cycle: {cycle}"
            )
        visiting.add(name)
        for dependency in specs[name].depends_on:
            # Only other *elicited* parameters constrain ordering; plain tool
            # arguments are already available before resolution starts.
            if dependency in specs:
                visit(dependency, (*trail, name))
        visiting.discard(name)
        ordered[name] = specs[name]

    for name in specs:
        visit(name, ())
    return ordered


def _fn_name(fn: Callable[..., Any]) -> str:
    return getattr(fn, "__name__", None) or type(fn).__name__


class _Answer(BaseModel):
    """One recorded answer, as it travels in `request_state`."""

    action: Literal["accept", "decline", "cancel"]
    #: The client's own content, stored exactly as it arrived so restoring it
    #: revalidates the same bytes rather than a re-serialized model.
    data: Any = None
    #: Digest of the question this answered.
    q: str


class _State(BaseModel):
    """Everything carried from one round to the next."""

    v: int
    answers: dict[str, _Answer] = {}
    #: Digest of each question asked last round, so an answer is only accepted
    #: for the exact wording it was shown against.
    asked: dict[str, str] = {}


def _decode_state(request_state: str | None) -> _State:
    """Read the state a previous round carried forward.

    The string arrives already unsealed and verified by the framework, so
    anything unreadable here is drift inside the operator's own fleet (a rolling
    upgrade, say) and is treated as no progress rather than an error.
    """
    empty = _State(v=_STATE_VERSION)
    if not request_state:
        return empty
    try:
        state = _State.model_validate(json.loads(request_state))
    except ValueError:
        return empty
    return state if state.v == _STATE_VERSION else empty


def _encode_state(answers: Mapping[str, _Answer], asked: Mapping[str, str]) -> str:
    state = _State(v=_STATE_VERSION, answers=dict(answers), asked=dict(asked))
    return json.dumps(state.model_dump(mode="json"), separators=(",", ":"))


def _digest(request: mcp_types.ElicitRequest) -> str:
    """Pin an answer to exactly what the client was shown."""
    params = request.params
    rendered = json.dumps(
        params.model_dump(mode="json", by_alias=True, exclude_none=True)
        if params
        else None,
        separators=(",", ":"),
        sort_keys=True,
    )
    packed = hashlib.sha256(rendered.encode()).digest()[:16]
    return base64.urlsafe_b64encode(packed).decode().rstrip("=")


def _build_request(message: str, config: ElicitConfig) -> mcp_types.ElicitRequest:
    return mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema=config.schema,
        )
    )


def _settle(
    spec: ElicitParam,
    action: str,
    content: Any,
    config: ElicitConfig,
) -> Any:
    """Turn one answer into the value the parameter takes.

    Raises:
        ToolError: If a required parameter's question was declined or cancelled,
            or if accepted content does not match the schema it was asked for.
    """
    if action == "accept":
        try:
            return handle_elicit_accept(config, content).data
        except (ValidationError, ValueError) as e:
            raise ToolError(
                f"The answer for {spec.name!r} does not match the requested schema"
            ) from e
    if spec.has_default:
        return spec.default
    raise ToolError(
        f"Cannot continue without {spec.name!r}: the request was {action}d. "
        "Give the parameter a default to make it optional."
    )


async def resolve_elicitations(
    specs: Mapping[str, ElicitParam],
    arguments: Mapping[str, Any],
    context: Context,
) -> dict[str, Any]:
    """Fill every elicited parameter, asking the client for whatever is missing.

    `arguments` is the call's already-validated arguments, so a question built
    from one of them sees the same value the body will.

    Raises:
        NeedsInput: On the modern protocol, when questions remain unanswered.
            Carries this leg's `InputRequiredResult` payload.
        ToolError: If a required parameter's question was declined or cancelled.
    """
    if context._is_modern_protocol():
        return await _resolve_across_rounds(specs, arguments, context)
    return await _resolve_in_process(specs, arguments, context)


async def _resolve_in_process(
    specs: Mapping[str, ElicitParam],
    arguments: Mapping[str, Any],
    context: Context,
) -> dict[str, Any]:
    """Handshake-era path: ask over the back-channel while the call is open."""
    resolved: dict[str, Any] = {}
    for spec in specs.values():
        request = await spec.resolve({**arguments, **resolved})
        if not isinstance(request, Elicit):
            # The resolver already knew the answer, so nobody is asked.
            resolved[spec.name] = request
            continue
        outcome = await context.elicit(
            _message_text(request, spec),
            response_type=spec.type_for(request),
            response_title=request.title,
            response_description=request.description,
        )
        if outcome.action == "accept":
            resolved[spec.name] = outcome.data
        elif spec.has_default:
            resolved[spec.name] = spec.default
        else:
            raise ToolError(
                f"Cannot continue without {spec.name!r}: the request was "
                f"{outcome.action}d. Give the parameter a default to make it "
                "optional."
            )
    return resolved


def _message_text(request: Elicit[Any], spec: ElicitParam) -> str:
    """The text an `Elicit` shows the user.

    Raises:
        ToolError: If a resolver built an `Elicit` around another callable, which
            has no meaning — a resolver has already decided what to ask.
    """
    if isinstance(request.message, str):
        return request.message
    raise ToolError(
        f"The resolver for {spec.name!r} returned an Elicit wrapping a callable; "
        "return Elicit(<the question text>) instead"
    )


async def _resolve_across_rounds(
    specs: Mapping[str, ElicitParam],
    arguments: Mapping[str, Any],
    context: Context,
) -> dict[str, Any]:
    """Modern-protocol path: batch what is unanswered into one result.

    Every question that can be rendered this round is visited, so independent
    ones are all asked together rather than one per round trip. A question that
    is built from an unanswered one cannot be rendered yet and simply waits.
    """
    state = _decode_state(context.request_state)
    replies = context.input_responses or {}

    resolved: dict[str, Any] = {}
    pending: dict[str, Any] = {}
    carry: dict[str, _Answer] = {}
    asked: dict[str, str] = {}
    waiting: set[str] = set()

    for spec in specs.values():
        if any(dependency in waiting for dependency in spec.depends_on):
            # Its question quotes something nobody has answered yet.
            waiting.add(spec.name)
            continue

        decision = await spec.resolve({**arguments, **resolved})
        if not isinstance(decision, Elicit):
            # The resolver already knew the answer, so nothing is asked and
            # nothing is carried forward — it decides again next round.
            resolved[spec.name] = decision
            continue

        config = spec.config(decision)
        request = _build_request(_message_text(decision, spec), config)
        question = _digest(request)

        answer = _recall(state, spec.name, question)
        if answer is None:
            answer = _accept_reply(replies.get(spec.name), state, spec.name, question)
        if answer is None:
            pending[spec.name] = request
            asked[spec.name] = question
            waiting.add(spec.name)
            continue

        carry[spec.name] = answer
        resolved[spec.name] = _settle(spec, answer.action, answer.data, config)

    if pending:
        raise NeedsInput(pending, _encode_state(carry, asked))
    return resolved


def _recall(state: _State, name: str, question: str) -> _Answer | None:
    """An answer recorded on an earlier round, if it answered this same question."""
    answer = state.answers.get(name)
    if answer is None:
        return None
    if answer.q != question:
        logger.debug(
            "Dropping the recorded answer for %r: the question changed since it "
            "was asked",
            name,
        )
        return None
    return answer


def _accept_reply(
    reply: Any, state: _State, name: str, question: str
) -> _Answer | None:
    """A fresh reply from the client, if it answers the question we just asked."""
    if reply is None:
        return None
    if state.asked.get(name) != question:
        logger.info(
            "Discarding the reply for %r: the question changed since it was asked",
            name,
        )
        return None
    if not isinstance(reply, mcp_types.ElicitResult):
        raise ToolError(f"The response for {name!r} is not an elicitation result")
    if reply.action == "accept" and reply.content is None:
        raise ToolError(f"The answer for {name!r} was accepted but carries no content")
    return _Answer(action=reply.action, data=reply.content, q=question)
