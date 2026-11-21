"""依赖图谱与自动启动开关（021）的浏览器验收。

两条：
- 详情弹层里能看到「下游链」（拓扑序 + 关键路径徽章）——这是 021 唯一的
  用户可见产出，藏在弹层里最容易「做了但没人看见」；
- 勾上「自动启动」但没选 Agent 时保存按钮不可点（半配置状态不该能提交）。
"""

from __future__ import annotations


def _create_chain(api, token: str, run_id: str) -> dict:
    """A ← B ← C（B 依赖 A，C 依赖 B）。返回三个需求。"""
    a = api.create_request(token, f"e2e链A-{run_id}")
    b = api.create_request(token, f"e2e链B-{run_id}")
    c = api.create_request(token, f"e2e链C-{run_id}")
    for dependent, dependency in ((b, a), (c, b)):
        resp = api.add_dependency(token, dependent["id"], dependency["id"])
        assert resp.status_code == 201, resp.text[:200]
    return {"a": a, "b": b, "c": c}


def test_detail_shows_downstream_chain(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    chain = _create_chain(api, accounts["owner"]["token"], run_id)
    page = page_as("owner")
    page.goto(base_url, wait_until="networkidle")

    # 打开链头 A 的详情
    card = page.locator("article", has_text=chain["a"]["title"]).first
    card.hover()
    card.get_by_role("button", name="详情").click()
    page.get_by_role("dialog").wait_for(timeout=8_000)

    dialog = page.get_by_role("dialog")
    dialog.get_by_text("下游链").first.wait_for(timeout=10_000)
    # 图谱是弹层打开后才拉的（useQuery），要等列表渲染出来再断言。
    # 必须限定在 <ol>（下游链）里：前置/候选下拉的 <option> 也含同名标题，
    # get_by_text 会先命中那个 hidden 的 option。
    chain_list = dialog.locator("ol")
    chain_list.get_by_text(chain["b"]["title"]).first.wait_for(timeout=10_000)
    chain_list.get_by_text(chain["c"]["title"]).first.wait_for(timeout=10_000)
    # 关键路径徽章：整条链都是关键路径
    chain_list.get_by_text("关键路径").first.wait_for(timeout=10_000)


def test_auto_start_requires_agent(
    base_url: str,
    page_as,
    run_id: str,
    api,
    accounts: dict[str, dict[str, str]],
) -> None:
    request = api.create_request(accounts["owner"]["token"], f"e2e自动-{run_id}")
    page = page_as("owner")
    page.goto(base_url, wait_until="networkidle")

    card = page.locator("article", has_text=request["title"]).first
    card.hover()
    card.get_by_role("button", name="详情").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_text("下游链").first.wait_for(timeout=10_000)
    dialog.get_by_role("checkbox").first.check()

    # 勾了自动启动但没选 Agent：保存按钮应禁用（半配置状态不可提交）
    save = dialog.get_by_role("button", name="保存").last
    assert not save.is_enabled()
