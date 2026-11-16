"""Fixed, bounded argv-only transport for an execution's kernel operations."""
import base64
import errno
import json
import re
import subprocess
import sys
import time
import uuid

_FAILURE_CODES = {
    "permission_denied", "address_in_use", "address_unavailable", "missing_executable",
    "invalid_argument", "no_space_left", "operation_timeout", "nft_syntax",
    "nft_feature_unavailable", "nft_transaction_failed",
}


def _failure_code(failure: BaseException) -> str:
    if isinstance(failure, (TimeoutError, subprocess.TimeoutExpired)):
        return "operation_timeout"
    if isinstance(failure, FileNotFoundError):
        return "missing_executable"
    if isinstance(failure, OSError):
        return {
            errno.EPERM: "permission_denied", errno.EACCES: "permission_denied",
            errno.EADDRINUSE: "address_in_use", errno.EADDRNOTAVAIL: "address_unavailable",
            errno.EINVAL: "invalid_argument", errno.ENOSPC: "no_space_left",
        }.get(failure.errno, "nft_transaction_failed")
    text = getattr(failure, "stderr", b"")
    if isinstance(text, bytes):
        text = text[:8192].decode("utf-8", "replace")
    if isinstance(text, str):
        text = text.lower()
        for markers, code in (
            (("operation not permitted", "permission denied"), "permission_denied"),
            (("address already in use",), "address_in_use"),
            (("cannot assign requested address",), "address_unavailable"),
            (("syntax error",), "nft_syntax"),
            (("operation not supported", "not supported", "no such file or directory"),
             "nft_feature_unavailable"),
            (("invalid argument",), "invalid_argument"),
            (("no space left",), "no_space_left"),
        ):
            if any(marker in text for marker in markers):
                return code
    return "nft_transaction_failed"


def _report_failure(data: dict, stage: str, failure: BaseException) -> None:
    try:
        execution_id = str(uuid.UUID(data["execution_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return
    code = _failure_code(failure)
    if code not in _FAILURE_CODES:
        code = "nft_transaction_failed"
    print(json.dumps({"aw_net_failure": 1, "execution_id": execution_id,
                      "stage": stage, "code": code}, separators=(",", ":")),
          file=sys.stderr, flush=True)


def _run_checked(argv: list[str], *, input_data: bytes | None, timeout: float,
                 data: dict, stage: str, report: bool = True) -> None:
    try:
        subprocess.run(argv, input=input_data, capture_output=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        if report:
            _report_failure(data, stage, exc)
        raise


def _install_transactions(commands: list[list[str]]) -> list[tuple[list[str], bytes | None]]:
    """Batch only simple filter installs; cleanup/other argv retain their semantics.

    No shell quoting: restore accepts only whitespace-free tokens in this subset.
    --noflush preserves every unrelated chain; each family commits atomically.
    """
    if not commands or any(
        len(command) < 3 or command[0] not in ("iptables", "ip6tables")
        or command[1] not in ("-N", "-A", "-I")
        or (command[1] == "-N" and len(command) != 3)
        or any(re.fullmatch(r"[A-Za-z0-9_./:!,+=-]+", token) is None for token in command)
        for command in commands
    ):
        return [(command, None) for command in commands]
    operations = []
    for family in ("iptables", "ip6tables"):
        selected = [command for command in commands if command[0] == family]
        if not selected:
            continue
        declarations = [f":{command[2]} - [0:0]" for command in selected if command[1] == "-N"]
        rules = [" ".join(command[1:]) for command in selected if command[1] != "-N"]
        script = "\n".join(["*filter", *declarations, *rules, "COMMIT", ""])
        operations.append(([family + "-restore", "--noflush", "--wait", "2"], script.encode()))
    return operations


def main() -> None:
    if len(sys.argv) != 2 or len(sys.argv[1]) > 524288:
        raise ValueError("invalid network batch")
    data = json.loads(base64.b64decode(sys.argv[1], validate=True))
    if not isinstance(data, dict) or not isinstance(data.get("execution_id"), str):
        raise TypeError("invalid network batch identity")
    commands = data["commands"]
    if not isinstance(commands, list) or len(commands) > 2048:
        raise ValueError("network command budget exceeded")
    for command in commands:
        if (not isinstance(command, list) or not command or
                command[0] not in ("iptables", "ip6tables", "nft") or
                len(command) > 64 or any(not isinstance(x, str) or len(x) > 256 for x in command)):
            raise ValueError("invalid network command")
    operations = ([(command, None) for command in commands] if data.get("best_effort", False)
                  else _install_transactions(commands))
    failures = 0
    deadline = time.monotonic() + 8
    script = data.get("nft_script")
    if script is not None:
        if not isinstance(script, str) or len(script) > 65536:
            raise ValueError("invalid nft policy script")
        _run_checked(["nft", "-f", "-"], input_data=script.encode(), timeout=1,
                     data=data, stage="policy_install")
    for command, input_data in operations:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("network batch deadline exceeded")
        try:
            _run_checked(command, input_data=input_data, timeout=min(2, remaining),
                         data=data, stage="policy_install", report=False)
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            if not data.get("best_effort", False):
                _report_failure(data, "policy_install", exc)
                raise
            failures += 1
    print(json.dumps({"commands": len(commands), "failures": failures}))


if __name__ == "__main__":
    main()
