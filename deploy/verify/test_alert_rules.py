"""告警规则与代码指标的契约校验。

为什么需要这条门禁：告警表达式里写错一个指标名，Prometheus 不会报错 ——
它只是永远不触发。结果是「配了告警」的假象，出事时一条都不响。
`promtool check rules` 只校验语法，不校验指标是否存在，所以必须在这里自己比。

同时校验几条最低要求，避免规则变成没人能接的哑告警：
- 每条规则必须有 `severity` 标签（接收端路由依赖它）
- 每条规则必须有 `for`（没有 for 的规则会被瞬时抖动反复触发）
- 每条规则必须有 summary（否则告警短信是一串裸表达式）
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "deploy" / "observability" / "alerts.yml"

# Prometheus 自带指标（不是应用暴露的）
BUILTIN_METRICS = {"up", "ALERTS", "ALERTS_FOR_STATE"}

# 直方图/计数器在表达式里会带这些后缀，比对时去掉
_SUFFIXES = ("_bucket", "_sum", "_count", "_total")


def _normalize(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def app_metric_names() -> set[str]:
    """从后端代码取已注册的指标名（需要能 import app）。"""
    from app.core import metrics

    return {_normalize(name) for name in metrics.metric_names()}


class AlertRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        with ALERTS.open(encoding="utf-8") as handle:
            self.config = yaml.safe_load(handle)
        self.rules = [
            rule
            for group in self.config.get("groups", [])
            for rule in group.get("rules", [])
            if "alert" in rule
        ]

    def test_rules_exist_and_are_grouped(self) -> None:
        self.assertTrue(self.config.get("groups"), "alerts.yml 没有 groups")
        self.assertGreaterEqual(len(self.rules), 5, "告警规则少于 5 条，覆盖不足")

    def test_every_metric_reference_is_exposed_by_the_app(self) -> None:
        exposed = app_metric_names()
        missing: dict[str, str] = {}
        for rule in self.rules:
            expression = str(rule.get("expr", ""))
            for name in re.findall(r"\baw_[a-z0-9_]+\b", expression):
                normalized = _normalize(name)
                if normalized not in exposed:
                    missing[name] = rule["alert"]
        self.assertFalse(
            missing,
            f"规则引用了应用未暴露的指标（改名忘了同步？）: {missing}；已暴露: {sorted(exposed)}",
        )

    def test_no_rule_references_unknown_builtin_only(self) -> None:
        """占位检查：确认解析逻辑确实读到了指标名，而不是正则漏了。"""
        referenced = {
            _normalize(name)
            for rule in self.rules
            for name in re.findall(r"\baw_[a-z0-9_]+\b", str(rule.get("expr", "")))
        }
        self.assertGreaterEqual(len(referenced), 4, f"只解析到 {referenced}")

    def test_rules_have_severity_and_for(self) -> None:
        for rule in self.rules:
            with self.subTest(alert=rule["alert"]):
                self.assertIn("severity", rule.get("labels", {}))
                self.assertIn(rule["labels"]["severity"], {"warning", "critical"})
                self.assertIn("for", rule)
                self.assertIn("summary", rule.get("annotations", {}))

    def test_every_metric_family_appears_in_some_rule(self) -> None:
        """反向覆盖：暴露了却没人告警的指标，要显式确认是有意为之。"""
        # 这些指标刻意不告警：gauges 用于面板展示，或本身不构成故障
        intentionally_unwatched = {
            "aw_ws_connections_active",
            "aw_llm_call_duration_seconds",
            "aw_sandbox_execution_duration_seconds",
            # 007 的活动量计数器：只做面板与占比的分母（分派动作分布、
            # 通知类型分布），绝对量涨跌不构成故障——不派单不告警，
            # 派单失败才告警，而那条路径走的是 aw_run_failure_rate 与
            # API 5xx，不在这两个 counter 上。
            "aw_request_dispatch_actions",
            "aw_notifications_created",
        }
        referenced = {
            _normalize(name)
            for rule in self.rules
            for name in re.findall(r"\baw_[a-z0-9_]+\b", str(rule.get("expr", "")))
        }
        unwatched = app_metric_names() - referenced - intentionally_unwatched
        self.assertFalse(
            unwatched,
            f"以下指标暴露了但没有任何告警，也没有登记为刻意不告警: {sorted(unwatched)}",
        )


if __name__ == "__main__":
    unittest.main()
