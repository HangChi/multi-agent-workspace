"""视觉回归基线（015 T024 的落点）：三个「结构稳定、数据为空」的页面。

为什么只挑三个页面：

- **看板 / 项目 / 需求**里都是别的用例造的数据（标题带随机 run_id），截图必然每跑
  都不一样；给它们做视觉基线只会收获一堆假红。
- **登录页、新账号的看板空态、新账号的模板页空态**结构固定、无时间戳、无计数，
  像素级比对才有意义。它们恰好覆盖「外壳 / 主内容区 / 设置二级页」三种布局。

Playwright 首次运行会**自动生成**基线 PNG（`tests/screenshots/`），之后每次比对。
容差 2%：字体抗锯齿在不同 Canvas 后端会有细微差别，0 容差在本机也可能偶发闪红。

更新基线的正确姿势：删掉对应 PNG 再跑一次（新基线会重新生成并提示）。
不要「看到红了就删」——先看截图，确认是预期的视觉变更再更新。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Page

SHOTS = Path(__file__).resolve().parent / "screenshots"

# 允许 2% 的像素变化：字体抗锯齿在不同渲染路径下会有边缘噪声，
# 0 容差会让本机重跑也偶发闪红（那样的基线没人会维护）
MAX_DIFF_RATIO = 0.02
# 单像素某通道差 ≤12 视为噪声（≈5% 亮度），超过才算「真的变了」
PIXEL_NOISE = 12


def _shot(page: Page, name: str) -> None:
    """截图并与基线比对；基线缺失时生成并 skip（明确告知，不是静默通过）。

    没用 `expect(...).to_have_screenshot`：那是 pytest-playwright 插件的
    fixture 语义，本项目用的是原生 sync_playwright，Python 版没有这个方法。
    """
    # 不用 mkdir(exist_ok=True)：沙箱的文件系统 shim 在目录已存在时会抛 EEXIST
    if not SHOTS.is_dir():
        SHOTS.mkdir(parents=True)

    data = page.screenshot(animations="disabled")
    target = SHOTS / name
    if not target.exists():
        target.write_bytes(data)
        pytest.skip(f"视觉基线不存在，已生成 {name}（下次运行开始比对）")

    expected = Image.open(target).convert("RGB")
    actual = Image.open(BytesIO(data)).convert("RGB")
    assert expected.size == actual.size, (
        f"{name}: 截图尺寸变了 {expected.size} → {actual.size}（布局发生结构性变化）"
    )

    diff = ImageChops.difference(expected, actual).convert("L")
    histogram = diff.histogram()
    changed = sum(histogram[PIXEL_NOISE + 1 :])
    ratio = changed / (expected.size[0] * expected.size[1])
    assert ratio <= MAX_DIFF_RATIO, (
        f"{name}: {ratio:.2%} 的像素发生变化（容差 {MAX_DIFF_RATIO:.0%}）。"
        f"确认是预期变更的话，删掉 {target.name} 重新生成基线。"
    )


def test_login_page_visual(browser, base_url: str) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = context.new_page()
    try:
        page.goto(f"{base_url}/login", wait_until="networkidle")
        page.get_by_label("邮箱").wait_for()
        _shot(page, "login.png")
    finally:
        context.close()


def test_board_empty_state_visual(page_as, base_url: str) -> None:
    """新账号的看板空态：没有卡片、没有计数，结构固定。"""
    page = page_as("mate")
    page.goto(base_url, wait_until="networkidle")
    page.get_by_role("banner").wait_for()
    _shot(page, "board-empty.png")


def test_templates_empty_state_visual(page_as, base_url: str) -> None:
    """设置二级页的空态：验证设置壳（左导航 + 内容区）没有走样。"""
    page = page_as("mate")
    page.goto(f"{base_url}/settings/templates", wait_until="networkidle")
    page.get_by_text("模板").first.wait_for()
    _shot(page, "templates-empty.png")
