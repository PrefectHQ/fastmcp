#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx",
#     "pydantic>=2",
#     "typesafe-sdk>=0.6",
#     "openai>=1.60",
#     "textual>=1.0",
# ]
# ///
"""Browse the issue ranking from rank_issues.py in a terminal UI.

Takes the same options as rank_issues.py (--repo, --judge, --limit, ...).

Keys: up/down select, o or enter open the issue, a open the author,
links in the detail pane open in the browser, k cycle the kind filter,
u show only issues without a maintainer reply, m load the next page,
r re-rank from the newest, q quit.

Usage:
    uv run scripts/rank_issues_tui.py --judge jev --limit 100
"""

from __future__ import annotations

import re
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from rank_issues import (  # noqa: E402
    KINDS,
    MAINTAINER_ASSOCIATIONS,
    Config,
    Ranked,
    parse_args,
    rank,
)
from rich.text import Text  # noqa: E402
from textual import work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.widgets import DataTable, Footer, Markdown, Static  # noqa: E402

BG = "#0d0807"
PANEL = "#171009"
SCAN = "#1d130b"
INK = "#ffd9b8"
MUTED = "#9c6f52"
BRIGHT = "#ff7a45"
TRACK = "#33200f"
BORDER = "#5c3322"

COMPONENT_LABELS = {
    "severity": "reads as severe",
    "security": "security-sensitive",
    "regression": "likely regression",
    "release_proximity": "right after a release it names",
    "repro": "has a reproduction",
    "actionable": "precise and actionable",
    "spec": "MCP spec conformance",
    "downstream": "breaks a downstream integration",
    "author_trust": "trusted author",
    "engagement": "others engaged",
    "neglect": "no maintainer reply yet",
    "handled": "assigned or has a linked PR",
    "feature": "feature request",
    "question": "usage question",
    "low_effort": "low-effort report",
    "spray": "author opens PRs across many repos",
    "dismissed": "labeled invalid, duplicate, or wontfix",
    "waiting": "waiting on the reporter",
    "priority": "labeled high priority",
    "deprioritized": "labeled low priority",
    "security_label": "labeled security",
    "feature_label": "labeled enhancement or proposal",
}

KIND_FILTERS = [None, *KINDS]


def score_bar(score: float, peak: float, width: int = 10) -> Text:
    filled = round(width * max(score, 0) / peak) if peak > 0 else 0
    return Text.assemble(("▓" * filled, BRIGHT), ("░" * (width - filled), TRACK))


ISSUE_REF = re.compile(r"(?<![\w/&\[])#(\d{2,6})\b")
FENCE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def label_query(label: str) -> str:
    return quote('is:open label:"' + label + '"')


def link_issue_refs(text: str, repo: str) -> str:
    """Turn `#1234` references outside code into links (GitHub redirects an
    issue URL to the pull request when the number is a PR)."""
    parts = FENCE.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = ISSUE_REF.sub(
            lambda m: f"[#{m.group(1)}](https://github.com/{repo}/issues/{m.group(1)})",
            parts[i],
        )
    return "".join(parts)


def top_kind(r: Ranked) -> tuple[str, float]:
    if not r.judgment:
        return "-", 0.0
    return max(r.judgment.kind.items(), key=lambda item: item[1])


