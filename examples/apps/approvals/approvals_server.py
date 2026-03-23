"""Approval workflow — a FastMCPApp example with tabs, status badges, and action chaining.

Demonstrates a multi-step interactive workflow:
- @app.ui() entry point showing a pending approvals dashboard
- @app.tool() backend tools that the UI calls via CallTool
- @app.tool(model=True) for tools accessible from both model and UI
- Tabs with filtered lists and counter badges
- Action chaining: approve → update state → show toast

Usage:
    uv run python approvals_server.py              # HTTP (default)
    uv run python approvals_server.py --stdio       # stdio for MCP clients
"""

from __future__ import annotations

from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp, set_initial_state
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    CardContent,
    CardHeader,
    CardTitle,
    Column,
    ForEach,
    Heading,
    If,
    Muted,
    Row,
    Separator,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.rx import ERROR, RESULT, Rx

from fastmcp import FastMCP, FastMCPApp

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

_requests: list[dict] = [
    {
        "id": "REQ-001",
        "type": "expense",
        "title": "Client dinner — Acme Corp",
        "submitter": "Alice Chen",
        "description": "Business dinner with Acme Corp stakeholders to discuss Q3 partnership.",
        "amount": 284.50,
        "status": "pending",
        "created_at": "2026-03-18",
    },
    {
        "id": "REQ-002",
        "type": "access",
        "title": "Production database read access",
        "submitter": "Bob Martinez",
        "description": "Need read access to prod DB for quarterly analytics report.",
        "amount": None,
        "status": "pending",
        "created_at": "2026-03-19",
    },
    {
        "id": "REQ-003",
        "type": "time_off",
        "title": "Vacation — Apr 7-11",
        "submitter": "Carol Johnson",
        "description": "Family vacation, all deliverables handed off to David.",
        "amount": None,
        "status": "approved",
        "created_at": "2026-03-15",
    },
    {
        "id": "REQ-004",
        "type": "expense",
        "title": "Conference registration — PyCon 2026",
        "submitter": "David Kim",
        "description": "PyCon US 2026 early-bird registration plus tutorial day.",
        "amount": 650.00,
        "status": "pending",
        "created_at": "2026-03-20",
    },
    {
        "id": "REQ-005",
        "type": "access",
        "title": "AWS staging account access",
        "submitter": "Eva Mueller",
        "description": "Staging environment access for load testing new API endpoints.",
        "amount": None,
        "status": "rejected",
        "created_at": "2026-03-14",
    },
    {
        "id": "REQ-006",
        "type": "expense",
        "title": "Team offsite lunch",
        "submitter": "Frank Okafor",
        "description": "Catering for 12-person engineering offsite planning session.",
        "amount": 420.00,
        "status": "pending",
        "created_at": "2026-03-21",
    },
    {
        "id": "REQ-007",
        "type": "time_off",
        "title": "Personal day — Mar 28",
        "submitter": "Grace Liu",
        "description": "Personal appointment, will be available on Slack for emergencies.",
        "amount": None,
        "status": "pending",
        "created_at": "2026-03-20",
    },
    {
        "id": "REQ-008",
        "type": "expense",
        "title": "Software license — Figma annual",
        "submitter": "Hassan Ali",
        "description": "Annual Figma Professional license renewal for design team.",
        "amount": 144.00,
        "status": "approved",
        "created_at": "2026-03-12",
    },
]


def _by_status(status: str) -> list[dict]:
    return [r for r in _requests if r["status"] == status]


def _find_request(request_id: str) -> dict | None:
    for r in _requests:
        if r["id"] == request_id:
            return r
    return None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastMCPApp("Approvals")


def _all_lists() -> dict[str, list[dict]]:
    """Return state updates for all three status lists."""
    return {
        "pending_requests": _by_status("pending"),
        "approved_requests": _by_status("approved"),
        "rejected_requests": _by_status("rejected"),
    }


@app.tool()
def approve_request(request_id: str) -> dict[str, list[dict]]:
    """Approve a pending request and return updated lists."""
    req = _find_request(request_id)
    if req is None:
        raise ValueError(f"Request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is already {req['status']}")
    req["status"] = "approved"
    return _all_lists()


