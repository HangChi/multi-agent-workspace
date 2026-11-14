"""Mock LLM 网关（quickstart S5 验证用，非生产组件）。

OpenAI 兼容 /v1/chat/completions，供 LiteLLMClient 真实调用：
- 无工具调用请求 → 直接返回最终答案（一次完成，SC-004 简单执行）
- 带 tools 的请求 → 首轮返回 web_search 工具调用，二轮返回最终答案（ReAct 两轮闭环）
- 路径 /fail → 始终返回 500（验证失败→重试路径）
- 路径 /needs-input → 返回 __NEEDS_INPUT__ 前缀（验证 needs_input 挂起）
- usage 返回固定 token 数（成本累计验证，SC-006）

仅用于本地验证栈，不打包进生产镜像。
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.getenv("MOCK_LLM_MODEL", "gpt-4o-mini")
MODE = os.getenv("MOCK_LLM_MODE", "auto")  # auto | fail | needs_input


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
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
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
