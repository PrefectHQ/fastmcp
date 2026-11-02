from __future__ import annotations

import os
from pathlib import Path

import pytest

from fastmcp.skills._resources import read_file


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires named pipes")
def test_fifo_is_rejected_without_blocking(tmp_path: Path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ValueError, match="regular file"):
        read_file(tmp_path, "pipe")
