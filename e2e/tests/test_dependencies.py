"""需求依赖（009）：详情弹层的「前置需求」区块与门禁提示。

门禁是**分配时**的前置校验，不是需求状态——所以页面上的信号是
「详情里说清为什么不能分配」，而不是给卡片加一个 blocked 态。
这条用例就是守那句话还在。
"""

from __future__ import annotations

import re

from support.ui import open_request_detail


def test_blocked_hint_in_detail(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    token = accounts["owner"]["token"]
    dependency = api.create_request(token, f"e2e前置-{run_id}")
    dependent = api.create_request(token, f"e2e后续-{run_id}")
    edge = api.add_dependency(token, dependent["id"], dependency["id"])
    assert edge.status_code == 201, edge.text[:200]

    page = page_as("owner")
    page.goto(base_url, wait_until="networkidle")
    page.locator("article", has_text=dependent["title"]).first.wait_for(timeout=10_000)

    open_request_detail(page, dependent["title"], dependent["id"])
    dialog = page.get_by_role("dialog")
    assert dialog.get_by_text("前置需求").first.is_visible()
    assert (
        dialog.get_by_text(re.compile("暂不能分配执行")).first.is_visible()
    ), "前置未完成时详情里应给出阻塞原因"
    assert dialog.get_by_text(dependency["title"]).first.is_visible()


def test_dependency_removal_refreshes(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    """移除前置后弹层立刻刷新（013 修的「增删后不刷新」）。"""
    token = accounts["owner"]["token"]
    dependency = api.create_request(token, f"e2e移除前置-{run_id}")
    dependent = api.create_request(token, f"e2e移除后续-{run_id}")
    assert api.add_dependency(token, dependent["id"], dependency["id"]).status_code == 201

    page = page_as("owner")
    page.goto(base_url, wait_until="networkidle")
    open_request_detail(page, dependent["title"], dependent["id"])
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name=re.compile(f"移除前置 {dependency['title']}")).click()

    confirm = page.get_by_role("dialog").last
    confirm.get_by_role("button", name="移除").click()
    dialog.get_by_text(re.compile("暂不能分配执行")).first.wait_for(
        state="detached", timeout=10_000
    )
