"""Bounded real-Docker storage/log regression; never fills the host disk."""
from __future__ import annotations

import argparse
import time
import uuid

from sandbox_runtime import docker_client, report

DISK_CODE = '''
import errno, pathlib
size = 0
p = pathlib.Path('/workspace/output/fill.bin')
try:
    with p.open('wb', buffering=0) as f:
        for _ in range(24):
            size += f.write(b'x' * 1048576)
except OSError as exc:
    assert exc.errno == errno.ENOSPC, exc
else:
    raise AssertionError('missing kernel quota')
assert 0 < size <= 16 * 1048576, size
p.unlink()
pathlib.Path('/tmp/alias').write_text('shared')
assert pathlib.Path('/workspace/alias').read_text() == 'shared'
try:
    pathlib.Path('/code/main.py').write_text('changed')
except OSError as exc:
    assert exc.errno in (errno.EROFS, errno.EACCES), exc
else:
    raise AssertionError('script volume is writable')
pathlib.Path('/workspace/output/kept.txt').write_text('retained-after-exit')
print('kernel-quota-and-alias-ok', size)
'''

OUTPUT_CODE = '''
import os
for _ in range(48):
    os.write(1, b'x' * 65536)
os.write(1, b'TAIL_STDOUT')
for _ in range(32):
    os.write(2, b'y' * 65536)
os.write(2, b'TAIL_STDERR')
'''


def main() -> int:
    from app.core.config import settings
    from app.executors.sandbox_client import SandboxRun

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    args = parser.parse_args()
    client = docker_client()
    old = settings.sbx_disk_mb, settings.sbx_stdout_max_bytes
    settings.sbx_disk_mb, settings.sbx_stdout_max_bytes = 16, 4096
    results = []
    try:
        for name, code in [('bounded workspace/output/tmp and readonly script', DISK_CODE),
                           ('live bounded stdout/stderr with exact byte counts', OUTPUT_CODE)]:
            eid = str(uuid.uuid4())
            evidence = {'execution_id': eid}
            seen = {'stdout': 0, 'stderr': 0}
            started = time.monotonic()

            def observe(stream: str, text: str, counters=seen) -> None:
                counters[stream] += len(text.encode('utf-8'))

            passed = False
            try:
                with SandboxRun(execution_id=eid, language='python', code=code,
                                timeout_s=10, client=client, output_callback=observe) as run:
                    outcome = run.execute()
                    artifacts = {f.name: f.data for f in run.iter_output_files()}
                    evidence.update(exit_code=outcome.exit_code,
                                    stdout_bytes=outcome.stdout_full_bytes,
                                    stderr_bytes=outcome.stderr_full_bytes,
                                    stdout_retained=len(outcome.stdout),
                                    stderr_retained=len(outcome.stderr), streamed_bytes=seen)
                    if code == DISK_CODE:
                        passed = (outcome.exit_code == 0
                                  and b'kernel-quota-and-alias-ok' in outcome.stdout
                                  and artifacts.get('kept.txt') == b'retained-after-exit')
                    else:
                        passed = (outcome.exit_code == 0
                                  and outcome.stdout_full_bytes == 48 * 65536 + 11
                                  and outcome.stderr_full_bytes == 32 * 65536 + 11
                                  and len(outcome.stdout) == len(outcome.stderr) == 4096
                                  and outcome.stdout.endswith(b'TAIL_STDOUT')
                                  and outcome.stderr.endswith(b'TAIL_STDERR')
                                  and seen == {'stdout': 4096, 'stderr': 4096})
                    if not passed:
                        evidence['stderr_tail'] = outcome.stderr.decode('utf-8', 'replace')
            except Exception as exc:  # noqa: BLE001 - report probe failure after cleanup
                evidence['error'] = f'{type(exc).__name__}: {exc}'
            filters = {'label': f'aw.execution={eid}'}
            residual = {'containers': len(client.containers.list(all=True, filters=filters)),
                        'networks': len(client.networks.list(filters=filters)),
                        'volumes': len(client.volumes.list(filters=filters))}
            evidence.update(residual=residual, total_ms=round((time.monotonic() - started) * 1000))
            results.append({'name': name, 'passed': passed and not any(residual.values()),
                            'runtime': evidence})
    finally:
        settings.sbx_disk_mb, settings.sbx_stdout_max_bytes = old
        client.close()
    return report(results, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
