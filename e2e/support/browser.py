"""浏览器定位与启动参数。

`playwright install` 需要联网，而本机 / 离线环境的浏览器往往已经在
`~/Library/Caches/ms-playwright`（mac）或 `~/.cache/ms-playwright`（linux）里。
s12 的 R-01 记过这个坑：版本号对不上时直接跑会报 executable doesn't exist。
这里沿用同一套解析顺序，只补充 linux 容器形态。
"""

from __future__ import annotations

import os
from pathlib import Path

_CACHE_CANDIDATES = (
    Path.home() / "Library" / "Caches" / "ms-playwright",
    Path.home() / ".cache" / "ms-playwright",
)


def _search_roots() -> list[Path]:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = [Path(override)] if override else []
    roots.extend(_CACHE_CANDIDATES)
    return roots


def chromium_executable() -> str | None:
    """返回可用的 chromium 可执行文件路径；None 表示交给 Playwright 默认解析。"""
    direct = os.environ.get("AW_E2E_CHROMIUM_PATH") or os.environ.get("S12_CHROMIUM_PATH")
    if direct:
        return direct

    for root in _search_roots():
        if not root.is_dir():
            continue
        # 版本号目录排序后取最高——缓存里常有多个旧版本
        shells = sorted(
            list(root.glob("chromium_headless_shell-*")) + list(root.glob("chromium-*")),
            key=lambda p: p.name,
        )
        for browser_dir in reversed(shells):
            for rel in (
                "chrome-headless-shell-mac-arm64/chrome-headless-shell",
                "chrome-headless-shell-mac-x64/chrome-headless-shell",
                "chrome-headless-shell-linux64/chrome-headless-shell",
                "chrome-linux/chrome",
                "chrome-linux/headless_shell",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            ):
                exe = browser_dir / rel
                if exe.exists():
                    return str(exe)
    return None


def launch_kwargs() -> dict[str, object]:
    """启动参数：headless 默认，AW_E2E_HEADED=1 时开窗（调 locator 时用）。"""
    kwargs: dict[str, object] = {
        "headless": os.environ.get("AW_E2E_HEADED") != "1",
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    exe = chromium_executable()
    if exe:
        kwargs["executable_path"] = exe
    return kwargs
