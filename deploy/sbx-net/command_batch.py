"""Fixed, bounded argv-only transport for an execution's kernel operations."""
import base64
import json
import subprocess
import sys
import time


def main() -> None:
    if len(sys.argv) != 2 or len(sys.argv[1]) > 524288:
        raise ValueError("invalid network batch")
    data = json.loads(base64.b64decode(sys.argv[1], validate=True))
    commands = data["commands"]
    if not isinstance(commands, list) or len(commands) > 2048:
        raise ValueError("network command budget exceeded")
    failures = 0
    deadline = time.monotonic() + 8
    script = data.get("nft_script")
    if script is not None:
        if not isinstance(script, str) or len(script) > 65536:
            raise ValueError("invalid nft policy script")
        subprocess.run(["nft", "-f", "-"], input=script.encode(), capture_output=True, timeout=1, check=True)
    for command in commands:
        if (not isinstance(command, list) or not command or
                command[0] not in ("iptables", "ip6tables", "nft") or
                len(command) > 64 or any(not isinstance(x, str) or len(x) > 256 for x in command)):
            raise ValueError("invalid network command")
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("network batch deadline exceeded")
            subprocess.run(command, capture_output=True, timeout=min(2, remaining), check=True)
        except subprocess.SubprocessError:
            if not data.get("best_effort", False):
                raise
            failures += 1
    print(json.dumps({"commands": len(commands), "failures": failures}))


if __name__ == "__main__":
    main()