def detail_markdown(r: Ranked, config: Config) -> str:
    issue, facts = r.issue, r.facts
    gh = f"https://github.com/{config.repo}"
    kind, kind_p = top_kind(r)
    release = facts.get("release_before")
    days = facts.get("days_after_release")
    release_line = (
        f"opened {days:.0f}d after [{release}]({gh}/releases/tag/{release})"
        if release and days is not None
        else "no release before it"
    )
    if facts.get("mentioned_version"):
        release_line += f", reports {facts['mentioned_version']}"
    labels = (
        ", ".join(
            f"[{label}]({gh}/issues?q={label_query(label)})" for label in issue.labels
        )
        or "none"
    )

    why = sorted(
        (
            (name, value, config.weights.get(name, 0.0) * value)
            for name, value in r.components.items()
        ),
        key=lambda item: -abs(item[2]),
    )
    why_lines = [
        f"- {'▲' if contribution > 0 else '▼'} {COMPONENT_LABELS.get(name, name)} "
        f"`{value:.2f}` → `{contribution:+.1f}`"
        for name, value, contribution in why
        if abs(contribution) >= 0.1
    ]

    author = facts.get("author") or {}
    now = datetime.now(timezone.utc)
    author_bits = []
    if issue.author_type != "User":
        author_bits.append("bot account")
    if issue.author_association in MAINTAINER_ASSOCIATIONS:
        author_bits.append("maintainer")
    if issue.author_created_at:
        author_bits.append(
            f"account {(now - issue.author_created_at).days // 365}y old"
        )
    author_bits.append(f"{issue.author_followers} followers")
    if author:
        who = quote(f"author:{issue.author}")
        author_bits.append(
            f"[{plural(author['merged_prs_in_repo'], 'merged PR')} here]({gh}/pulls?q=is:pr+is:merged+{who})"
        )
        author_bits.append(
            f"[{plural(author['issues_in_repo'], 'issue')} here]({gh}/issues?q=is:issue+{who})"
        )
        if author["unmerged_closed_prs_in_repo"]:
            author_bits.append(
                f"{author['unmerged_closed_prs_in_repo']} PRs closed unmerged"
            )
        author_bits.append(
            f"{author['recent_prs']} PRs in {author['recent_pr_repos']} other repos "
            f"in the last {config.spray_window_days}d"
        )

    others = sorted({c for c in issue.commenters if c != issue.author})
    state_bits = [
        "maintainer replied"
        if facts.get("maintainer_replied")
        else "**no maintainer reply**"
        if facts.get("maintainer_replied") is False
        else "reply history incomplete",
        f"assigned to {', '.join(issue.assignees)}"
        if issue.assignees
        else "unassigned",
        f"linked PRs {', '.join(f'#{n}' for n in issue.linked_prs)}"
        if issue.linked_prs
        else "no linked PR",
        f"{issue.reactions} reactions",
        f"{len(others)} other commenters",
    ]

    judgment_line = ""
    if r.judgment:
        j = r.judgment
        judgment_line = "## judgment\n\n" + " · ".join(
            [
                f"{kind} {kind_p:.0%}",
                f"severity `{j.severity:.2f}`",
                f"security `{j.security:.2f}`",
                f"repro `{j.repro:.2f}`",
                f"actionable `{j.actionable:.2f}`",
                f"spec `{j.spec:.2f}`",
                f"downstream `{j.downstream:.2f}`",
                f"low effort `{j.low_effort:.2f}`",
            ]
        )

    body = link_issue_refs(issue.body.strip(), config.repo) or "_no description_"
    if len(body) > config.body_chars:
        body = body[: config.body_chars] + "\n\n_… truncated_"

    return "\n\n".join(
        part
        for part in [
            f"# [#{issue.number}]({issue.url}) {issue.title}",
            f"**score {r.score:.1f}** · {kind} · {facts['age_days']:.0f}d old · {release_line}  \n"
            f"labels: {labels} · [open on GitHub]({issue.url})",
            "## why\n\n" + ("\n".join(why_lines) or "_no strong signals_"),
            f"## author\n\n**[{issue.author}](https://github.com/{issue.author})** · "
            + " · ".join(author_bits),
            "## state\n\n" + " · ".join(state_bits),
            judgment_line,
            "---",
            body,
        ]
        if part
    )


