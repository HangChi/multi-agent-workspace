"""定时调度 schema 的形状校验测试（T013）。

只测「形状」——长度、非空白、枚举、null 语义。cron 与时区的语义校验
在 service 层（第 3/4 步），不在 schema 里，这里刻意不测。

两类断言值得特别说明：

1. **PATCH 的 null 语义**：`null` 表示「不改」，首次运行后每次 PATCH 都必须
   靠 `exclude_unset` 判断用户到底传了什么。如果 schema 用默认值填充，
   `model_dump()` 会把用户没传的字段也带上，PATCH 就变成了「把没传的字段
   全清空」——这类 bug 在接口测试里很容易漏，因为它只在部分更新时显现。

2. **`description` 空字符串 = 清空**：这是与 null 相对的另一半语义。
   两者必须同时成立，否则用户无法把已填的描述改回空。
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.schedule import (
    CronPreviewOut,
    CronPreviewIn,
    ScheduleCreate,
    ScheduleTarget,
    ScheduleTargetType,
    ScheduleTemplate,
    ScheduleUpdate,
    ScheduleTemplatePatch,
    TriggerCounts,
    TargetStatus,
)


def _create(**over) -> ScheduleCreate:
    base = dict(
        name="每日晨报",
        cron_expr="0 9 * * 1-5",
        template={"title": "每日进度汇总"},
        target={"type": "agent", "id": "a1"},
    )
    base.update(over)
    return ScheduleCreate(**base)


class TestScheduleCreate:
    def test_minimal_payload_defaults(self):
        s = _create()
        # 时区默认上海：本项目全部用户在国内，默认值必须可用而不是留空
        assert s.timezone == "Asia/Shanghai"
        assert s.enabled is True
        assert s.template.priority.value == "normal"
        assert s.target.strategy is None

    def test_blank_name_is_rejected(self):
        with pytest.raises(ValidationError) as ei:
            _create(name="   ")
        assert "规则名称不能为空" in str(ei.value)

    def test_blank_cron_is_rejected(self):
        with pytest.raises(ValidationError) as ei:
            _create(cron_expr="  ")
        assert "周期表达式不能为空" in str(ei.value)

    def test_blank_template_title_is_rejected(self):
        with pytest.raises(ValidationError) as ei:
            _create(template={"title": " "})
        assert "模板标题不能为空" in str(ei.value)

    def test_unknown_target_type_is_rejected(self):
        with pytest.raises(ValidationError):
            _create(target={"type": "user", "id": "u1"})

    def test_unknown_priority_is_rejected(self):
        with pytest.raises(ValidationError):
            _create(template={"title": "x", "priority": "urgent"})

    def test_invalid_strategy_pattern_is_rejected(self):
        with pytest.raises(ValidationError):
            _create(target={"type": "group", "id": "g1", "strategy": "round_robin"})

    def test_shape_validation_does_not_touch_cron_semantics(self):
        """形状校验只管长度，不管能不能解析。

        这是契约第 2 步与第 3 步的分界：如果 schema 在这里就拒掉
        `0 0 31 2 *`，那第 3 步「能算出下一时刻」的判据就没机会表达，
        错误字段也会指错（指向整个请求而不是 cron_expr）。
        """
        s = _create(cron_expr="0 0 31 2 *")
        assert s.cron_expr == "0 0 31 2 *"


class TestScheduleUpdate:
    def test_unset_fields_are_absent_from_dump(self):
        """PATCH 的「不改」必须靠「字段没出现」表达，不能靠默认值。"""
        u = ScheduleUpdate(name="改名")
        dump = u.model_dump(exclude_unset=True)
        assert dump == {"name": "改名"}
        # 其余字段不能被填充成 None 混进去
        assert "cron_expr" not in dump
        assert "enabled" not in dump

    def test_changes_helper_equals_exclude_unset(self):
        """service 层只该用 `changes()`。

        直接调 `model_dump()` 会把没传的字段补成 None 一起输出，
        部分更新就变成「清空用户没提到的字段」。这条断言钉住了
        两种写法的差异，改坏 `changes()` 会立刻失败。
        """
        u = ScheduleUpdate(name="改名")
        assert u.changes() == {"name": "改名"}
        assert u.changes() != u.model_dump()
        # 差异的具体来源：裸 dump 会带上所有未传字段
        assert "cron_expr" in u.model_dump()

    def test_empty_string_description_is_kept_as_clear_signal(self):
        u = ScheduleUpdate(description="")
        dump = u.model_dump(exclude_unset=True)
        assert dump == {"description": ""}

    def test_explicit_null_description_is_also_distinguishable(self):
        u = ScheduleUpdate(description=None)
        dump = u.model_dump(exclude_unset=True)
        # 传了 null 会出现在 dump 里（区别于完全没传）
        assert dump == {"description": None}

    def test_blank_name_in_patch_is_rejected(self):
        with pytest.raises(ValidationError) as ei:
            ScheduleUpdate(name="  ")
        assert "规则名称不能为空" in str(ei.value)

    def test_nested_template_patch_only_carries_given_fields(self):
        u = ScheduleUpdate(template=ScheduleTemplatePatch(priority="high"))
        dump = u.model_dump(exclude_unset=True)
        assert dump == {"template": {"priority": "high"}}

    def test_disable_to_null_cron_is_not_silently_allowed(self):
        with pytest.raises(ValidationError):
            ScheduleUpdate(cron_expr="")


class TestScheduleTargetOut:
    def test_display_fields_default_to_active(self):
        t = ScheduleTarget(type=ScheduleTargetType.agent, id="a1")
        assert t.strategy is None

    def test_missing_status_is_distinct_from_archived(self):
        """`missing` 与 `archived` 不能合并。

        归档可恢复，物理删除不可恢复；合并成一个 null 后界面无法提示
        「目标已不存在，请重建规则」。
        """
        assert TargetStatus.missing != TargetStatus.archived
        assert {s.value for s in TargetStatus} == {"active", "archived", "missing"}


class TestTriggerCounts:
    def test_all_four_results_have_zero_defaults(self):
        c = TriggerCounts()
        assert c.model_dump() == {
            "fired": 0,
            "missed": 0,
            "skipped_busy": 0,
            "failed": 0,
        }


class TestCronPreviewOut:
    def test_parsable_result_stays_as_is(self):
        now = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        o = CronPreviewOut(next_runs=[now])
        assert o.error is None
        assert o.next_runs == [now]

    def test_empty_without_error_becomes_a_hint_not_a_500(self):
        """算不出下次时间时必须带提示，不能返回「成功但空」。

        输出模型抛异常在线上等于 500，而这只是一个应当被友好告知的输入问题。
        """
        o = CronPreviewOut(next_runs=[])
        assert o.error is not None
        assert "无法计算" in o.error

    def test_parse_error_is_passed_through(self):
        o = CronPreviewOut(error="周期表达式不合法")
        assert o.error == "周期表达式不合法"
        assert o.next_runs == []


class TestCronPreviewIn:
    def test_count_is_bounded(self):
        with pytest.raises(ValidationError):
            CronPreviewIn(cron_expr="0 9 * * *", count=0)
        with pytest.raises(ValidationError):
            CronPreviewIn(cron_expr="0 9 * * *", count=11)


class TestScheduleTemplate:
    def test_title_is_stripped(self):
        assert ScheduleTemplate(title="  汇总  ").title == "汇总"

    def test_description_allowed_empty(self):
        assert ScheduleTemplate(title="x", description="").description == ""
