import sys

import pytest

from flows.agentic_validate_patch import run_command


def test_failed_check_keeps_the_actual_failure_tail(tmp_path):
    with pytest.raises(RuntimeError) as command_error:
        run_command(
            [
                sys.executable,
                "-c",
                "print('x'*25000); print('actual check failure'); raise SystemExit(7)",
            ],
            tmp_path,
        )
    failure = str(command_error.value)
    assert "exited 7" in failure
    assert failure.endswith("actual check failure\n")
    assert len(failure) < 21000
