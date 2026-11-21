"""需求创建与项目归属（008/015 后的表单结构）。

守的点：新建需求时「所属项目」下拉**默认不归类**，且候选只有本人可见的项目
（008 的 A2 清单项）。下拉是异步查询，选项未展开时是 hidden——
必须 `state="attached"`，等 visible 会永远超时。
"""

from __future__ import annotations


def test_create_request_in_project(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    token = accounts["owner"]["token"]
    project = api.create_project(token, f"e2e归属项目-{run_id}")

    page = page_as("owner")
    page.goto(base_url, wait_until="networkidle")
    page.get_by_role("button", name="新建需求").click()

    title = f"e2e归属需求-{run_id}"
    page.get_by_label("标题").fill(title)
    select = page.get_by_label("所属项目（可选）")
    assert select.input_value() == "", "所属项目应默认不归类"

    select.locator("option", has_text=project["name"]).first.wait_for(
        state="attached", timeout=15_000
    )
    # 必须用 value 选：option 文本是「名字（需求数）」，label 精确匹配会失败，
    # 而 Playwright 对这种失败报的是「waiting for visible and enabled」——极易误判
    select.select_option(value=project["id"])
    page.get_by_role("button", name="创建需求").click()
    page.get_by_text(title).first.wait_for(timeout=10_000)

    created = api.get(token, "/requests", scope="relevant")
    match = next((r for r in created.json()["items"] if r["title"] == title), None)
    assert match is not None, "新建的需求没出现在列表"
    assert match.get("project_id") == project["id"], "项目归属没落到后端"


def test_project_filter_on_board(
    base_url: str, page_as, api, accounts: dict[str, dict[str, str]], run_id: str
) -> None:
    """看板按项目筛选：URL 带 project 深链后只剩该项目的需求。"""
    token = accounts["owner"]["token"]
    project = api.create_project(token, f"e2e筛选项目-{run_id}")
    inside = api.create_request(token, f"e2e项目内-{run_id}", project["id"])
    outside = api.create_request(token, f"e2e项目外-{run_id}")

    page = page_as("owner")
    page.goto(f"{base_url}/?project={project['id']}", wait_until="networkidle")
    page.get_by_text(inside["title"]).first.wait_for(timeout=10_000)
    assert page.get_by_text(outside["title"]).count() == 0, "筛选后仍出现其它项目的需求"
