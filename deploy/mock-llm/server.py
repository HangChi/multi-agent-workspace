"""Mock LLM 网关（quickstart S5 验证用，非生产组件）。

OpenAI 兼容 /v1/chat/completions，供 LiteLLMClient 真实调用：
- 无工具调用请求 → 直接返回最终答案（一次完成，SC-004 简单执行）
- 带 tools 的请求 → 首轮返回 web_search 工具调用，二轮返回最终答案（ReAct 两轮闭环）
- 路径 /fail → 始终返回 500（验证失败→重试路径）
- 路径 /needs-input → 返回 __NEEDS_INPUT__ 前缀（验证 needs_input 挂起）
- usage 返回固定 token 数（成本累计验证，SC-006）
- 显式 [sandbox:<scenario>] 标记驱动 002 验收；仅返回固定工具调用，不执行代码。
  场景：python_sum / shell / failure / artifacts / denied / no_tools。

仅用于本地验证栈，不打包进生产镜像。
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.getenv("MOCK_LLM_MODEL", "gpt-4o-mini")
MODE = os.getenv("MOCK_LLM_MODE", "auto")  # auto | fail | needs_input

# These are model responses, never commands run by this HTTP gateway. Only the
# application's normal authorization/executor path may run them in a sandbox.
SANDBOX_CALLS = {
    "python_sum": ("run_python", {"code": "print(sum(i * i for i in range(1, 101)))"}),
    "shell": ("run_shell", {"command": "printf 'sandbox-shell-ok\\n'"}),
    "failure": ("run_python", {"code": "raise RuntimeError('sandbox-controlled-error')"}),
    "artifacts": (
        "run_python",
        {"code": (
            "import csv\n"
            "from pathlib import Path\n"
            "path = Path('/workspace/output/sales.csv')\n"
            "with path.open('w', newline='') as stream:\n"
            "    writer = csv.writer(stream)\n"
            "    writer.writerow(['day', 'sales'])\n"
            "    writer.writerows((day, day * 10) for day in range(1, 21))\n"
            "print('sales.csv: 20 rows')\n"
        )},
    ),
    # Deliberately absent from the advertised schemas in the denial scenario.
    "denied": ("run_shell", {"command": "printf 'UNAUTHORIZED_EXECUTION\\n'"}),
}
SANDBOX_MARKER = re.compile(r"\[sandbox:([a-z_]+)\]")


def sandbox_reply(messages, tools, last_user):
    """Return fixed 002 responses, or None for the existing 001 mock behavior."""
    marker = SANDBOX_MARKER.search(last_user)
    if marker is None:
        return None
    scenario = marker.group(1)
    if scenario not in {*SANDBOX_CALLS, "no_tools"}:
        raise ValueError("unknown sandbox scenario")
    tool_messages = [message for message in messages if message.get("role") == "tool"]
    if scenario == "no_tools":
        return ("SANDBOX_NO_TOOLS_OK" if not tools and not tool_messages
                else "SANDBOX_SCENARIO_ERROR: unexpected tools"), None
    name, arguments = SANDBOX_CALLS[scenario]
    advertised = {tool.get("function", {}).get("name") for tool in tools}
    if scenario != "denied" and name not in advertised:
        return "SANDBOX_SCENARIO_ERROR: required tool not advertised", None
    if not tool_messages:
        return "", [{
            "id": f"call_sandbox_{scenario}_1",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]
    try:
        output = json.loads(tool_messages[-1].get("content", ""))
    except (TypeError, ValueError):
        return "SANDBOX_SCENARIO_ERROR: invalid tool result", None
    if not isinstance(output, dict):
        return "SANDBOX_SCENARIO_ERROR: invalid tool result", None
    if scenario == "denied":
        confirmed = output.get("denied") is True and output.get("ok") is False
        return ("SANDBOX_DENIED_CONFIRMED" if confirmed
                else "SANDBOX_SCENARIO_ERROR: tool was not denied"), None
    if scenario == "failure" and len(tool_messages) == 1:
        if output.get("status") != "failed" or output.get("error_category") != "exit_code":
            return "SANDBOX_SCENARIO_ERROR: expected controlled failure", None
        return "", [{
            "id": "call_sandbox_failure_2",
            "type": "function",
            "function": {
                "name": "run_python",
                "arguments": json.dumps({"code": "print('sandbox-recovered')"}),
            },
        }]
    if output.get("ok") is not True or output.get("exit_code") != 0:
        return "SANDBOX_SCENARIO_ERROR: tool execution unsuccessful", None
    return f"SANDBOX_{scenario.upper()}_OK: {output.get('stdout', '').strip()}", None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        pass

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        # litellm 对 api_base 的拼接：请求 /chat/completions 或 /v1/chat/completions，
        # 某些版本直接 POST api_base 根路径。统一兜底处理。
        if self.path not in ("/chat/completions", "/v1/chat/completions", "/"):
            self._json(404, {"error": f"unknown path {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        messages = body.get("messages", [])
        tools = body.get("tools", [])
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        has_tool_call = any(m.get("tool_calls") for m in messages)

        # Route explicit acceptance markers before broad FAIL keyword matching.
        # In particular, [sandbox:failure] is a tool failure, not an LLM HTTP 500.
        try:
            scenario_reply = sandbox_reply(messages, tools, last_user)
        except ValueError:
            self._json(400, {"error": "unknown sandbox scenario"})
            return
        if scenario_reply is not None:
            content, tool_calls = scenario_reply
            usage = {"prompt_tokens": 100, "completion_tokens": 50}
            self._json(200, self._completion(content, usage, tools, has_tool_call, tool_calls))
            return

        mode = MODE
        # 支持按需求关键词路由（验证不同场景）
        if "FAIL" in last_user.upper() or "/fail" in last_user:
            mode = "fail"
        elif "补充信息" in last_user or "NEEDS_INPUT" in last_user.upper():
            mode = "needs_input"
        elif "SLOW" in last_user.upper():
            mode = "slow"

        if mode == "fail":
            # 模拟 LLM 服务不可用
            self._json(500, {"error": {"message": "mock LLM 服务不可用（模拟故障）", "type": "server_error"}})
            return

        if mode == "slow":
            # 模拟慢响应（验证取消竞态窗口 FR-018）：每轮 sleep 3s
            import time
            time.sleep(3)

        if mode == "needs_input":
            content = "__NEEDS_INPUT__ 请提供更多上下文：这个需求的验收标准是什么？"
            usage = {"prompt_tokens": 60000, "completion_tokens": 20000}
            self._json(200, self._completion(content, usage, tools, has_tool_call))
            return

        if tools and not has_tool_call:
            # 首轮：返回 web_search 工具调用（ReAct 第一轮）。
            # 标准 OpenAI 协议：arguments 为 JSON 字符串。
            content = ""
            tool_calls = [
                {
                    "id": "call_mock_1",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": json.dumps({"query": last_user[:50] or "default query"}, ensure_ascii=False),
                    },
                }
            ]
            usage = {"prompt_tokens": 60000, "completion_tokens": 10000}
            self._json(200, self._completion(content, usage, tools, has_tool_call, tool_calls))
            return

        # 最终轮：直接返回答案
        content = f"【mock 完成】已处理需求：{last_user[:40] or '（空）'}。模拟执行结果：分析完成，产出报告摘要 3 行。"
        usage = {"prompt_tokens": 60000, "completion_tokens": 20000}
        self._json(200, self._completion(content, usage, tools, has_tool_call))

    def _completion(self, content, usage, tools, has_tool_call, tool_calls=None):
        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 1700000000,
            "model": MODEL,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": usage,
        }

    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "4000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"mock-llm listening on :{port} mode={MODE}")
    server.serve_forever()