class RankApp(App[None]):
    """open issues, ranked by how much they deserve a maintainer's attention."""

    TITLE = "issues"

    CSS = f"""
    Screen {{ background: {BG}; color: {INK}; }}
    * {{
        scrollbar-color: {BORDER};
        scrollbar-color-hover: {BRIGHT};
        scrollbar-color-active: {BRIGHT};
        scrollbar-background: {PANEL};
        scrollbar-corner-color: {PANEL};
        scrollbar-size-vertical: 1;
    }}
    #hero {{
        height: 3;
        padding: 0 2;
        background: {PANEL};
        border-bottom: double {BORDER};
    }}
    #title {{ color: {BRIGHT}; text-style: bold; }}
    #stats {{ color: {MUTED}; }}
    #table {{ width: 3fr; height: 1fr; background: {BG}; }}
    #detail {{
        width: 2fr;
        padding: 0 2;
        border-left: heavy {BORDER};
        background: {BG};
    }}
    DataTable > .datatable--header {{ background: {BG}; color: {BRIGHT}; text-style: bold; }}
    DataTable > .datatable--cursor {{ background: {TRACK}; }}
    DataTable > .datatable--even-row {{ background: {SCAN}; }}
    DataTable > .datatable--odd-row {{ background: {BG}; }}
    Markdown {{ background: {BG}; color: {INK}; }}
    MarkdownH1 {{ color: {BRIGHT}; background: {BG}; text-style: bold; }}
    MarkdownH2 {{ color: {BRIGHT}; text-style: bold; }}
    MarkdownH3, MarkdownH4 {{ color: {BRIGHT}; }}
    Footer {{ background: {PANEL}; }}
    FooterKey {{ background: {PANEL}; color: {MUTED}; }}
    FooterKey > .footer-key--key {{ background: {PANEL}; color: {BRIGHT}; }}
    FooterKey > .footer-key--description {{ background: {PANEL}; color: {MUTED}; }}
    """

    BINDINGS = [
        Binding("o", "open", "open"),
        Binding("a", "open_author", "author"),
        Binding("k", "cycle_kind", "kind"),
        Binding("u", "toggle_unanswered", "unanswered"),
        Binding("m", "more", "more"),
        Binding("r", "refresh", "re-rank"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.ranked: list[Ranked] = []
        self.shown: list[Ranked] = []
        self.kind_filter: str | None = None
        self.unanswered_only = False
        self.progress: dict[str, tuple[int, int]] = {}
        self.next_cursor: str | None = None
        self.exhausted = False

    def compose(self) -> ComposeResult:
        with Vertical(id="hero"):
            yield Static(f"issues · {self.config.repo}", id="title")
            yield Static("ranking…", id="stats")
        with Horizontal():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail"):
                yield Markdown("_ranking…_", id="body")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "issue", "score", "", "kind", "author", "age", "↩")
        table.focus()
        self.load()

    @work(exclusive=True)
    async def load(self, more: bool = False) -> None:
        table = self.query_one(DataTable)
        table.loading = True
        self.progress = {}
        try:
            page = await rank(
                self.config, self.report_progress, self.next_cursor if more else None
            )
        except Exception as exc:
            self.notify(f"ranking failed: {exc}", severity="error", timeout=15)
            return
        finally:
            table.loading = False
        if more:
            known = {r.issue.number for r in self.ranked}
            merged = self.ranked + [
                r for r in page.ranked if r.issue.number not in known
            ]
            self.ranked = sorted(merged, key=lambda r: -r.score)
        else:
            self.ranked = page.ranked
        self.next_cursor = page.next_cursor
        self.exhausted = page.next_cursor is None
        self.populate()

    def report_progress(self, phase: str, done: int, total: int) -> None:
        self.progress[phase] = (done, total)
        self.query_one("#stats", Static).update(
            "ranking… "
            + " · ".join(f"{name} {d}/{t}" for name, (d, t) in self.progress.items())
        )

    def populate(self) -> None:
        self.shown = [
            r
            for r in self.ranked
            if (self.kind_filter is None or top_kind(r)[0] == self.kind_filter)
            and (not self.unanswered_only or r.facts.get("maintainer_replied") is False)
        ]
        table = self.query_one(DataTable)
        table.clear()
        peak = max((r.score for r in self.ranked), default=0.0)
        for position, r in enumerate(self.shown, 1):
            kind, _ = top_kind(r)
            table.add_row(
                str(position),
                f"#{r.issue.number} {r.issue.title[:40]}",
                f"{r.score:.1f}",
                score_bar(r.score, peak),
                kind,
                r.issue.author[:14],
                f"{r.facts['age_days']:.0f}d",
                "✓"
                if r.facts.get("maintainer_replied")
                else "·"
                if r.facts.get("maintainer_replied") is False
                else "?",
                key=str(r.issue.number),
            )
        filters = [f"kind={self.kind_filter}"] if self.kind_filter else []
        if self.unanswered_only:
            filters.append("unanswered only")
        unanswered = sum(
            1 for r in self.ranked if r.facts.get("maintainer_replied") is False
        )
        security = sum(
            1 for r in self.ranked if r.judgment and r.judgment.security >= 0.5
        )
        regressions = sum(
            1 for r in self.ranked if r.components.get("regression", 0) >= 0.5
        )
        stats = (
            f"{len(self.shown)} shown of {len(self.ranked)} loaded · {unanswered} without a maintainer reply · "
            f"{security} security-flagged · {regressions} likely regressions · judge {self.config.judge}"
            + ("" if self.exhausted else " · m for more")
        )
        if filters:
            stats += f" · {' · '.join(filters)}"
        if self.config.judge == "none":
            stats += " · no judge: facts and labels only (set TYPESAFE_API_KEY)"
        self.query_one("#stats", Static).update(stats)
        table.focus()
        if self.shown:
            self.show(self.shown[0])
        else:
            self.query_one(Markdown).update("_no issues match the filters_")

    def show(self, r: Ranked) -> None:
        self.query_one(Markdown).update(detail_markdown(r, self.config))
        self.query_one("#detail", VerticalScroll).scroll_home(animate=False)

    def selected(self) -> Ranked | None:
        table = self.query_one(DataTable)
        if not self.shown or table.cursor_row >= len(self.shown):
            return None
        return self.shown[table.cursor_row]

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        r = self.selected()
        if r:
            self.show(r)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

    def action_open(self) -> None:
        r = self.selected()
        if r:
            webbrowser.open(r.issue.url)

    def action_open_author(self) -> None:
        r = self.selected()
        if r:
            webbrowser.open(f"https://github.com/{r.issue.author}")

    def action_cycle_kind(self) -> None:
        index = KIND_FILTERS.index(self.kind_filter)
        self.kind_filter = KIND_FILTERS[(index + 1) % len(KIND_FILTERS)]
        self.populate()

    def action_toggle_unanswered(self) -> None:
        self.unanswered_only = not self.unanswered_only
        self.populate()

    def action_refresh(self) -> None:
        self.next_cursor = None
        self.load()

    def action_more(self) -> None:
        if self.exhausted:
            self.notify("no more open issues")
            return
        self.load(more=True)


def main() -> None:
    config, _ = parse_args()
    RankApp(config).run()


if __name__ == "__main__":
    main()