@app.tool()
def reject_request(request_id: str) -> dict[str, list[dict]]:
    """Reject a pending request and return updated lists."""
    req = _find_request(request_id)
    if req is None:
        raise ValueError(f"Request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is already {req['status']}")
    req["status"] = "rejected"
    return _all_lists()


@app.tool()
def add_comment(request_id: str, comment: str) -> dict:
    """Add a comment to a request. Returns the updated request."""
    req = _find_request(request_id)
    if req is None:
        raise ValueError(f"Request {request_id} not found")
    comments = req.setdefault("comments", [])
    comments.append(comment)
    return req


@app.tool(model=True)
def get_request_details(request_id: str) -> dict:
    """Get full details for a single request. Available to both model and UI."""
    req = _find_request(request_id)
    if req is None:
        raise ValueError(f"Request {request_id} not found")
    return req


@app.tool()
def list_requests(status: str | None = None) -> list[dict]:
    """List requests, optionally filtered by status."""
    if status is not None:
        return _by_status(status)
    return list(_requests)


def _update_all_lists() -> list:
    """Actions to update all three status lists from a tool result."""
    return [
        SetState("pending_requests", RESULT.pending_requests),
        SetState("approved_requests", RESULT.approved_requests),
        SetState("rejected_requests", RESULT.rejected_requests),
    ]


def _build_request_card(
    item: Rx,
    *,
    status_variant: str = "warning",
    show_actions: bool = False,
) -> None:
    """Build a card for a single request inside a ForEach context."""
    request_id = str(item.id)

    with Card():
        with CardHeader():
            with Row(gap=2, align="center", justify="between"):
                CardTitle(item.title)
                Badge(item.status, variant=status_variant)
        with CardContent(css_class="space-y-2"):
            with Row(gap=2, align="center"):
                Badge(item.type, variant="secondary")
                Text(item.submitter, css_class="font-medium")
                Muted(item.created_at)

            with If(item.amount):
                Text(item.amount.currency(), css_class="text-lg font-semibold")

            Muted(item.description)

            if show_actions:
                Separator()
                with Row(gap=2):
                    Button(
                        "Approve",
                        variant="default",
                        on_click=CallTool(
                            approve_request,
                            arguments={"request_id": request_id},
                            on_success=_update_all_lists()
                            + [
                                ShowToast(
                                    "Request approved",
                                    variant="success",
                                ),
                            ],
                            on_error=ShowToast(
                                ERROR,
                                variant="error",
                            ),
                        ),
                    )
                    Button(
                        "Reject",
                        variant="destructive",
                        on_click=CallTool(
                            reject_request,
                            arguments={"request_id": request_id},
                            on_success=_update_all_lists()
                            + [
                                ShowToast(
                                    "Request rejected",
                                    variant="warning",
                                ),
                            ],
                            on_error=ShowToast(
                                ERROR,
                                variant="error",
                            ),
                        ),
                    )


@app.ui()
def approval_dashboard() -> PrefabApp:
    """Open the approval dashboard. The model calls this to launch the app."""
    state = set_initial_state(
        pending_requests=_by_status("pending"),
        approved_requests=_by_status("approved"),
        rejected_requests=_by_status("rejected"),
    )

    pending_count = state.pending_requests.length()
    approved_count = state.approved_requests.length()
    rejected_count = state.rejected_requests.length()

    with Column(gap=6, css_class="p-6") as view:
        with Row(gap=3, align="center"):
            Heading("Approval Dashboard")
            Badge(pending_count, variant="warning")
            Muted("pending")

        with Tabs(value="pending"):
            with Tab(title="Pending"):
                with If(pending_count):
                    with ForEach("pending_requests") as item:
                        _build_request_card(item, show_actions=True)
                with If(~pending_count):
                    Muted("No pending requests.")

            with Tab(title="Approved"):
                with If(approved_count):
                    with ForEach("approved_requests") as item:
                        _build_request_card(item, status_variant="success")
                with If(~approved_count):
                    Muted("No approved requests.")

            with Tab(title="Rejected"):
                with If(rejected_count):
                    with ForEach("rejected_requests") as item:
                        _build_request_card(item, status_variant="destructive")
                with If(~rejected_count):
                    Muted("No rejected requests.")

    return PrefabApp(view=view)


mcp = FastMCP("Approvals Server", providers=[app])

if __name__ == "__main__":
    mcp.run(transport="http")
