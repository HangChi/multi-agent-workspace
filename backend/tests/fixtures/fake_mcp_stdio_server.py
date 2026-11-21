"""一个最小的 stdio MCP server（测试夹具）。

只实现测试需要的方法：initialize / tools/list / tools/call。
--crash 让它在启动后立刻退出，用来验「server 挂了」的错误路径。
"""

import json
import sys


def respond(msg_id, result):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n"
    )
    sys.stdout.flush()


def main() -> None:
    if "--crash" in sys.argv:
        sys.stderr.write("boom: simulated startup failure\n")
        sys.exit(1)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")

        if method == "initialize":
            respond(
                message.get("id"),
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1.0"},
                },
            )
        elif method == "tools/list":
            respond(
                message.get("id"),
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "回显输入",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                        }
                    ]
                },
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            if arguments.get("text") == "please-fail":
                respond(
                    message.get("id"),
                    {"isError": True, "content": [{"type": "text", "text": "boom"}]},
                )
            else:
                respond(
                    message.get("id"),
                    {
                        "content": [
                            {"type": "text", "text": str(arguments.get("text", ""))}
                        ]
                    },
                )
        # notifications（initialized / cancelled）不回


if __name__ == "__main__":
    main()
