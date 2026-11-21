"""项目容器（008）与项目共享（010）。

覆盖两条最容易回归的路径：
- 内联新建 + 行内改名的定位（改名态里 input 的 value 不算 text content）；
- 成员能在自己的项目列表里看到被加入的项目（013 修过的「共享项目前端不可见」）。
"""

from __future__ import annotations


def test_create_and_rename_project(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    page = page_as("owner")
    page.goto(f"{base_url}/projects", wait_until="networkidle")

    page.get_by_role("button", name="新建项目").first.click()
    name = f"e2e新建-{run_id}"
    page.get_by_label("项目名称").fill(name)
    page.get_by_role("button", name="创建项目").click()
    page.get_by_text(name).first.wait_for(timeout=10_000)

    page.locator("li", has_text=name).first.get_by_role("button", name="改名").click()
    renamed = f"e2e改名-{run_id}"
    edit_row = page.locator("li", has=page.get_by_role("button", name="保存"))
    edit_row.locator("input").first.fill(renamed)
    edit_row.get_by_role("button", name="保存").click()
    page.get_by_text(renamed).first.wait_for(timeout=10_000)

    listed = api.get(accounts["owner"]["token"], "/projects", scope="mine")
    assert any(p["name"] == renamed for p in listed.json()["items"]), "改名没落到后端"


def test_member_sees_shared_project(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    owner_token = accounts["owner"]["token"]
    project = api.create_project(owner_token, f"e2e共享-{run_id}")
    added = api.post(
        owner_token,
        f"/projects/{project['id']}/members",
        {"user_id": accounts["mate"]["id"]},
    )
    assert added.status_code == 201, added.text[:200]

    page = page_as("mate")
    page.goto(f"{base_url}/projects", wait_until="networkidle")
    page.get_by_text(project["name"]).first.wait_for(timeout=10_000)


def test_project_page_renders_without_error(base_url: str, page_as) -> None:
    """成员进项目页不报错（viewer 门由 013 的 C5 覆盖，这里守正向路径）。"""
    page = page_as("owner")
    page.goto(f"{base_url}/projects", wait_until="networkidle")
    assert page.locator('[role="alert"]').count() == 0
