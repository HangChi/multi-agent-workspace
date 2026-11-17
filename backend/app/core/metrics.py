"""极简 Prometheus 指标注册表（零依赖，支持多进程合并）。

## 为什么自己写

只暴露十来个指标，引 `prometheus_client` 就要同步 `uv.lock`、重打镜像、跟上游
安全更新。输出严格遵循 Prometheus 文本格式（`text/plain; version=0.0.4`）。

## 为什么需要「多进程合并」

指标天然是**进程内**的：API 起 2 个 uvicorn worker，Celery 又是独立容器。
如果只暴露 API 进程的内存计数：

- 抓一次只看到被访问到的那个 worker 的计数，另一个 worker 的请求凭空消失；
- 真正重要的 LLM 调用数、沙箱执行数、Run 终态数**全在 Worker 进程里**，
  在 API 的 `/metrics` 上永远是 0 —— 典型的「配了告警但永不触发」。

所以：非抓取进程（Celery worker）每 5 秒把快照写进 Redis（`metrics:proc:*`，
TTL 30s），`/metrics` 把本进程快照与其余进程快照按「指标名 + 标签组合」相加。

代价与取舍（如实记录）：
- **最终一致**：远端计数最多滞后一个刷新周期（5s）。告警窗口都是分钟级，无影响。
- 计数器进程重启会归零，相加后总量会「回退」一次。`rate()`/`increase()` 本身
  就处理 counter reset，这里不额外补偿。
- 只在 Redis 可达时合并；Redis 挂了 `/metrics` 仍返回本进程数据，不报错。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# 延迟直方图默认桶（秒）：覆盖 LLM/沙箱这种秒级到十秒级的操作
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

# 远端快照的 Redis key 前缀与 TTL：TTL 必须明显大于刷新周期，
# 否则一次 GC 抖动就会让某个 worker 的计数整段消失。
REMOTE_PREFIX = "metrics:proc:"
REMOTE_TTL_SECONDS = 30
FLUSH_INTERVAL_SECONDS = 5

_lock = threading.Lock()
_registry: dict[str, _Metric] = {}


def _escape(value: str) -> str:
    """标签值转义（Prometheus 文本格式）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels_key(labelnames: Iterable[str], labels: dict[str, str]) -> tuple[str, ...]:
    unknown = set(labels) - set(labelnames)
    if unknown:
        raise ValueError(f"未声明的标签: {sorted(unknown)}")
    missing = set(labelnames) - set(labels)
    if missing:
        raise ValueError(f"缺少标签: {sorted(missing)}")
    return tuple(str(labels[name]) for name in labelnames)


