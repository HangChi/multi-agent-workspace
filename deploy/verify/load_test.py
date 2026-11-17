"""并发压测：在真实栈上量 P95、错误率与吞吐。

为什么单独写而不是用 ab/wrk：本项目的鉴权是 Bearer + JSON，且需要区分
「不碰数据库的存活探针」与「同时探 DB/Redis 的就绪探针」——后者才是真正
会被连接池卡住的路径。用 httpx 异步客户端能精确控制并发数与目标。

用法：
    # 100 并发打就绪探针（会穿透到 DB/Redis，最贴近真实负载）
    python deploy/verify/load_test.py --url http://127.0.0.1:8080/api/v1/health/ready \
        --concurrency 100 --requests 3000

    # 带鉴权打业务接口
    python deploy/verify/load_test.py --url http://127.0.0.1:8080/api/v1/requests \
        --token "$AW_TOKEN" --concurrency 50 --requests 2000

判定：错误率 > --max-error-rate 或 P95 > --max-p95-ms 时非零退出。
默认阈值只是「明显不可用」的兜底，不是性能目标。
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx


async def worker(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    counter: dict[str, int],
    latencies: list[float],
    errors: list[str],
    lock: asyncio.Lock,
) -> None:
    while True:
        async with lock:
            if counter["done"] >= counter["total"]:
                return
            counter["done"] += 1
        started = time.perf_counter()
        try:
            response = await client.get(url, headers=headers)
            if response.status_code >= 400:
                errors.append(f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - 压测要把失败分类统计
            errors.append(type(exc).__name__)
        finally:
            latencies.append(time.perf_counter() - started)


async def run(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    counter = {"done": 0, "total": args.requests}
    latencies: list[float] = []
    errors: list[str] = []
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=args.timeout, limits=limits, follow_redirects=False
    ) as client:
        # 预热：排除首个连接的 TLS/DNS/连接池初始化成本
        for _ in range(min(10, args.concurrency)):
            try:
                await client.get(args.url, headers=headers)
            except Exception:  # noqa: BLE001
                pass

        started = time.perf_counter()
        await asyncio.gather(
            *(
                worker(client, args.url, headers, counter, latencies, errors, lock)
                for _ in range(args.concurrency)
            )
        )
        elapsed = time.perf_counter() - started

    if not latencies:
        print("没有收到任何响应", file=sys.stderr)
        return 2

    latencies_ms = sorted(value * 1000 for value in latencies)
    error_rate = len(errors) / len(latencies_ms)
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95) - 1]
    p99 = latencies_ms[int(len(latencies_ms) * 0.99) - 1]

    print(f"目标        : {args.url}")
    print(f"并发 / 请求 : {args.concurrency} / {len(latencies_ms)}")
    print(f"耗时        : {elapsed:.2f}s    吞吐: {len(latencies_ms) / elapsed:.1f} req/s")
    print(f"延迟 ms     : p50={p50:.1f}  p95={p95:.1f}  p99={p99:.1f}  max={latencies_ms[-1]:.1f}")
    print(f"错误        : {len(errors)} ({error_rate:.2%})")
    if errors:
        kinds: dict[str, int] = {}
        for item in errors:
            kinds[item] = kinds.get(item, 0) + 1
        print(f"错误分布    : {kinds}")

    failed = False
    if error_rate > args.max_error_rate:
        print(
            f"FAIL: 错误率 {error_rate:.2%} > {args.max_error_rate:.2%}", file=sys.stderr
        )
        failed = True
    if p95 > args.max_p95_ms:
        print(f"FAIL: P95 {p95:.1f}ms > {args.max_p95_ms}ms", file=sys.stderr)
        failed = True
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="并发压测（真实 HTTP 栈）")
    parser.add_argument("--url", required=True)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--token", default="", help="Bearer access token（可选）")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
