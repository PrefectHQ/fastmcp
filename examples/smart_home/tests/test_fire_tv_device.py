import asyncio
import os

import pytest
from smart_home.fire_tv.client import FireTVConnection, FireTVSettings
from smart_home.fire_tv.server import fire_tv_mcp

from fastmcp import Client


@pytest.mark.skipif(
    not os.getenv("FIRE_TV_TEST_RESTART_PACKAGE"),
    reason="Opt-in device test interrupts the selected app",
)
async def test_restart_installed_app_on_device() -> None:
    package = os.environ["FIRE_TV_TEST_RESTART_PACKAGE"]
    observer = FireTVConnection(FireTVSettings())

    async def process_id() -> str:
        try:
            device = await observer.get()
            assert package in (device.installed_apps or [])
            return (await device.adb_shell(f"pidof {package}") or "").strip()
        finally:
            await observer.close()

    async with Client(fire_tv_mcp) as client:
        before = (await client.call_tool("read_status")).data
        assert before.available
        rejected = await client.call_tool(
            "restart_app", {"package_id": "not.installed; reboot"}, raise_on_error=False
        )
        assert rejected.is_error
        old_pid = await process_id()
        assert old_pid, "Launch the selected app before running this test"
        receipt = (await client.call_tool("restart_app", {"package_id": package})).data
        assert receipt.accepted and not receipt.state_verified
        for _ in range(10):
            after = (await client.call_tool("read_status")).data
            if after.available and after.current_app == package:
                break
            await asyncio.sleep(1)
        else:
            pytest.fail(f"App did not return to foreground: {after}")
        new_pid = await process_id()
        assert new_pid and new_pid != old_pid
