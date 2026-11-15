"""Bounded real-Docker isolation matrix; no scripts execute on the host.

backend/.venv/bin/python deploy/verify/sbx_escape_matrix.py --output /tmp/002-matrix.json
All load is inside one resource-limited sandbox at a time. The disk probe writes
only to bounded tmpfs. The output-specific regression uses a 16 MiB test
quota and at most 18 MiB of writes, never attempts to fill the host disk.
"""
from __future__ import annotations

import argparse

from sandbox_runtime import docker_client, probe, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    client = docker_client()
    from app.core.config import settings

    cases = [
        ("1 host paths/socket invisible", "import os; assert not any(os.path.exists(p) for p in ['/host','/var/run/docker.sock','/proc/1/root/host']); print('isolated')", "isolated"),
        ("2 application credentials absent", "import os; assert not {'DATABASE_URL','JWT_SECRET','MINIO_SECRET_KEY','LLM_API_KEY','REDIS_URL'} & os.environ.keys(); print('no-credentials')", "no-credentials"),
        ("3 metadata connection denied", "import socket; s=socket.socket(); s.settimeout(1); assert s.connect_ex(('169.254.169.254',80)) != 0; print('denied')", "denied"),
        ("4 private connection denied", "import socket; s=socket.socket(); s.settimeout(1); assert s.connect_ex(('10.253.254.2',8000)) != 0; print('denied')", "denied"),
        ("5 bounded process ceiling", '''import errno,subprocess
children=[]
try:
 for i in range(256):
  children.append(subprocess.Popen(['sleep','10']))
 raise AssertionError('pids limit not enforced')
except OSError as exc:
 assert exc.errno==errno.EAGAIN
 print('pids-enforced',len(children))
finally:
 for child in children: child.terminate()
 for child in children: child.wait()
''', "pids-enforced"),
        ("6 bounded allocation is OOM-killed", f"x=bytearray({(settings.sbx_mem_mb + 128) * 1024 * 1024})", None),
        ("7 workspace tmpfs disk ceiling", f'''import errno
chunk=b'x'*(1024*1024)
written=0
try:
 with open('/workspace/limit.bin','wb',buffering=0) as file:
  for i in range({settings.sbx_disk_mb + 2}):
   written+=file.write(chunk)
 raise AssertionError('disk limit not enforced')
except OSError as exc:
 assert exc.errno==errno.ENOSPC
 assert written <= {settings.sbx_disk_mb * 1024 * 1024}
 print('disk-enforced',written)
''', "disk-enforced"),
        ("8 traversal cannot write rootfs", '''import errno
try:
 open('/workspace/../../sandbox-escape-probe','w').write('x')
 raise AssertionError('rootfs write succeeded')
except OSError as exc:
 assert exc.errno in (errno.EROFS,errno.EACCES)
 print('rootfs-protected')
''', "rootfs-protected"),
        ("9 no root/capabilities/privilege gain", '''import os
assert os.getuid()==10001
status=open('/proc/self/status').read()
assert 'CapEff:\t0000000000000000' in status
assert 'NoNewPrivs:\t1' in status
try:
 os.setuid(0)
 raise AssertionError('setuid succeeded')
except PermissionError: print('no-privilege-gain')
''', "no-privilege-gain"),
        ("10 detached child is reclaimed", "import subprocess; subprocess.Popen(['sleep','60'],start_new_session=True); print('child-started')", "child-started"),
    ]
    results = []
    try:
        for name, code, marker in cases:
            outcome = probe(client, code, timeout_s=10)
            passed = (outcome.get("exit_code") == 0 and marker in outcome.get("stdout", "")) if marker else (outcome.get("status") == "resource" and outcome.get("error_category") == "oom")
            if name.startswith(("3 ", "4 ")):
                passed = passed and any(r["action"] == "deny" for r in outcome.get("egress", []))
            passed = passed and not any(outcome["residual"].values())
            results.append({"name": name, "passed": passed, "runtime": outcome})
        for name, kwargs, expected in [
            ("timeout kills within 3s tolerance", {"timeout_s": 2}, "timeout"),
            ("cancellation stops running container", {"timeout_s": 20, "abort_check": lambda: True}, "cancelled"),
        ]:
            outcome = probe(client, "import time; time.sleep(30)", **kwargs)
            passed = outcome.get("error_category") == expected and outcome.get("duration_ms", 999999) <= 5000 and not any(outcome["residual"].values())
            results.append({"name": name, "passed": passed, "runtime": outcome})
        original_disk = settings.sbx_disk_mb
        settings.sbx_disk_mb = 16
        try:
            outcome = probe(client, '''import errno,pathlib
p=pathlib.Path('/workspace/output/limit.bin')
written=0
try:
 with p.open('wb',buffering=0) as f:
  for _ in range(18): written+=f.write(b'x'*1048576)
 raise AssertionError('output quota missing')
except OSError as exc:
 assert exc.errno==errno.ENOSPC
 assert 0 < written <= 16*1048576
finally:
 p.unlink(missing_ok=True)
pathlib.Path('/workspace/output/quota.txt').write_text('bounded')
print('output-quota-enforced',written)
''', timeout_s=10)
        finally:
            settings.sbx_disk_mb = original_disk
        passed = (outcome.get("exit_code") == 0
                  and "output-quota-enforced" in outcome.get("stdout", "")
                  and any(f["name"] == "quota.txt" for f in outcome.get("artifacts", []))
                  and not any(outcome["residual"].values()))
        results.append({"name": "output volume kernel disk quota",
                        "passed": passed, "runtime": outcome})
        return report(results, args.output)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
