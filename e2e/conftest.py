"""常驻浏览器 e2e 套件的共享夹具。

与 `deploy/verify/s12_browser_walkthrough.py` 的分工：
- s12 是一次性的**清单验收**（008/009/010 三份 quickstart 的 15 项，出证据用）；
- 这里是**可重复的回归套件**（按功能分文件、共享登录态、失败自动留证），
  每加一个前端特性就往对应文件里加一条用例，`pytest e2e` 一条命令跑完。

环境变量：
  AW_E2E_BASE        前端与 API 的同源地址，默认 http://127.0.0.1:8080
  AW_E2E_INVITE      注册用邀请码。**缺失时全部 skip**——本地没起栈不该跑出一片红
  AW_E2E_HEADED      1 = 开窗跑（调 locator 时用）
  AW_E2E_CHROMIUM_PATH / S12_CHROMIUM_PATH   指定浏览器可执行文件
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from support.api import PASSWORD, ApiClient
from support.browser import launch_kwargs

ARTIFACT_DIR = Path(__file__).resolve().parent / ".artifacts"
VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_TIMEOUT_MS = 15_000


# ---------------------------------------------------------------- 环境


@pytest.fixture(scope="session")
def base_url() -> str:
    base = os.environ.get("AW_E2E_BASE", "http://127.0.0.1:8080").rstrip("/")
    try:
        resp = httpx.get(f"{base}/api/v1/health", timeout=3.0)
    except Exception as exc:  # noqa: BLE001 - 环境缺失属于 skip，不是失败
        pytest.skip(f"全栈不可达（{base}）：{exc}。先起 docker compose 或后端+前端。")
    if resp.status_code != 200:
        pytest.skip(f"健康检查非 200（{base}/api/v1/health → {resp.status_code}）")
    return base


@pytest.fixture(scope="session")
def invite() -> str:
    code = os.environ.get("AW_E2E_INVITE", "").strip()
    if not code:
        pytest.skip("未设置 AW_E2E_INVITE（注册需要邀请码），跳过 e2e")
    return code


@pytest.fixture(scope="session")
def api(base_url: str) -> Iterator[ApiClient]:
    client = ApiClient(base_url)
    yield client
    client.close()


@pytest.fixture(scope="session")
def run_id() -> str:
    """随机后缀：同一套用例重复跑不会撞邮箱 / 项目名（SC-004）。"""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def accounts(api: ApiClient, invite: str, run_id: str) -> dict[str, dict[str, str]]:
    """owner + mate 两个账号（member 角色，经真实注册端点创建）。"""
    out: dict[str, dict[str, str]] = {}
    for who in ("owner", "mate"):
        email = f"e2e-{who}-{run_id}@test.com"
        body = api.register(email, invite)
        out[who] = {
            "email": email,
            "token": body["tokens"]["access"],
            "id": body["user"]["id"],
        }
    return out


# ---------------------------------------------------------------- 浏览器


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        instance = pw.chromium.launch(**launch_kwargs())
        yield instance
        instance.close()


@pytest.fixture(scope="session")
def storage_states(
    browser: Browser, base_url: str, accounts: dict[str, dict[str, str]]
) -> dict[str, dict]:
    """每个账号经 **真实 UI 登录**一次，存下 storage_state 供后续用例复用。

    登录流程本身由 test_smoke::test_login_flow 单独覆盖；
    其余用例复用 cookie 是为了让断言聚焦在业务页面，而不是重复走登录表单。
    """
    states: dict[str, dict] = {}
    for who, account in accounts.items():
        context = browser.new_context(viewport=VIEWPORT, locale="zh-CN")
        page = context.new_page()
        page.goto(f"{base_url}/login", wait_until="networkidle")
        page.get_by_label("邮箱").fill(account["email"])
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录").click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)
        states[who] = context.storage_state()
        context.close()
    return states


@pytest.fixture
def page_as(
    request: pytest.FixtureRequest,
    browser: Browser,
    storage_states: dict[str, dict],
) -> Iterator[Callable[[str], Page]]:
    """按账号开页：`page = page_as("owner")`。用例结束统一关闭。"""
    opened: list[tuple[BrowserContext, Page]] = []

    def _open(who: str = "owner") -> Page:
        context = browser.new_context(
            storage_state=storage_states[who], viewport=VIEWPORT, locale="zh-CN"
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        opened.append((context, page))
        request.node._aw_e2e_page = page  # 给失败留证的 hook 用
        return page

    yield _open

    for context, page in opened:
        try:
            page.close()
        finally:
            context.close()


# ---------------------------------------------------------------- 失败留证


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return

    page = getattr(item, "_aw_e2e_page", None)
    if page is None:
        return

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.nodeid)
    try:
        page.screenshot(path=str(ARTIFACT_DIR / f"{stem}.png"), full_page=True)
        body = page.evaluate("() => document.body?.innerText ?? ''")
        (ARTIFACT_DIR / f"{stem}.txt").write_text(body[:4000], encoding="utf-8")
        print(f"\n[e2e] 失败留证：{ARTIFACT_DIR / stem}.png / .txt")
    except Exception as exc:  # noqa: BLE001 - 留证失败不掩盖断言失败本身
        print(f"\n[e2e] 留证失败：{exc}")
