from typing import Any


class BackendUnavailable(Exception):
    pass


def search(query: str, category: str, cursor: str | None = None) -> dict[str, Any]:
    if query == "offline":
        raise BackendUnavailable("Catalog temporarily unavailable")
    data = {
        "books": [
            {"id": "b1", "title": "Python Field Guide"},
            {"id": "b2", "title": "Python Design"},
            {"id": "b3", "title": "Python Patterns"},
        ],
        "games": [{"id": "g1", "title": "Python Quest"}],
    }[category]
    rows = [row for row in data if query.lower() in row["title"].lower()]
    start = int(cursor) if cursor else 0
    end = start + 2
    return {
        "items": rows[start:end],
        "next_cursor": str(end) if end < len(rows) else None,
    }
