from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = """
import json, os, sys
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text())
assert os.getuid() == os.geteuid() == 65534
assert os.getgid() == os.getegid() == 65534
assert os.getgroups() == []
for key in ('PREFECT_API_KEY', 'PREFECT_API_URL', 'PREFECT_API_AUTH_STRING', 'GH_TOKEN', 'SSH_AUTH_SOCK'):
    assert key not in os.environ
status = Path('/proc/self/status').read_text()
for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
    assert int(next(line.split(':')[1].strip() for line in status.splitlines() if line.startswith(key + ':')), 16) == 0
assert 'NoNewPrivs:\\t1' in status
for name in (spec['private_file'], '/proc/' + str(spec['parent_pid']) + '/environ'):
    try:
        Path(name).read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError('Supervisor data readable: ' + name)
try:
    Path(__file__).write_text('overwritten')
except PermissionError:
    pass
else:
    raise AssertionError('Trusted runner writable')
if spec.get('oversize'):
    sys.stdout.write('x' * 16_000_001)
else:
    sys.stdout.write(json.dumps({'spec': spec, 'status': 'canary_passed'}))
"""


class Spec:
    def __init__(self, values: dict) -> None:
        self.values = values

    def model_dump(self, **kwargs) -> dict:
        return self.values

    def model_dump_json(self) -> str:
        return json.dumps(self.values)


with tempfile.TemporaryDirectory(prefix="verification-canary-") as temporary:
    root = Path(temporary)
    root.chmod(0o755)
    package = root / "source"
    package.mkdir(mode=0o755)
    helper = package / "agentic_verification_process.py"
    shutil.copyfile(ROOT / "flows/agentic_verification_process.py", helper)
    (package / "agentic_pr_verification.py").write_text(RUNNER)
    private = root / "supervisor-private"
    private.write_text("dummy-private-data")
    private.chmod(0o600)
    binary = root / "uv"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    os.environ["PATH"] = str(root) + ":" + os.environ["PATH"]
    module_spec = importlib.util.spec_from_file_location("isolation", helper)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    spec = Spec({"private_file": str(private), "parent_pid": os.getpid()})
    for oversized in (False, True):
        spec.values["oversize"] = oversized
        run = root / ("contribution" + ("-large" if oversized else "-normal"))
        run.mkdir()
        try:
            result = module.isolated_verification(spec, run)
        except ValueError as error:
            assert oversized and "exceeds 16 MB" in str(error)
        else:
            assert not oversized
            assert result["status"] == "canary_passed"
            assert result["execution"]["uid"] == 65534
    print(
        "credential boundary, capabilities, runner ownership, and output bounds passed"
    )
