from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flows.agentic_pr_verification import VerificationSpec


def isolated_verification(spec: VerificationSpec, directory: Path) -> dict:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Managed verification requires Linux with a root supervisor")
    setpriv = shutil.which("setpriv")
    uv = shutil.which("uv")
    if setpriv is None or uv is None:
        raise RuntimeError("Managed verification requires setpriv and uv")
    directory.chmod(0o711)
    trusted = directory / "runner"
    trusted.mkdir(mode=0o755)
    package = trusted / "flows"
    package.mkdir(mode=0o755)
    for source in Path(__file__).parent.glob("*.py"):
        target = package / source.name
        target.write_bytes(source.read_bytes())
        target.chmod(0o644)
    binaries = trusted / "bin"
    binaries.mkdir(mode=0o755)
    shutil.copyfile(uv, binaries / "uv")
    (binaries / "uv").chmod(0o755)
    request = trusted / "spec.json"
    request.write_text(spec.model_dump_json())
    request.chmod(0o644)
    scratch = directory / "scratch"
    scratch.mkdir(mode=0o700)
    os.chown(scratch, 65534, 65534)
    environment = {
        "PATH": f"{binaries}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "PYTHONPATH": str(trusted),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "PREFECT_LOGGING_TO_API_ENABLED": "false",
    }
    command = [
        setpriv,
        "--reuid=65534",
        "--regid=65534",
        "--clear-groups",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        sys.executable,
        "-m",
        "flows.agentic_pr_verification",
        str(request),
    ]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            cwd=scratch,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            start_new_session=True,
        )
        try:
            process.wait(timeout=2850)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if process.returncode != 0:
            size = os.fstat(errors.fileno()).st_size
            errors.seek(max(0, size - 24000))
            detail = errors.read().decode(errors="replace")
            raise RuntimeError(
                f"Isolated verification exited with code {process.returncode}: {detail}"
            )
        if os.fstat(output.fileno()).st_size > 16_000_000:
            raise ValueError("Verification result exceeds 16 MB")
        output.seek(0)
        result = json.load(output)
    if result.get("spec") != spec.model_dump(mode="json"):
        raise ValueError("Isolated verification returned different inputs")
    result["execution"] = {"uid": 65534, "gid": 65534, "credentials_inherited": False}
    return result
