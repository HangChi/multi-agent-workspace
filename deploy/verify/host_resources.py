"""Low-overhead numeric native-host evidence, sampled once per second in S6."""
from __future__ import annotations

import math
import time
from pathlib import Path


def snapshot(root: Path = Path('/proc')) -> dict:
    try:
        cpu = (root / 'stat').read_text().splitlines()[0].split()
        if cpu[0] != 'cpu' or len(cpu) < 9:
            raise ValueError
        # guest/guest_nice are already included in user/nice on Linux.
        ticks = [int(value) for value in cpu[1:9]]
        available = next(line.split()[1] for line in (root / 'meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        pressure = next(line for line in (root / 'pressure' / 'cpu').read_text().splitlines()
                        if line.startswith('some '))
        avg10 = float(next(field.split('=', 1)[1] for field in pressure.split()
                           if field.startswith('avg10=')))
        available_kb = int(available)
        if min(ticks) < 0 or available_kb < 0 or not math.isfinite(avg10) or not 0 <= avg10 <= 100:
            raise ValueError
    except (ValueError, IndexError, StopIteration):
        raise ValueError('host resource counters invalid') from None
    return {'at_ms': round(time.monotonic() * 1000), 'cpu_total_ticks': sum(ticks),
            'cpu_idle_ticks': ticks[3] + ticks[4], 'memory_available_kb': available_kb,
            'cpu_pressure_some_avg10': avg10}
