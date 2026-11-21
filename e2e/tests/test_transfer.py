"""项目所有权移交（017）：UI 入口 + 新 owner 真权限 + 同名冲突消息。

为什么这三条要进常驻套件：
- 移交是会改权限归属的操作，前端入口藏错位置（例如成员也看到按钮）
  在单测里看不出来；
- 「徽章变了」不等于「权限到了」——必须让新 owner 真的做一次 owner 动作；
- 同名冲突的 409 是本特性唯一「告诉用户怎么修」的错误，消息被吃掉
  就等于功能残废（012 的历史教训）。
"""

from __future__ import annotations

import re

from support.ui import error_banner_count


def _project_row(page, name: str):
    return page.locator("li", has_text=name).first


def test_transfer_via_ui_changes_badge(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    """owner 经 UI 移交后，自己的徽章从「我的」变「共享的」（仍保留成员）。"""
    owner_token = accounts["owner"]["token"]
    project = api.create_project(owner_token, f"移交UI-{run_id}")
    added = api.post(
        owner_token,
        f"/projects/{project['id']}/members",
        {"user_id": accounts["mate"]["id"]},
    )
    assert added.status_code == 201, added.text[:200]

    page = page_as("owner")
    page.goto(f"{base_url}/projects", wait_until="networkidle")
    row = _project_row(page, project["name"])
    row.get_by_text("我的").wait_for(timeout=10_000)

    row.get_by_role("button", name="移交").click()
    page.get_by_label("移交给谁").select_option(value=accounts["mate"]["id"])
    page.get_by_role("button", name="确认移交").click()

    # 列表重拉后徽章变「共享的」——同时证明 selected 的是正确目标
    row = _project_row(page, project["name"])
    row.get_by_text("共享的").wait_for(timeout=10_000)
    assert error_banner_count(page) == 0


def test_new_owner_can_rename_after_transfer(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    """徽章变化不算数：新 owner 必须真的能改名（owner 动作）。"""
    owner_token = accounts["owner"]["token"]
    project = api.create_project(owner_token, f"接管后改名-{run_id}")
    api.post(
        owner_token,
        f"/projects/{project['id']}/members",
        {"user_id": accounts["mate"]["id"]},
    )
    transferred = api.post(
        owner_token,
        f"/projects/{project['id']}/transfer",
        {"new_owner_id": accounts["mate"]["id"], "keep_as_member": True},
    )
    assert transferred.status_code == 200, transferred.text[:200]

    renamed = f"接管改名后-{run_id}"
    page = page_as("mate")
    page.goto(f"{base_url}/projects", wait_until="networkidle")
    row = _project_row(page, project["name"])
    row.get_by_text("我的").wait_for(timeout=10_000)

    row.get_by_role("button", name="改名").click()
    edit_row = page.locator("li", has=page.get_by_role("button", name="保存")).first
    edit_row.locator("input").first.fill(renamed)
    edit_row.get_by_role("button", name="保存").click()
    page.get_by_text(renamed).first.wait_for(timeout=10_000)


def test_same_name_conflict_shows_backend_message(
    base_url: str,
    page_as,
    api,
    accounts: dict[str, dict[str, str]],
    run_id: str,
) -> None:
    """接手人已有同名项目 → 409，且消息原样显示（带项目名）。"""
    shared_name = f"撞名-{run_id}"
    owner_token = accounts["owner"]["token"]
    mate_token = accounts["mate"]["token"]

    project = api.create_project(owner_token, shared_name)
    api.post(
        owner_token,
        f"/projects/{project['id']}/members",
        {"user_id": accounts["mate"]["id"]},
    )
    # 接手人名下已有同名项目
    api.create_project(mate_token, shared_name)

    page = page_as("owner")
    page.goto(f"{base_url}/projects", wait_until="networkidle")
    _project_row(page, shared_name).get_by_role("button", name="移交").click()
    page.get_by_label("移交给谁").select_option(value=accounts["mate"]["id"])
    page.get_by_role("button", name="确认移交").click()

    page.get_by_text(re.compile("已有同名项目")).first.wait_for(timeout=10_000)
    # 弹层仍开着（失败不该假装成功关掉）
    assert page.get_by_role("dialog").count() >= 1
