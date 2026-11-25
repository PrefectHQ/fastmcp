"""How fastmcp-events loads: the client side without the server side."""

import subprocess
import sys


def test_the_client_side_imports_without_the_server():
    code = (
        "import sys\n"
        "from fastmcp_events import EventsClientExtension, subscribe_events\n"
        "loaded = [m for m in sys.modules if m.startswith('fastmcp.server')]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
