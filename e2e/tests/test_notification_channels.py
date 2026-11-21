"""通知渠道（018）：设置页入口 + 新建 → 列表可见 + 私网地址被拒。

为什么这两条值得进常驻套件：
- 设置页是 015 重排后的二级导航，新入口最容易「菜单里有、点进去 404」；
- 私网 URL 的 400 是安全底线（SSRF），它在 UI 上必须是**可读的错误**
  而不是一个安静的失败。

投递链路不在这里验（需要真实出网 + 等待 worker），由
`deploy/verify/s14_notification_webhook.py` 覆盖。
"""

from __future__ import annotations

import re


def test_create_channel_via_ui(base_url: str, page_as, run_id: str) -> None:
    page = page_as("owner")
    page.goto(f"{base_url}/settings/notifications", wait_until="networkidle")

    page.get_by_role("button", name="新建通道").click()
    url = f"https://httpbin.org/post?e2e={run_id}"
    page.get_by_label("Webhook 地址").fill(url)
    # 勾一个事件，验证提交后订阅被保存（全不勾 = 全部，见页面文案）
    page.get_by_text("运行结束").click()
    page.get_by_role("button", name="创建").click()

    page.get_by_text(url).first.wait_for(timeout=15_000)
    assert page.get_by_text("订阅 1 类事件").first.is_visible()


def test_private_url_rejected_with_readable_error(
    base_url: str, page_as, run_id: str
) -> None:
    page = page_as("owner")
    page.goto(f"{base_url}/settings/notifications", wait_until="networkidle")

    page.get_by_role("button", name="新建通道").click()
    page.get_by_label("Webhook 地址").fill(f"http://127.0.0.1:9000/hook?{run_id}")
    page.get_by_role("button", name="创建").click()

    # 保存时就拒（不是投递时才失败），且消息能看懂
    page.get_by_text(re.compile("不允许")).first.wait_for(timeout=10_000)
    # 弹层不关，用户可以直接改地址
    assert page.get_by_label("Webhook 地址").is_visible()
