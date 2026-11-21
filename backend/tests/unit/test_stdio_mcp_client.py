"""024 stdio MCP 客户端：真实子进程端到端 + 安全边界。

用 `tests/fixtures/fake_mcp_stdio_server.py` 当 server——它不是 mock，
是一个真的子进程，所以能验到「进程启停、管道、换行分帧、退出检测」这些
只有真进程才会暴露的问题。
"""

import os
import sys

import pytest

from app.executors.mcp_client import MCPProtocolError, MCPToolError, MCPTransportError
from app.executors.stdio_mcp_client import StdioCommandRejected, StdioMCPClient

SERVER = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "fake_mcp_stdio_server.py"
)
PY = os.path.basename(sys.executable)


def _client(*extra: str, **kwargs) -> StdioMCPClient:
    kwargs.setdefault("allowed_commands", (PY,))
    return StdioMCPClient(PY, [SERVER, *extra], **kwargs)


def test_rejects_command_outside_whitelist() -> None:
    """白名单是这条能力的安全底线：否则等于让 member 在 worker 里任意执行命令。"""
    with pytest.raises(StdioCommandRejected):
        StdioMCPClient("bash", ["-c", "rm -rf /"])


def test_rejects_shell_metacharacters_in_command() -> None:
    with pytest.raises(StdioCommandRejected):
        StdioMCPClient("/bin/sh; rm -rf /", [])


async def test_handshake_list_and_call() -> None:
    async with _client() as client:
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "fake"

        tools = await client.list_tools()
        assert [t.name for t in tools] == ["echo"]
        assert tools[0].input_schema["type"] == "object"

        result = await client.call_tool("echo", {"text": "你好"})
        assert result["content"][0]["text"] == "你好"


async def test_tool_error_is_raised() -> None:
    async with _client() as client:
        await client.initialize()
        with pytest.raises(MCPToolError):
            await client.call_tool("echo", {"text": "please-fail"})


async def test_missing_binary_raises_transport_error() -> None:
    client = StdioMCPClient(PY, ["/nonexistent/script.py"], allowed_commands=(PY,))
    # 命令存在（python），是脚本路径不存在 → 子进程启动后立刻退出
    async with client:
        with pytest.raises(MCPTransportError):
            await client.initialize()


async def test_server_crash_reports_stderr_tail() -> None:
    async with _client("--crash") as client:
        with pytest.raises(MCPTransportError) as exc:
            await client.initialize()
        # 错误信息里要带 stderr 尾部——否则用户只知道「失败了」
        assert "boom" in str(exc.value)


async def test_process_is_reaped_on_close() -> None:
    client = _client()
    async with client:
        await client.initialize()
        process = client._process
        assert process is not None
    assert process.returncode is not None, "关闭后不该留下僵尸子进程"


async def test_protocol_error_on_garbage_output() -> None:
    """server 往 stdout 打了非 JSON：必须是协议错误，而不是静默挂住。"""
    client = StdioMCPClient(
        PY,
        ["-c", "print('not json'); import time; time.sleep(5)"],
        allowed_commands=(PY,),
    )
    async with client:
        with pytest.raises(MCPProtocolError):
            await client.initialize()
