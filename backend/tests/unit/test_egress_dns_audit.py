"""DNS 审计事件的解析与校验单测（015 覆盖率补齐）。

`_domain_events` / `_gateway_records` 把沙箱内 dns-proxy 输出的日志解析成
「这次执行解析了哪些域名、命中了哪些拒绝规则」，是 egress 审计的唯一数据源。
它要防的是**日志被污染或截断后仍被当成有效审计**：
- 缺 ready 标记 → 说明审计未完整启动，必须报错而不是返回空列表；
- seq 不连续 / execution_id 不匹配 → 说明串了别人的记录；
- 解析失败（非法 JSON、非 UTF-8）→ 一律转成 dns 诊断错误。

这些分支在真实环境里要造出「坏日志」才能触发，所以直接构造对象喂字符串最省事。
"""

import json

import pytest

from app.executors.egress_network import NetworkCapture, NetworkPolicyError


def _capture(*, logs, execution_id="exec-1", domain_config=None):
    """构造一个只用于解析测试的 NetworkCapture（不碰 Docker）。"""

    class _Gateway:
        def __init__(self, payload):
            self.payload = payload

        def logs(self, *args, **kwargs):
            return self.payload

    return NetworkCapture(
        client=None,
        network=None,
        execution_id=execution_id,
        mode="restricted",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=[],
        dns_proxy=None,
        dns_proxy_ip="172.30.0.2",
        bridge="br-test",
        install_commands=[],
        cleanup_commands=[],
        domain_gateway=_Gateway(logs),
        domain_config=domain_config if domain_config is not None else {},
    )


def _line(payload: dict) -> str:
    return json.dumps(payload)


def test_domain_events_empty_without_gateway():
    cap = _capture(logs="")
    cap.domain_gateway = None
    assert cap._domain_events() == []


def test_domain_events_parses_ready_and_events():
    logs = "\n".join(
        [
            _line({"aw_dns_ready": "exec-1"}),
            _line({"aw_dns": 1, "execution_id": "exec-1", "seq": 1, "name": "a.com"}),
            _line({"aw_dns": 1, "execution_id": "exec-1", "seq": 2, "name": "b.com"}),
        ]
    )
    events = _capture(logs=logs)._domain_events()

    assert [e["name"] for e in events] == ["a.com", "b.com"]


def test_domain_events_ignores_non_json_lines():
    logs = "\n".join(
        [_line({"aw_dns_ready": "exec-1"}), "starting dns-proxy", "[ok] listening"]
    )
    assert _capture(logs=logs)._domain_events() == []


def test_domain_events_accepts_bytes_logs():
    logs = (_line({"aw_dns_ready": "exec-1"})).encode()
    assert _capture(logs=logs)._domain_events() == []


def test_domain_events_requires_ready_marker():
    """没有 ready 说明审计没完整启动：返回空列表会被误读成「没有任何解析」。"""
    logs = _line({"aw_dns": 1, "execution_id": "exec-1", "seq": 1, "name": "a.com"})
    with pytest.raises(NetworkPolicyError) as exc:
        _capture(logs=logs)._domain_events()
    assert "dns_failure" in str(exc.value)


def test_domain_events_rejects_sequence_gap():
    logs = "\n".join(
        [
            _line({"aw_dns_ready": "exec-1"}),
            _line({"aw_dns": 1, "execution_id": "exec-1", "seq": 2, "name": "a.com"}),
        ]
    )
    with pytest.raises(NetworkPolicyError):
        _capture(logs=logs)._domain_events()


def test_domain_events_rejects_foreign_execution():
    """串了别人的执行记录 → 审计不可信，必须报错。"""
    logs = "\n".join(
        [
            _line({"aw_dns_ready": "exec-1"}),
            _line({"aw_dns": 1, "execution_id": "exec-2", "seq": 1, "name": "a.com"}),
        ]
    )
    with pytest.raises(NetworkPolicyError):
        _capture(logs=logs)._domain_events()


def test_domain_events_rejects_invalid_json():
    logs = "\n".join([_line({"aw_dns_ready": "exec-1"}), "{not json"])
    with pytest.raises(NetworkPolicyError):
        _capture(logs=logs)._domain_events()


def test_gateway_records_rejects_oversized_log():
    cap = _capture(logs="x" * 131073)
    with pytest.raises(NetworkPolicyError):
        cap._gateway_records()


def test_gateway_records_skips_bracketed_lines():
    """只认以 `{` 开头的行（dns-proxy 的结构化日志）；数组/普通文本一律跳过。

    注：源码里还有一条「解析出来不是 dict 就报错」的防御分支，但以 `{` 开头的
    行经 json.loads 必然得到 dict，所以它实际不可达——这里不伪造用例去覆盖，
    留作防御。
    """
    cap = _capture(logs="[1,2,3]\nplain text")
    assert cap._gateway_records() == []


def test_gateway_records_reports_dns_failure_event():
    """dns-proxy 自报失败时必须原样映射成 dns_failure[stage,code]。"""
    stage, code = "dns_startup", "permission_denied"
    cap = _capture(
        logs=_line(
            {
                "aw_dns_failure": 1,
                "execution_id": "exec-1",
                "stage": stage,
                "code": code,
            }
        )
    )
    with pytest.raises(NetworkPolicyError) as exc:
        cap._gateway_records()
    assert f"{stage},{code}" in str(exc.value)
