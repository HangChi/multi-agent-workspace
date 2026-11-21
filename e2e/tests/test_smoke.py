"""冒烟：登录、主导航、全局无错误横幅。

这一组是「任何前端改动都不该弄坏」的底线。放在最前面跑，
失败时不用往下看业务用例——先修壳。
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser
from support.api import PASSWORD
from support.ui import error_banner_count


def test_login_flow(
    browser: Browser, base_url: str, accounts: dict[str, dict[str, str]]
) -> None:
    """真实 UI 登录：填表单 → 离开登录页 → 顶栏可见 → 无错误提示。"""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = context.new_page()
    page.set_default_timeout(15_000)
    try:
        page.goto(f"{base_url}/login", wait_until="networkidle")
        page.get_by_label("邮箱").fill(accounts["owner"]["email"])
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录").click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)
        # 顶栏在桌面宽度下用 shortLabel（「看板」而非「需求看板」），移动端第二行才是全称
        assert page.get_by_role("banner").get_by_role(
            "link", name="看板", exact=True
        ).first.is_visible()
        assert error_banner_count(page) == 0, "登录后出现错误横幅"
    finally:
        context.close()


@pytest.mark.parametrize(
    ("path", "nav_label"),
    [
        ("/", "看板"),
        ("/dashboard", "仪表盘"),
        ("/agents", "Agent"),
        ("/groups", "组"),
        ("/projects", "项目"),
    ],
)
def test_main_nav_pages_render(base_url: str, page_as, path: str, nav_label: str) -> None:
    """五个主导航页都能打开、导航项高亮可见、且不报错。"""
    page = page_as("owner")
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    assert page.get_by_role("banner").get_by_role(
        "link", name=nav_label, exact=True
    ).first.is_visible()
    assert error_banner_count(page) == 0, f"{path} 出现错误横幅"


def test_settings_pages_render(base_url: str, page_as) -> None:
    """设置二级页（模板 / 调度 / 工具市场）可打开——015 重排后的结构。"""
    page = page_as("owner")
    for path in ("/settings/general", "/settings/templates", "/settings/schedules", "/settings/marketplace"):
        page.goto(f"{base_url}{path}", wait_until="networkidle")
        assert error_banner_count(page) == 0, f"{path} 出现错误横幅"
