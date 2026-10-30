import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import public_text

SCRIPT = Path(__file__).parent / "public_text.py"
ROOT = Path(__file__).parents[2]

GOOD = """Strict OpenAPI tools now reject undeclared top-level arguments before sending anything. Default mode is unchanged.

<details>
<summary>Checks</summary>

The strict-mode rejection tests fail on main and pass here.

</details>"""

BAD = """AI-assisted independent review, not a claim of human review.

Want: merge after CI.

Verified at 3f1c2a9b8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a with run 0b5e2d6c-1a2b-4c3d-8e9f-0a1b2c3d4e5f.

uv run pytest tests/server/providers/openapi -q
"""


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    payload.setdefault("cwd", str(ROOT))
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def test_concise_review_with_collapsed_checks_passes():
    assert public_text.violations(GOOD, "review") == []


def test_raw_report_style_is_rejected():
    found = public_text.violations(BAD, "review")
    assert '"Want:" / "Next action:" prefixes' in found
    assert "AI-review disclaimers" in found
    assert "full 40-character SHAs" in found
    assert "run UUIDs" in found
    assert "command transcripts" in found


def test_word_limit_counts_only_visible_text():
    hidden = "<details><summary>Checks</summary>\n\n" + "word " * 300 + "\n</details>"
    assert public_text.violations("Short finding.\n\n" + hidden, "review") == []
    assert public_text.violations("word " * 121, "review")


def test_pr_bodies_have_no_word_cap():
    assert public_text.violations("word " * 400, "body") == []


def test_bans_apply_inside_details():
    body = "Fine.\n\n<details><summary>Checks</summary>\n\nuv run pytest -q\n</details>"
    assert public_text.violations(body, "review") == ["command transcripts"]


def test_html_comment_markers_are_ignored():
    marker = "<!-- fastmcp-triage-review:v1 head:" + "a" * 40 + " -->"
    assert public_text.violations(marker + "\n\n" + GOOD, "review") == []


def test_hook_blocks_gh_review_body_file(tmp_path):
    body = tmp_path / "review.md"
    body.write_text(BAD)
    result = run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": f"gh pr review 6140 --repo PrefectHQ/fastmcp --approve --body-file {body}"
            },
        }
    )
    assert result.returncode == 2
    assert "Want:" in result.stderr


def test_hook_reads_heredoc_bodies():
    command = (
        "gh pr comment 1 --repo PrefectHQ/fastmcp --body-file - <<'EOF'\n"
        + BAD
        + "EOF\n"
    )
    assert (
        run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode
        == 2
    )


def test_hook_allows_good_inline_comment():
    command = f"gh issue comment 1 -R PrefectHQ/fastmcp --body {json.dumps('Thanks, merged.')}"
    assert (
        run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode
        == 0
    )


def test_hook_ignores_other_repositories(tmp_path):
    body = tmp_path / "review.md"
    body.write_text(BAD)
    command = f"gh pr review 1 --repo PrefectHQ/prefect --comment --body-file {body}"
    assert (
        run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode
        == 0
    )


def test_hook_blocks_github_mcp_review():
    payload = {
        "tool_name": "mcp__github__pull_request_review_write",
        "tool_input": {
            "owner": "PrefectHQ",
            "repo": "fastmcp",
            "pullNumber": 6140,
            "method": "create",
            "event": "APPROVE",
            "body": BAD,
        },
    }
    assert run_hook(payload).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin claude/fix-openapi-docs",
        "git checkout -b codex/triage-sweep",
        "gh pr create --repo PrefectHQ/fastmcp --head claude/x --title t --body ok",
    ],
)
def test_hook_blocks_harness_branch_names(command):
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 2
    assert "plain feature branch" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin fix-openapi-strict-docs",
        "git checkout -b 3.x-backport-timeouts",
        "git push origin main",
    ],
)
def test_hook_allows_plain_branch_names(command):
    assert (
        run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode
        == 0
    )


def test_hook_blocks_mcp_branch_creation():
    payload = {
        "tool_name": "mcp__github__create_branch",
        "tool_input": {"owner": "prefecthq", "repo": "fastmcp", "branch": "claude/foo"},
    }
    assert run_hook(payload).returncode == 2
