"""Mock LLM 网关（quickstart S5 验证用，非生产组件）。

OpenAI 兼容 /v1/chat/completions，供 LiteLLMClient 真实调用：
- 无工具调用请求 → 直接返回最终答案（一次完成，SC-004 简单执行）
- 带 tools 的请求 → 首轮返回 web_search 工具调用，二轮返回最终答案（ReAct 两轮闭环）
- 路径 /fail → 始终返回 500（验证失败→重试路径）
- 路径 /needs-input → 返回 __NEEDS_INPUT__ 前缀（验证 needs_input 挂起）
- usage 返回固定 token 数（成本累计验证，SC-006）
- 显式 [sandbox:<scenario>] 标记驱动 002 验收；仅返回固定工具调用，不执行代码。
  场景：python_sum / shell / failure / artifacts / denied / no_tools。
- 显式 [external:<outcome>] 标记驱动 003 外部工具并发验收；从本回合 advertised
  的 mcp_* schema 中选取唯一外部工具并按其 outcome（success/tool_error/timeout）
  调用，不执行任何代码、不绕过 Worker 的真实执行路径。

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
    "denied_threshold": ("run_shell", {"command": "printf 'UNAUTHORIZED_EXECUTION\\n'"}),
    "calculate": ("calculate", {"expression": "(100 * 101 * 201) / 6"}),
    "web_search": ("web_search", {"query": "002 controlled search fixture"}),
    "read_url": ("read_url", {"url": "https://example.com"}),
    "output": ("run_python", {"code": (
        "import os\n"
        "os.write(1, b'X' * (3 * 1024 * 1024) + b'OUTPUT_TAIL\\n')\n"
        "os.write(2, b'\\xff\\xfe' + b'BINARY_STDERR\\n')\n"
    )}),
    "artifact_files": ("run_python", {"code": (
        "from pathlib import Path\n"
        "for n in range(60):\n"
        " Path(f'/workspace/output/file-{n:02}.txt').write_text(str(n))\n"
        "print('artifact-files-written')\n"
    )}),
    "artifact_file_size": ("run_python", {"code": (
        "from pathlib import Path\n"
        "Path('/workspace/output/large.bin').write_bytes(b'A' * (21 * 1024 * 1024))\n"
        "print('artifact-file-size-written')\n"
    )}),
    "artifact_total_size": ("run_python", {"code": (
        "from pathlib import Path\n"
        "for n in range(3):\n"
        " Path(f'/workspace/output/total-{n}.bin').write_bytes(b'T' * (18 * 1024 * 1024))\n"
        "print('artifact-total-written')\n"
    )}),
    "artifact_duplicate": ("run_python", {"code": (
        "from pathlib import Path\n"
        "for n in (1, 2):\n"
        " p=Path(f'/workspace/output/dir{n}/same.bin'); p.parent.mkdir()\n"
        " p.write_bytes(bytes([n]) * 128)\n"
        "print('artifact-duplicates-written')\n"
    )}),
    "timeout": ("run_python", {"code": "import time\ntime.sleep(30)", "timeout_s": 2}),
    "oom": ("run_python", {"code": (
        "import time\nblocks=[]\n"
        "for _ in range(96):\n blocks.append(bytearray(4 * 1024 * 1024)); time.sleep(.01)\n"
    )}),
    "disk": ("run_python", {"code": (
        "with open('/workspace/output/full.bin','wb') as stream:\n"
        " for _ in range(128): stream.write(b'X' * (1024 * 1024))\n"
    )}),
    "pids": ("run_python", {"code": (
        "import subprocess\nchildren=[]\n"
        "for _ in range(256): children.append(subprocess.Popen(['sleep','30']))\n"
    )}),
    "policy": ("run_python", {"code": (
        "import socket\ns=socket.socket(); s.settimeout(.3)\n"
        "s.connect(('169.254.169.254',80))\n"
    )}),
    "egress_domain": ("run_python", {"code": (
        "import urllib.request\n"
        "with urllib.request.urlopen('https://pypi.org/simple/pip/',timeout=10) as response:\n"
        " assert response.status == 200; assert response.read(64)\n"
        "print('domain-allowed')\n"
    )}),
    "egress_domain_denied": ("run_python", {"code": (
        "import urllib.request\n"
        "urllib.request.urlopen('https://example.com',timeout=5).read(64)\n"
    )}),
    "infra": ("run_python", {"code": "print('MUST_NOT_EXECUTE_INFRA')"}),
    "sleep": ("run_python", {"code": "import time\ntime.sleep(3)\nprint('sleep-completed')"}),
    "quota": ("run_python", {"code": "import time\ntime.sleep(65)\nprint('quota-completed')"}),
    "cancel": ("run_python", {"code": "import time\nprint('cancel-started', flush=True)\ntime.sleep(60)"}),
    "ws": ("run_python", {"code": "print('ws-output-evidence', flush=True)"}),
}
SANDBOX_MARKER = re.compile(r"\[sandbox:([a-z_]+)\]")
# 003 acceptance: the Agent grants exactly one external tool, so the model turn
# is derived from the single advertised mcp_* schema instead of a hardcoded name.
EXTERNAL_MARKER = re.compile(r"\[external:([a-z_]+)\]")
EXTERNAL_OUTCOMES = ("success", "tool_error", "timeout")
EXTERNAL_ARGUMENTS = {
    # The verify stack runs MCP_CALL_TIMEOUT_SECONDS=10; this call must exceed it.
    "success": {"query": "003 deterministic fixture"},
    "tool_error": {},
    "timeout": {"delay_ms": 20000},
}


def external_reply(messages, tools, last_user):
    """Return fixed 003 external-tool responses, or None for other scenarios."""
    marker = EXTERNAL_MARKER.search(last_user)
    if marker is None:
        return None
    scenario = marker.group(1)
    if scenario not in EXTERNAL_OUTCOMES:
        raise ValueError("unknown external scenario")
    tool_messages = [message for message in messages if message.get("role") == "tool"]
    advertised = [tool.get("function", {}).get("name", "") for tool in tools]
    external = [name for name in advertised if name.startswith("mcp_")]
    if not tool_messages:
        if len(external) != 1:
            return "EXTERNAL_SCENARIO_ERROR: expected exactly one external tool grant", None
        return "", [{
            "id": f"call_external_{scenario}_1",
            "type": "function",
            "function": {"name": external[0], "arguments": json.dumps(EXTERNAL_ARGUMENTS[scenario])},
        }]
    # Only the success path reaches a second model turn; a classified failure
    # terminates the run inside the orchestrator.
    if scenario != "success":
        return "EXTERNAL_SCENARIO_ERROR: failure scenario produced another model turn", None
    try:
        output = json.loads(tool_messages[-1].get("content", ""))
    except (TypeError, ValueError):
        return "EXTERNAL_SCENARIO_ERROR: invalid tool result", None
    if not isinstance(output, dict):
        return "EXTERNAL_SCENARIO_ERROR: invalid tool result", None
    text = "".join(item.get("text", "") for item in output.get("content") or []
                   if isinstance(item, dict))
    if output.get("isError") is not False or '"ok": true' not in text:
        return "EXTERNAL_SCENARIO_ERROR: external call did not return a successful result", None
    return f"EXTERNAL_SUCCESS_OK: {text[-120:]}", None



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
    if scenario not in {"denied", "denied_threshold"} and advertised != {name}:
        return "SANDBOX_SCENARIO_ERROR: grants differ from requested singleton", None
    if not tool_messages or scenario == "denied_threshold":
        if scenario in {"ws", "cancel", "calculate", "web_search", "read_url", "denied_threshold",
                        "timeout", "oom", "pids", "disk", "policy", "egress_domain_denied", "infra", "quota"} and not tool_messages:
            # Run assignment precedes WS connect. Delay only the first response
            # for observed scenarios until the real subscription can establish.
            # S6 sleep workload/model timing is deliberately unchanged.
            import time
            time.sleep(1)
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
    if scenario == "calculate":
        confirmed = output.get("result") == 338350
    elif scenario == "web_search":
        confirmed = any(item.get("title") == "002 controlled search result"
                        for item in output.get("results", []))
    elif scenario == "read_url":
        confirmed = output.get("status") == 200 and "Example Domain" in output.get("text", "")
    elif scenario in {"timeout", "oom", "pids", "disk", "policy", "infra"}:
        confirmed = output.get("ok") is False and output.get("error_category") == scenario
    elif scenario == "egress_domain_denied":
        confirmed = output.get("ok") is False and output.get("error_category") == "policy"
    elif scenario == "quota" and output.get("error_category") == "resource_busy":
        confirmed = output.get("ok") is False
    else:
        confirmed = output.get("ok") is True and output.get("exit_code") == 0
    if not confirmed:
        return "SANDBOX_SCENARIO_ERROR: tool execution unsuccessful", None
    # Keep the final answer bounded even when stdout deliberately exceeds 3 MiB.
    observed = str(output.get('stdout', output.get('result', ''))) [-256:].strip()
    return f"SANDBOX_{scenario.upper()}_OK: {observed}", None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        pass

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path.split("?", 1)[0] == "/search":
            self._json(200, {"results": [{"title": "002 controlled search result",
                                         "url": "https://example.com", "snippet": "fixture response"}]})
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
        except (ValueError, TypeError, OSError):
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

        try:
            external_scenario_reply = external_reply(messages, tools, last_user)
        except ValueError:
            self._json(400, {"error": "unknown external scenario"})
            return
        if external_scenario_reply is not None:
            content, tool_calls = external_scenario_reply
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
