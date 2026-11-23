"""`EventPayload`, the base class for an event type you emit.

Experimental: this API may change in any release without a deprecation period.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel


class EventPayload(BaseModel):
    """The payload of an event type, and its declaration.

    Subclassing with `name=` declares the event type: the fields are its
    payload schema, the docstring its description, and the keyword parameters
    of an optional `matches` method its subscription arguments. Pass the
    class to `EventsExtension(events=[...])` on each server that emits it.

    ```python
    from fastmcp.experimental.events import EventPayload


    class AlertFired(EventPayload, name="alert.fired"):
        \"\"\"An alert fired.\"\"\"

        project: str

        def matches(self, project: str | None = None) -> bool:
            return project is None or self.project == project
    ```

    A subclass without `name=` declares no event type, so a shared base class
    can hold common fields.
    """

    __event_name__: ClassVar[str | None] = None
    __event_description__: ClassVar[str | None] = None

    def __init_subclass__(
        cls,
        *,
        name: str | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        # Set on every subclass, so an unnamed one does not inherit its
        # parent's event type.
        cls.__event_name__ = name
        cls.__event_description__ = description or _docstring(cls)
        if name is None:
            return
        from fastmcp.experimental.events.emitted import parse_matches

        # Fail at the class statement, not at the first poll.
        parse_matches(cls)


def _docstring(cls: type) -> str | None:
    import inspect

    doc = cls.__dict__.get("__doc__")
    return inspect.cleandoc(doc) if doc else None
