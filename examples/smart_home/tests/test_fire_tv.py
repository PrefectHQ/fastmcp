import pytest
from smart_home.fire_tv import client as fire_tv_client
from smart_home.fire_tv.server import fire_tv_mcp

from fastmcp import Client


class FakeFireTV:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.device_properties = {"manufacturer": "Amazon", "model": "AFTTIFF43"}
        self.installed_apps = [
            "com.amazon.firetv.youtube",
            "com.amazon.tv.launcher",
        ]
        self.commands: list[tuple[str, str | None]] = []
        self.closed = False

    async def update(
        self, get_running_apps: bool = True, lazy: bool = True
    ) -> tuple[str, str, list[str], None]:
        assert get_running_apps is False
        assert lazy is True
        return "idle", "com.amazon.tv.launcher", ["com.amazon.tv.launcher"], None

    async def home(self) -> None:
        self.commands.append(("home", None))

    async def launch_app(self, app: str) -> None:
        self.commands.append(("launch_app", app))

    async def adb_shell(self, command: str) -> str | None:
        self.commands.append(("adb_shell", command))
        return None

    async def adb_close(self) -> None:
        self.closed = True


@pytest.fixture
def configured_fire_tv(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeFireTV, list[dict[str, object]]]:
    device = FakeFireTV()
    setup_calls: list[dict[str, object]] = []

    async def setup(**kwargs: object) -> FakeFireTV:
        setup_calls.append(kwargs)
        return device

    monkeypatch.setenv("FIRE_TV_HOST", "192.0.2.10")
    monkeypatch.setenv("FIRE_TV_ADB_SERVER_IP", "127.0.0.1")
    monkeypatch.setattr(fire_tv_client, "setup_android_tv", setup)
    return device, setup_calls


async def test_status_and_commands_share_one_connection(configured_fire_tv):
    device, setup_calls = configured_fire_tv
    async with Client(fire_tv_mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {
            "read_status",
            "press_home",
            "launch_app",
            "play_youtube_video",
        }
        assert tools["read_status"].annotations.read_only_hint is True
        assert tools["press_home"].annotations.read_only_hint is False

        status = (await client.call_tool("read_status")).data
        assert status.model == "AFTTIFF43"
        assert status.current_app == "com.amazon.tv.launcher"

        receipt = (await client.call_tool("press_home")).data
        assert receipt.accepted is True
        assert receipt.state_verified is False

        launch = (
            await client.call_tool(
                "launch_app", {"package_id": "com.amazon.firetv.youtube"}
            )
        ).data
        assert launch.command == "launch_app:com.amazon.firetv.youtube"

        video = (
            await client.call_tool("play_youtube_video", {"video_id": "D1VM6V6wmU0"})
        ).data
        assert video.command == "play_youtube_video:D1VM6V6wmU0"

    assert setup_calls == [
        {
            "host": "192.0.2.10",
            "port": 5555,
            "adbkey": "",
            "adb_server_ip": "127.0.0.1",
            "adb_server_port": 5037,
            "device_class": "firetv",
        }
    ]
    assert device.commands == [
        ("home", None),
        ("launch_app", "com.amazon.firetv.youtube"),
        (
            "adb_shell",
            "am start -a android.intent.action.VIEW "
            "-d https://www.youtube.com/watch?v=D1VM6V6wmU0 "
            "com.amazon.firetv.youtube",
        ),
    ]
    assert device.closed is True


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("launch_app", {"package_id": "not.installed"}, "not installed"),
        (
            "play_youtube_video",
            {"video_id": "invalid; reboot"},
            "11 URL-safe characters",
        ),
    ],
)
async def test_invalid_targets_are_rejected_without_device_commands(
    configured_fire_tv,
    tool: str,
    arguments: dict[str, str],
    message: str,
):
    device, _ = configured_fire_tv
    async with Client(fire_tv_mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
        assert result.is_error
        assert message in str(result.content)

    assert device.commands == []


async def test_unconfigured_server_exposes_tools_but_refuses_calls(monkeypatch):
    monkeypatch.delenv("FIRE_TV_HOST", raising=False)
    async with Client(fire_tv_mcp) as client:
        result = await client.call_tool("read_status", raise_on_error=False)
        assert result.is_error
        assert "FIRE_TV_HOST" in str(result.content)


async def test_unavailable_device_is_closed(monkeypatch):
    device = FakeFireTV(available=False)

    async def setup(**kwargs: object) -> FakeFireTV:
        return device

    monkeypatch.setenv("FIRE_TV_HOST", "192.0.2.10")
    monkeypatch.setattr(fire_tv_client, "setup_android_tv", setup)

    with pytest.raises(RuntimeError, match="unavailable"):
        async with Client(fire_tv_mcp):
            pass

    assert device.closed is True
