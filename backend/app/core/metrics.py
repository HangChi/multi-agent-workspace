"""极简 Prometheus 指标注册表（零依赖）。

为什么自己写而不引 `prometheus_client`：本项目只暴露十来个指标，而多一个依赖
就要同步 `uv.lock`、重打镜像、跟着上游做安全更新。这里实现 Counter / Gauge /
Histogram 三个类型，输出严格遵循 Prometheus 文本格式（`text/plain; version=0.0.4`），
Prometheus 侧无感知。

设计约束：
- **标签必须显式声明**：写错标签名直接抛错，而不是默默丢一个点（静默丢指标是
  可观测性里最难查的一类问题：面板空着，但没人知道是"没流量"还是"写错了"）。
- **低基数**：HTTP 指标用路由模板（`/runs/{run_id}`）而不是真实路径，否则每个
  run_id 一条时间序列，Prometheus 直接被撑爆。
- **线程安全**：API 是多 worker、Worker 是多进程，同进程内还有线程池
  （`asyncio.to_thread`），所以计数要加锁。
- 不提供 `unregister`：指标是进程级静态声明，测试里用 `reset()` 清值即可。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable

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

    def render(self) -> list[str]:
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

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with _lock:
            items = sorted(self._values.items())
        if not items:
            # 没有样本也要输出一行 0，否则面板上会出现 "no data" 而不是 0
            lines.append(f"{self.name} 0")
            return lines
        for key, value in items:
            lines.append(f"{self.name}{_format_labels(self.labelnames, key)} {value}")
        return lines


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

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        with _lock:
            items = sorted(self._values.items())
        if not items:
            lines.append(f"{self.name} 0")
            return lines
        for key, value in items:
            lines.append(f"{self.name}{_format_labels(self.labelnames, key)} {value}")
        return lines


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

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with _lock:
            keys = sorted(set(self._counts) | set(self._totals))
            snapshot = [
                (
                    key,
                    list(self._counts.get(key, [])),
                    self._sums[key],
                    self._totals[key],
                )
                for key in keys
            ]
        for key, counts, total_sum, total_count in snapshot:
            for bound, count in zip(self.buckets, counts, strict=True):
                labels = dict(zip(self.labelnames, key, strict=True))
                labels["le"] = _format_float(bound)
                lines.append(f"{self.name}_bucket{_format_labels_dict(labels)} {count}")
            labels = dict(zip(self.labelnames, key, strict=True))
            labels["le"] = "+Inf"
            lines.append(
                f"{self.name}_bucket{_format_labels_dict(labels)} {total_count}"
            )
            lines.append(
                f"{self.name}_sum{_format_labels(self.labelnames, key)} {total_sum}"
            )
            lines.append(
                f"{self.name}_count{_format_labels(self.labelnames, key)} {total_count}"
            )
        if not snapshot:
            # 无样本时至少给出 +Inf 与 0，避免查询返回空
            lines.append(f'{self.name}_bucket{{le="+Inf"}} 0')
            lines.append(f"{self.name}_sum 0")
            lines.append(f"{self.name}_count 0")
        return lines


def _format_float(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _format_labels(labelnames: tuple[str, ...], key: tuple[str, ...]) -> str:
    return _format_labels_dict(dict(zip(labelnames, key, strict=True)))


def _format_labels_dict(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{name}="{_escape(value)}"' for name, value in labels.items()]
    return "{" + ",".join(parts) + "}"


def render() -> str:
    """渲染全部指标（Prometheus 文本格式）。"""
    with _lock:
        metrics = list(_registry.values())
    lines: list[str] = []
    for metric in metrics:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"


def metric_names() -> set[str]:
    """已注册的指标名（告警规则的契约测试用）。"""
    with _lock:
        return set(_registry)


def reset() -> None:
    """清空所有指标的值（仅测试使用；注册关系保留）。"""
    with _lock:
        for metric in _registry.values():
            if isinstance(metric, Counter | Gauge):
                metric._values.clear()
            elif isinstance(metric, Histogram):
                metric._counts.clear()
                metric._sums.clear()
                metric._totals.clear()


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
    "LLM 累计成本（分，未取整的浮点值）",
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
