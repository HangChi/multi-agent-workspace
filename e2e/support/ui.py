"""页面操作辅助：把 s12 踩出来的时序知识固化，避免每条用例重新踩一遍。

两个反复出现的坑：
1. **详情弹层是两段式**：先用列表数据秒开，再 fetchRequest 补全
   dependencies/经手链。不等那次 GET 就断言，会读到 `dependencies=null`
   ——011 曾把它误判成「弹层拿不到依赖」的 app bug。
2. **失败的上一条用例会留弹窗**：下一条开始前不关掉，点击全被遮罩吃掉。
"""

from __future__ import annotations

import re

from playwright.sync_api import Page


def close_modals(page: Page) -> None:
    for _ in range(3):
        if page.get_by_role("dialog").count() == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    close = page.get_by_role("button", name="关闭").first
    if close.count():
        close.click()
        page.wait_for_timeout(300)


def open_request_detail(page: Page, title: str, request_id: str | None = None) -> None:
    """点卡片 hover 出现的「详情」，并等详情补全完成。"""
    close_modals(page)
    card = page.locator("article", has_text=title).first
    card.hover()
    card.get_by_role("button", name="详情").click()
    page.get_by_role("dialog").wait_for(timeout=8_000)
    if request_id:
        try:
            page.wait_for_response(
                lambda r: f"/api/v1/requests/{request_id}" in r.url
                and r.request.method == "GET",
                timeout=8_000,
            )
        except Exception:  # noqa: BLE001,S110 - 拉取失败时应用会降级为列表数据
            pass
        page.wait_for_timeout(500)


def open_bell(page: Page) -> None:
    """开铃铛面板，并等面板内容出现（点完不等，后续等待会全部超时）。"""
    bell = page.get_by_role("button", name=re.compile("未读|通知")).first
    for _ in range(2):
        bell.click()
        try:
            page.get_by_role(
                "button", name=re.compile("全部已读|查看全部|通知")
            ).last.wait_for(timeout=3_000)
            return
        except Exception:  # noqa: BLE001 - 面板没开就再点一次
            page.wait_for_timeout(400)


def error_banner_count(page: Page) -> int:
    """可见的 ErrorBanner 数量（role=alert 由统一错误组件使用）。"""
    return page.locator('[role="alert"]').count()