def _format_float(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _format_labels_dict(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{name}="{_escape(value)}"' for name, value in labels.items()]
    return "{" + ",".join(parts) + "}"


class _Metric:
    kind = "untyped"

    def __init__(
        self, name: str, help_text: str, labelnames: Iterable[str] = ()
    ) -> None:
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        with _lock:
            if name in _registry:
                raise ValueError(f"指标重复注册: {name}")
            _registry[name] = self

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError


class Counter(_Metric):
    """单调递增计数器。"""

    kind = "counter"

    def __init__(
        self, name: str, help_text: str, labelnames: Iterable[str] = ()
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple[str, ...], float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(self.labelnames, labels)
        with _lock:
            self._values[key] += amount

    def value(self, **labels: str) -> float:
        return self._values.get(_labels_key(self.labelnames, labels), 0.0)

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            series = {
                json.dumps(
                    dict(zip(self.labelnames, key, strict=True)), ensure_ascii=False
                ): value
                for key, value in self._values.items()
            }
        return {
            "kind": self.kind,
            "help": self.help,
            "labelnames": list(self.labelnames),
            "series": series,
        }


class Gauge(_Metric):
    """可增可减的瞬时值。"""

    kind = "gauge"

    def __init__(
        self, name: str, help_text: str, labelnames: Iterable[str] = ()
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple[str, ...], float] = defaultdict(float)

    def set(self, value: float, **labels: str) -> None:
        key = _labels_key(self.labelnames, labels)
        with _lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(self.labelnames, labels)
        with _lock:
            self._values[key] += amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels: str) -> float:
        return self._values.get(_labels_key(self.labelnames, labels), 0.0)

    snapshot = Counter.snapshot


class Histogram(_Metric):
    """累积直方图（`_bucket` / `_sum` / `_count`）。"""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = defaultdict(float)
        self._totals: dict[tuple[str, ...], int] = defaultdict(int)

    def observe(self, value: float, **labels: str) -> None:
        key = _labels_key(self.labelnames, labels)
        with _lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[index] += 1
            self._sums[key] += value
            self._totals[key] += 1

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            keys = sorted(set(self._counts) | set(self._totals))
            series = {
                json.dumps(
                    dict(zip(self.labelnames, key, strict=True)), ensure_ascii=False
                ): {
                    "counts": list(self._counts.get(key, [0] * len(self.buckets))),
                    "sum": self._sums[key],
                    "count": self._totals[key],
                }
                for key in keys
            }
        return {
            "kind": self.kind,
            "help": self.help,
            "labelnames": list(self.labelnames),
            "buckets": list(self.buckets),
            "series": series,
        }


# ---------------------------------------------------------------------------
# 快照 / 合并 / 渲染
# ---------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """本进程全部指标的可序列化快照。"""
    with _lock:
        metrics = list(_registry.items())
    return {name: metric.snapshot() for name, metric in metrics}


def merge_snapshots(snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """按「指标名 + 标签组合」把多份快照相加。

    直方图逐桶相加、sum/count 相加；Counter/Gauge 直接相加（对连接数、队列
    深度这类量，「各进程当前值之和」正是想要的语义）。
    """
    merged: dict[str, Any] = {}
    for snap in snapshots:
        for name, metric in snap.items():
            target = merged.get(name)
            if target is None:
                target = {
                    "kind": metric["kind"],
                    "help": metric["help"],
                    "labelnames": metric["labelnames"],
                    "buckets": metric.get("buckets"),
                    "series": {},
                }
                merged[name] = target
            series = target["series"]
            for label_key, value in metric["series"].items():
                if metric["kind"] == "histogram":
                    current = series.get(label_key)
                    if current is None:
                        series[label_key] = {
                            "counts": list(value["counts"]),
                            "sum": value["sum"],
                            "count": value["count"],
                        }
                    else:
                        counts = current["counts"]
                        for index, item in enumerate(value["counts"]):
                            if index < len(counts):
                                counts[index] += item
                        current["sum"] += value["sum"]
                        current["count"] += value["count"]
                else:
                    series[label_key] = series.get(label_key, 0.0) + value
    return merged


def render_merged(merged: dict[str, Any]) -> str:
    lines: list[str] = []
    for name in sorted(merged):
        metric = merged[name]
        lines.append(f"# HELP {name} {metric['help']}")
        lines.append(f"# TYPE {name} {metric['kind']}")
        series = metric["series"]
        if not series:
            # 没有样本也要输出 0，否则面板显示 no data 而不是 0
            lines.append(f"{name} 0")
            continue
        for label_key in sorted(series):
            labels = json.loads(label_key)
            value = series[label_key]
            if metric["kind"] == "histogram":
                buckets = metric["buckets"] or []
                for bound, count in zip(buckets, value["counts"], strict=False):
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = _format_float(bound)
                    lines.append(
                        f"{name}_bucket{_format_labels_dict(bucket_labels)} {count}"
                    )
                inf_labels = dict(labels)
                inf_labels["le"] = "+Inf"
                lines.append(
                    f"{name}_bucket{_format_labels_dict(inf_labels)} {value['count']}"
                )
                lines.append(f"{name}_sum{_format_labels_dict(labels)} {value['sum']}")
                lines.append(
                    f"{name}_count{_format_labels_dict(labels)} {value['count']}"
                )
            else:
                lines.append(f"{name}{_format_labels_dict(labels)} {value}")
    return "\n".join(lines) + "\n"


def render() -> str:
    """只渲染本进程（测试，以及 Redis 不可用时的退化路径）。"""
    return render_merged(merge_snapshots([snapshot()]))


def metric_names() -> set[str]:
    with _lock:
        return set(_registry)


def reset() -> None:
    """清空所有指标的值（仅测试使用；注册关系保留）。"""
    with _lock:
        for metric in _registry.values():
            if isinstance(metric, (Counter, Gauge)):
                metric._values.clear()
            elif isinstance(metric, Histogram):
                metric._counts.clear()
                metric._sums.clear()
                metric._totals.clear()


# ---------------------------------------------------------------------------
# 跨进程发布（Celery worker → Redis → API 的 /metrics 合并）
# ---------------------------------------------------------------------------

_PROCESS_ID = f"{socket.gethostname()}:{os.getpid()}"


def process_id() -> str:
    """进程标识（host:pid）。容器重建后 pid 可能复用，配合 TTL 足以区分。"""
    return _PROCESS_ID


def publish(redis_client: Any) -> bool:
    """把本进程快照写进 Redis（同步客户端；失败返回 False 而不抛）。"""
    try:
        redis_client.set(
            f"{REMOTE_PREFIX}{_PROCESS_ID}",
            json.dumps(snapshot(), ensure_ascii=False, separators=(",", ":")),
            ex=REMOTE_TTL_SECONDS,
        )
        return True
    except Exception:  # noqa: BLE001 - 指标发布失败不能影响业务
        return False


def _publisher_loop(client_factory: Any) -> None:
    failures = 0
    while True:
        try:
            ok = publish(client_factory())
        except Exception as exc:  # noqa: BLE001 - 建连失败也算一次
            ok = False
            error = type(exc).__name__
        else:
            error = "publish_false"
        if ok:
            failures = 0
        else:
            failures += 1
            # 每 12 次（约 1 分钟）提示一次：Redis 长时间不可用时既不刷屏也不静默
            if failures % 12 == 1:
                logger.warning("metrics_publish_failed", attempts=failures, error=error)
        time.sleep(FLUSH_INTERVAL_SECONDS)


def start_background_publisher(client_factory: Any) -> threading.Thread:
    """在 Celery worker 进程里起后台线程周期发布快照。

    用线程而不是信号处理器：发布是 I/O，塞进信号处理器会阻塞任务调度。
    """
    thread = threading.Thread(
        target=_publisher_loop,
        args=(client_factory,),
        name="metrics-publisher",
        daemon=True,
    )
    thread.start()
    return thread


async def load_remote_snapshots(redis_client: Any) -> list[dict[str, Any]]:
    """读取其它进程的快照（排除自己，避免重复计数）。"""
    snapshots: list[dict[str, Any]] = []
    try:
        async for key in redis_client.scan_iter(match=f"{REMOTE_PREFIX}*", count=100):
            if key == f"{REMOTE_PREFIX}{_PROCESS_ID}":
                continue
            raw = await redis_client.get(key)
            if not raw:
                continue
            try:
                snapshots.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001 - Redis 抖动时退化为只报本进程
        return []
    return snapshots


# ---------------------------------------------------------------------------
# 指标声明（集中在这里，便于一眼看清暴露了什么、基数有多大）
# ---------------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "aw_http_requests_total",
    "HTTP 请求数（按方法/路由模板/状态码）",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "aw_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ("method", "route"),
)
WS_CONNECTIONS = Gauge(
    "aw_ws_connections_active",
    "当前活跃的 WebSocket 连接数",
)
LLM_CALLS = Counter(
    "aw_llm_calls_total",
    "LLM 调用次数（outcome=ok/error/timeout）",
    ("model", "outcome"),
)
LLM_DURATION = Histogram(
    "aw_llm_call_duration_seconds",
    "LLM 单次调用耗时（秒）",
    ("model",),
)
LLM_COST = Counter(
    "aw_llm_cost_cents_total",
    "LLM 累计成本（分）",
    ("model",),
)
RUN_TERMINAL = Counter(
    "aw_run_terminal_total",
    "进入终态的 Run 数（按终态）",
    ("status",),
)
SANDBOX_EXECUTIONS = Counter(
    "aw_sandbox_executions_total",
    "沙箱执行次数（按终态）",
    ("status",),
)
SANDBOX_DURATION = Histogram(
    "aw_sandbox_execution_duration_seconds",
    "沙箱单次执行耗时（秒）",
    ("language",),
)
CELERY_QUEUE_DEPTH = Gauge(
    "aw_celery_queue_depth",
    "Celery 默认队列积压任务数（抓取时从 broker 读取）",
)
