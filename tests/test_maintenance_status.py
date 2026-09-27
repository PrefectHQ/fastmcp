import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def status_module():
    path = Path(__file__).parents[1] / "scripts" / "maintenance_status.py"
    spec = importlib.util.spec_from_file_location("maintenance_status", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_health_never_reuses_old_commit_success(status_module, monkeypatch):
    def github(*args):
        if args[-1].endswith("commits/main"):
            return {
                "sha": "new",
                "html_url": "https://github.com/PrefectHQ/fastmcp/commit/new",
            }
        return {
            "workflow_runs": [
                {
                    "name": "Tests",
                    "head_sha": "old",
                    "event": "push",
                    "id": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "old-run",
                }
            ]
        }

    monkeypatch.setattr(status_module, "gh_json", github)
    result = status_module.main_health()
    assert result["sha"] == "new"
    assert all(check["state"] == "unknown" for check in result["checks"])


def test_main_health_recognizes_automatic_codeql(status_module, monkeypatch):
    def github(*args):
        if args[-1].endswith("commits/main"):
            return {"sha": "head", "html_url": "commit"}
        return {
            "workflow_runs": [
                {
                    "name": "Push on main",
                    "path": "dynamic/github-code-scanning/codeql",
                    "event": "dynamic",
                    "head_sha": "head",
                    "id": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "codeql-run",
                }
            ]
        }

    monkeypatch.setattr(status_module, "gh_json", github)
    assert status_module.main_health()["checks"][2] == {
        "name": "CodeQL",
        "state": "ok",
        "url": "codeql-run",
    }


@pytest.mark.parametrize(
    "status,conclusion,expected",
    [
        ("in_progress", None, "pending"),
        ("completed", "success", "ok"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "unknown"),
        ("completed", "skipped", "unknown"),
    ],
)
def test_main_health_uses_latest_attempt(
    status_module, monkeypatch, status, conclusion, expected
):
    def github(*args):
        if args[-1].endswith("commits/main"):
            return {"sha": "head", "html_url": "commit"}
        return {
            "workflow_runs": [
                {
                    "name": "Tests",
                    "head_sha": "head",
                    "event": "push",
                    "id": 2,
                    "status": status,
                    "conclusion": conclusion,
                    "html_url": "new-run",
                },
                {
                    "name": "Tests",
                    "head_sha": "head",
                    "event": "push",
                    "id": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "old-run",
                },
            ]
        }

    monkeypatch.setattr(status_module, "gh_json", github)
    assert status_module.main_health()["checks"][0] == {
        "name": "Tests",
        "state": expected,
        "url": "new-run",
    }
