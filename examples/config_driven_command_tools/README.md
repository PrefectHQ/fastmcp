# Config-driven Command Tools Example

通过一份 YAML/JSON 配置文件，将本地 CLI / 项目命令声明为 MCP 工具，在 FastMCP 启动时注册并暴露给 MCP 客户端调用。

## 功能简介

本示例演示如何用 **Provider** 将配置中的多条命令暴露为 MCP tools，无需修改 FastMCP 核心 API。适合已有现成脚本或 CLI 的工程，希望用最少代码把能力暴露给 Agent/MCP client。

## 配置示例

见同目录下的 `config.yaml`，结构示意：

```yaml
server_name: "Command Tools Example"

tools:
  - name: "echo_tool"
    description: "Echo a message to stdout"
    command: "python"
    args_template:
      - "scripts/echo_tool.py"
      - "--msg"
      - "{msg}"
    working_dir: "."
    env:
      PYTHONUNBUFFERED: "1"
    timeout_seconds: 30
    parameters:
      msg:
        type: "string"
        description: "Message to echo"
        required: true
```

支持的配置项：`command`、`args_template`、`working_dir`、`env`、`timeout_seconds`、`parameters`（含 type/description/required/default）。

## 测试环境（uv）

本仓库用 [uv](https://docs.astral.sh/uv/) 管理依赖，测试前需先配好环境：

```bash
# 在仓库根目录执行（若无 uv 会先安装）
bash scripts/setup_uv_env.sh
```

然后即可用 `uv run` 启动 server 或跑测试（见下方）。

## 启动方式

在仓库根目录执行：

```bash
uv run fastmcp run examples/config_driven_command_tools/server.py
```

或先进入本示例目录再指定 server 路径（从仓库根）：

```bash
cd examples/config_driven_command_tools
uv run fastmcp run server.py
```

（需在能访问到 `config.yaml` 和 `scripts/` 的环境下运行，一般推荐从仓库根执行第一条命令。）

## 运行测试

在仓库根目录、完成 `scripts/setup_uv_env.sh` 后：

```bash
uv run pytest tests/examples/test_config_driven_command_tools.py -v
```

## MCP 客户端调用示例

```python
from fastmcp import Client, FastMCP
from pathlib import Path
import sys
sys.path.insert(0, str(Path("examples/config_driven_command_tools").resolve()))
from config import load_config
from command_tool_provider import CommandToolProvider

config_path = Path("examples/config_driven_command_tools/config.yaml")
spec = load_config(config_path)
provider = CommandToolProvider(spec)
mcp = FastMCP(spec.server_name or "Command Tools Example", providers=[provider])

async def main():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        for t in tools:
            print(t.name, t.description)
        result = await client.call_tool("echo_tool", {"msg": "hello"})
        print(result.structured_content)

import asyncio
asyncio.run(main())
```

## 安全注意事项

- **禁止 `shell=True`**：所有命令均通过参数列表执行（`subprocess` 不用 shell），避免 shell 注入。
- **仅支持显式参数列表**：只使用 `command` + `args_template` 中的占位符替换，不支持 `&&`、`|`、`>` 等 shell 语法。
- **示例不替代沙箱**：本功能为示例级能力，若用于生产环境，请自行增加权限控制、目录白名单、隔离执行环境等安全措施。

## 适用场景

- 已有 CLI / bash / Python 入口脚本，希望零或极少业务代码改动即暴露为 MCP 工具。
- 希望用配置文件维护工具列表，而不是为每个命令写 Python tool 函数。

## 不适用场景

- 需要流式输出、shell 管道、后台任务协议、多阶段 workflow 等复杂能力（本 MVP 不支持）。
- 需要通用权限系统、动态 tool 热更新或复杂类型参数嵌套（本示例不覆盖）。
