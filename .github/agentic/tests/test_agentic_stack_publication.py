from __future__ import annotations

import json
import os
import threading
import urllib.parse
from contextlib import contextmanager
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from flows.agentic_stack import assemble_stack, git
from flows.agentic_stack_publication import GitHub, reconcile_stack
from tests.test_agentic_stack import command, probe


class PublicationServer(ThreadingHTTPServer):
    def __init__(self, remote: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.remote = remote
        self.prs: dict[int, dict] = {}
        self.stacks: dict[int, dict] = {}
        self.disconnect_after: int | None = None
        self.posts = 0
        self.hold_after: int | None = None

    def head(self, ref: str) -> str | None:
        result = git(
            self.remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/{ref}"
        )
        return result or None

    def ref(self, ref: str) -> dict:
        return {
            "ref": ref,
            "sha": self.head(ref),
            "repo": {"full_name": "PrefectHQ/fastmcp"},
        }


class Handler(BaseHTTPRequestHandler):
    server: PublicationServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def respond(self, result: dict | list | None) -> None:
        self.send_response(404 if result is None else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        path = urllib.parse.unquote(path).removeprefix("/repos/PrefectHQ/fastmcp/")
        if path == "/user":
            self.respond({"id": 10, "login": "automation"})
        elif path.startswith("git/ref/heads/"):
            head = self.server.head(path.removeprefix("git/ref/heads/"))
            self.respond({"object": {"sha": head}} if head else None)
        elif path == "pulls":
            branch = params["head"][0].removeprefix("PrefectHQ:")
            self.respond(
                [pr for pr in self.server.prs.values() if pr["head"]["ref"] == branch]
            )
        elif path.startswith("pulls/"):
            pr = self.server.prs.get(int(path.split("/")[-1]))
            if pr:
                pr["head"] = self.server.ref(pr["head"]["ref"])
                pr["base"] = self.server.ref(pr["base"]["ref"])
            self.respond(pr)
        elif path == "stacks":
            number = int(params["pull_request"][0])
            self.respond(
                [
                    stack
                    for stack in self.server.stacks.values()
                    if number in [pr["number"] for pr in stack["pull_requests"]]
                ]
            )
        elif path.startswith("stacks/"):
            self.respond(self.server.stacks.get(int(path.split("/")[-1])))
        else:
            self.respond(None)

    def do_POST(self) -> None:
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/pulls"):
            number = len(self.server.prs) + 100
            result = {
                **data,
                "number": number,
                "head": self.server.ref(data["head"]),
                "base": self.server.ref(data["base"]),
                "state": "open",
                "merged_at": None,
                "user": {"id": 10},
                "labels": [],
                "html_url": f"https://github.com/PrefectHQ/fastmcp/pull/{number}",
            }
            self.server.prs[number] = result
        elif self.path.endswith("/stacks"):
            number = len(self.server.stacks) + 200
            result = {
                "number": number,
                "open": True,
                "base": {"ref": "main"},
                "pull_requests": [self.server.prs[n] for n in data["pull_requests"]],
                "url": f"https://api.github.com/repos/PrefectHQ/fastmcp/stacks/{number}",
            }
            self.server.stacks[number] = result
        else:
            self.respond(None)
            return
        self.server.posts += 1
        if self.server.posts == self.server.hold_after:
            self.server.prs[100]["labels"] = [{"name": "DNM"}]
        if self.server.posts == self.server.disconnect_after:
            self.close_connection = True
            return
        self.respond(result)


@contextmanager
def publication(tmp_path, repository):
    repo, spec = repository
    remote = tmp_path / "remote.git"
    command(tmp_path, "git", "clone", "--bare", str(repo), str(remote))
    artifact = assemble_stack(repo, spec, probe)
    destination = tmp_path / "publisher"
    command(tmp_path, "git", "clone", str(remote), str(destination))
    server = PublicationServer(remote)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            server,
            destination,
            artifact,
            GitHub("test-only", f"http://127.0.0.1:{server.server_port}"),
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def approved() -> None:
    pass


@pytest.mark.parametrize("lost_response", [1, 2, 3])
def test_lost_write_response_resumes_same_branches_prs_and_stack(
    tmp_path, repository, lost_response
):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        server.disconnect_after = lost_response
        with pytest.raises(RemoteDisconnected):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        result = reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        assert result["pull_requests"] == [100, 101]
        assert result["stack_number"] == 200
        assert server.posts == 3
        assert len(server.prs) == 2
        assert len(server.stacks) == 1
        assert server.prs[101]["base"]["ref"] == artifact["layers"][0]["branch"]
        for layer in artifact["layers"]:
            assert server.head(layer["branch"]) == layer["head_sha"]
            assert (
                git(server.remote, "show", "-s", "--format=%P", layer["head_sha"])
                == layer["parent_sha"]
            )
        assert (
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
            == result
        )
        assert server.posts == 3


@pytest.mark.parametrize(
    "intervention", ["closed", "ready", "hold", "base", "body", "head"]
)
def test_recovery_preserves_human_intervention(tmp_path, repository, intervention):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        server.disconnect_after = 1
        with pytest.raises(RemoteDisconnected):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        pr = server.prs[100]
        if intervention == "closed":
            pr["state"] = "closed"
        elif intervention == "ready":
            pr["draft"] = False
        elif intervention == "hold":
            pr["labels"] = [{"name": "do-not-merge"}]
        elif intervention == "base":
            pr["base"]["ref"] = "release/3.x"
        elif intervention == "body":
            pr["body"] += "\nMaintainer edit"
        else:
            command(
                server.remote,
                "git",
                "update-ref",
                f"refs/heads/{pr['head']['ref']}",
                artifact["spec"]["trunk_sha"],
            )
        with pytest.raises(ValueError):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        assert server.posts == 1
        assert server.head(artifact["layers"][1]["branch"]) is None


def test_changed_evidence_stops_recovery_before_another_write(tmp_path, repository):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        server.disconnect_after = 1
        with pytest.raises(RemoteDisconnected):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        evidence = tmp_path / "evidence"
        evidence.write_text("new contributor claim")

        def guard():
            if evidence.read_text() != "reviewed evidence":
                raise ValueError("Evidence changed")

        with pytest.raises(ValueError, match="Evidence changed"):
            reconcile_stack(checkout, artifact, api, guard, dict(os.environ))
        assert server.posts == 1


def test_trunk_change_stops_publication(tmp_path, repository):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        command(
            repository[0], "git", "push", str(server.remote), "HEAD:refs/heads/main"
        )
        with pytest.raises(ValueError, match="trunk changed"):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        assert server.posts == 0


def test_hold_on_lower_layer_stops_stack_creation(tmp_path, repository):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        server.hold_after = 2
        with pytest.raises(ValueError, match="Explicit hold"):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        assert server.posts == 2
        assert server.stacks == {}


def test_changed_stack_membership_is_not_repaired_over_human_changes(
    tmp_path, repository
):
    with publication(tmp_path, repository) as (server, checkout, artifact, api):
        reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        server.stacks[200]["pull_requests"].reverse()
        with pytest.raises(ValueError, match="membership"):
            reconcile_stack(checkout, artifact, api, approved, dict(os.environ))
        assert server.posts == 3
        assert [pr["number"] for pr in server.stacks[200]["pull_requests"]] == [
            101,
            100,
        ]
