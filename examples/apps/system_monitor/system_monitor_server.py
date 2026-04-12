"""System monitor — live CPU, memory, and disk stats from the host machine.

Auto-refreshes every 3 seconds via SetInterval + CallTool.

Requires psutil: pip install psutil

Usage:
    fastmcp dev apps system_monitor_server.py
"""

import platform
import time
from datetime import datetime

import psutil
from prefab_ui.actions import SetInterval, SetState
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Card,
    CardContent,
    CardHeader,
    Column,
    Grid,
    Heading,
    Metric,
    Muted,
    Progress,
    Row,
    Select,
    SelectOption,
    Small,
    Text,
)
from prefab_ui.components.charts import AreaChart, ChartSeries
from prefab_ui.components.control_flow import ForEach
from prefab_ui.rx import RESULT, STATE, Rx

from fastmcp import FastMCP
from fastmcp.apps.app import FastMCPApp

app = FastMCPApp("Monitor")

_history: list[dict] = []


def _collect_stats() -> dict:
    """Collect a full snapshot of system stats."""
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    now = datetime.now().strftime("%H:%M:%S")
    _history.append({"time": now, "cpu": cpu, "memory": mem.percent})
    if len(_history) > 100:
        del _history[: len(_history) - 100]

    top_procs = []
    for p in sorted(
        psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
        key=lambda p: p.info.get("cpu_percent") or 0,
        reverse=True,
    )[:6]:
        info = p.info
        top_procs.append(
            {
                "pid": info.get("pid") or 0,
                "name": info.get("name") or "unknown",
                "cpu": f"{(info.get('cpu_percent') or 0):.1f}%",
                "memory": f"{(info.get('memory_percent') or 0):.1f}%",
            }
        )

    return {
        "cpu": cpu,
        "mem_pct": mem.percent,
        "mem_used": mem.used // (1024**3),
        "mem_total": mem.total // (1024**3),
        "disk_pct": disk.percent,
        "disk_used": disk.used // (1024**3),
        "disk_total": disk.total // (1024**3),
        "uptime": _format_uptime(),
        "cores": psutil.cpu_count(),
        "platform": f"{platform.system()} {platform.machine()}",
        "hostname": platform.node(),
        "healthy": cpu < 80 and mem.percent < 90,
        "history": list(_history),
        "top_procs": top_procs,
    }


def _format_uptime() -> str:
    elapsed = int(time.time() - psutil.boot_time())
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


@app.tool()
def refresh() -> dict:
    """Collect fresh system stats."""
    return _collect_stats()


@app.ui()
def system_dashboard() -> PrefabApp:
    """Live system dashboard with auto-refresh."""
    initial = _collect_stats()

    with PrefabApp(state={"stats": initial, "interval": "500"}) as ui:
        with Column(
            gap=6,
            css_class="p-6",
            on_mount=SetInterval(
                duration=Rx("interval"),
                on_tick=CallTool(
                    "refresh",
                    on_success=SetState("stats", RESULT),
                ),
            ),
        ):
            with Row(gap=3, align="center"):
                Heading("System Monitor")
                Badge(STATE.stats.hostname, variant="outline")
                with Select(name="interval", css_class="w-32"):
                    SelectOption("0.5s", value="500")
                    SelectOption("1s", value="1000")
                    SelectOption("5s", value="5000")

            with Grid(columns=4, gap=4):
                with Card():
                    with CardContent():
                        Metric(label="CPU", value=f"{STATE.stats.cpu}%")
                        Progress(value=STATE.stats.cpu)

                with Card():
                    with CardContent():
                        Metric(label="Memory", value=f"{STATE.stats.mem_pct}%")
                        Progress(value=STATE.stats.mem_pct)
                        Muted(f"{STATE.stats.mem_used}GB / {STATE.stats.mem_total}GB")

                with Card():
                    with CardContent():
                        Metric(label="Disk", value=f"{STATE.stats.disk_pct}%")
                        Progress(value=STATE.stats.disk_pct)
                        Muted(f"{STATE.stats.disk_used}GB / {STATE.stats.disk_total}GB")

                with Card():
                    with CardContent():
                        Metric(label="Uptime", value=STATE.stats.uptime)
                        Muted(f"{STATE.stats.cores} cores")

            with Grid(columns=[2, 1], gap=4):
                with Card():
                    with CardHeader():
                        Text("CPU & Memory", css_class="text-sm font-medium")
                    with CardContent():
                        AreaChart(
                            data=STATE.stats.history,
                            series=[
                                ChartSeries(data_key="cpu", label="CPU %"),
                                ChartSeries(data_key="memory", label="Memory %"),
                            ],
                            x_axis="time",
                            curve="smooth",
                            show_legend=True,
                            height=220,
                            animate=False,
                        )

                with Card():
                    with CardHeader():
                        Text("Top Processes", css_class="text-sm font-medium")
                    with CardContent():
                        with Column(gap=2):
                            with ForEach("stats.top_procs") as proc:
                                with Row(justify="between", align="center"):
                                    with Column(gap=0):
                                        Small(proc.name)
                                        Muted(proc.pid)
                                    with Row(gap=2):
                                        Badge(proc.cpu, variant="outline")
                                        Badge(proc.memory, variant="outline")

    return ui


mcp = FastMCP("System Monitor", providers=[app])

if __name__ == "__main__":
    mcp.run()
