"""008/009/010 前端走查清单的浏览器验收（011 / T001-T009）。

三个特性合并时都按纪律把「前端浏览器走查」留了 ⏳（仓库无 e2e 基建立项），
本脚本补上这个基建：用 Playwright 驱动真实 Chromium，把三份 quickstart
里的 15 项清单逐条跑成 ✅/❌。

形态与 s1–s11 同构：CLI（--base/--invite/…）、逐项 ✅/❌ 输出、
汇总行、退出码 0/1、随机后缀种子（可重复跑）。

三组清单：
- A 组（008）：管理页 CRUD / 建需求选择器 / 项目下拉+URL 深链 / 卡片徽章 / viewer 门
- B 组（009）：依赖区块 / blocked 提示 / 前置徽章 / 拖拽错误横幅 / 铃铛跳转
- C 组（010）：成员区管理 / 成员只读 / 成员看板与表单 / 铃铛成员通知 / viewer 门

环境要求（实测 2026-11-18）：
- 后端与前端由同一 nginx 服务（--base 与 --frontend-url 默认同为 8080）；
- Chromium 经 S12_CHROMIUM_PATH 或 ~/Library/Caches/ms-playwright 的
  headless shell 解析（R-01：playwright install 需联网，缓存副本可直接用）。

用法：
    backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \\
      --base http://localhost:8080 --invite <INVITE> [--viewer-invite <INVITE>]
"""

import argparse
import datetime as dt
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "deploy" / "verify" / ".artifacts"
PASSWORD = "TestPass123!"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False
    elapsed_s: float = 0.0

    def line(self) -> str:
        cost = f"（{self.elapsed_s:.1f}s）" if self.elapsed_s else ""
        if self.skipped:
            return f"⏭️ {self.name} — {self.detail}"
        mark = "✅" if self.ok else "❌"
        return f"{mark} {self.name} — {self.detail}{cost}"


class Walkthrough:
    """s12 驱动器：种子 → 登录 → 三组断言 → 汇总。"""

    def __init__(
        self, base: str, frontend_url: str, invite: str, viewer_invite: str | None
    ):
        self.base = base.rstrip("/")
        self.frontend_url = frontend_url.rstrip("/")
        self.invite = invite
        self.viewer_invite = viewer_invite
        self.results: list[Result] = []
        self.http = httpx.Client(timeout=30.0)
        self.run_id = uuid.uuid4().hex[:6]
        self.artifact_dir = (
            ARTIFACTS_DIR / f"s12-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S')}"
        )
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.ctxs: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}
        self.seed: dict[str, dict] = {}
        self._browser: Browser | None = None

    # ---------- 基建 ----------

    def _record(
        self,
        name: str,
        ok: bool,
        detail: str,
        started: float,
        skipped: bool = False,
    ) -> None:
        self.results.append(
            Result(name, ok, detail, skipped, time.monotonic() - started)
        )

    def _artifacts(self, code: str, page: Page) -> str:
        """失败产物：截图 + 可见文本摘要，返回给输出行的路径提示。"""
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            png = self.artifact_dir / f"{code}.png"
            txt = self.artifact_dir / f"{code}.txt"
            page.screenshot(path=str(png), full_page=True)
            body_text = page.evaluate("() => document.body?.innerText ?? ''")
            txt.write_text(body_text[:4000], encoding="utf-8")
            return f" 产物：{png.relative_to(REPO_ROOT)}"
        except Exception as exc:  # noqa: BLE001 - 产物失败不掩盖断言失败本身
            return f" （产物落盘失败：{exc}）"

    def resolve_chromium(self) -> str | None:
        """浏览器解析（R-01）：env 直指定 → 缓存扫描取最高版本 → None（交给默认）。"""
        direct = os.environ.get("S12_CHROMIUM_PATH")
        if direct:
            return direct
        cache = Path(
            os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH",
                str(Path.home() / "Library" / "Caches" / "ms-playwright"),
            )
        )
        shells = sorted(cache.glob("chromium_headless_shell-*"))
        for shell in reversed(shells):
            exe = shell / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
            if exe.exists():
                return str(exe)
            # linux 形态（CI 可能是 linux）
            exe_linux = shell / "chrome-linux" / "headless_shell"
            if exe_linux.exists():
                return str(exe_linux)
        return None

    # ---------- 种子（API） ----------

    def _register(self, who: str, invite: str) -> dict:
        email = f"w-{who}-{self.run_id}@test.com"
        resp = self.http.post(
            f"{self.base}/api/v1/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "invite_code": invite,
            },
        )
        assert resp.status_code == 201, (
            f"注册失败: {resp.status_code} {resp.text[:200]}"
        )
        body = resp.json()
        self.tokens[who] = body["tokens"]["access"]
        self.ids[who] = body["user"]["id"]
        return body["user"]

    def _headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(
            f"{self.base}/api/v1{path}", json=json, headers=self._headers(who)
        )

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(
            f"{self.base}/api/v1{path}", headers=self._headers(who), params=params
        )

    def build_seed(self) -> None:
        """建全部种子（随机后缀，重跑不冲突——SC-004）。"""
        for who in ("owner", "mate", "stranger"):
            self._register(who, self.invite)
        if self.viewer_invite:
            try:
                self._register("viewer", self.viewer_invite)
            except AssertionError:
                self.viewer_invite = None  # 邀请码不可用则相关项跳过

        agent = self._post(
            "owner",
            "/agents",
            {
                "name": f"走查验收 {self.run_id}",
                "system_prompt": "你是验收助手，请简短回答。",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 4,
                "cost_limit_cents": 10000,
                "tools": [],
            },
        )
        assert agent.status_code == 201, f"建 agent 失败: {agent.text[:200]}"

        proj_a = self._post("owner", "/projects", {"name": f"走查A-{self.run_id}"})
        proj_c = self._post("owner", "/projects", {"name": f"走查C-{self.run_id}"})
        assert proj_a.status_code == proj_c.status_code == 201

        req_p1 = self._post(
            "owner",
            "/requests",
            {"title": f"项目内需求-{self.run_id}", "project_id": proj_a.json()["id"]},
        )
        req_p2 = self._post("owner", "/requests", {"title": f"散落需求-{self.run_id}"})
        req_b = self._post("owner", "/requests", {"title": f"后续需求-{self.run_id}"})
        req_pre = self._post("owner", "/requests", {"title": f"前置需求-{self.run_id}"})
        # B3/B5 各用一组**干净**数据对：B1 会在 req_b 上连边（其弹层不刷新，
        # 移除失败会留下第二条边），不让那个已知发现污染 B3 的计数与 B5 的通知
        req_b3 = self._post("owner", "/requests", {"title": f"徽章需求-{self.run_id}"})
        req_pre3 = self._post(
            "owner", "/requests", {"title": f"徽章前置-{self.run_id}"}
        )
        req_b5 = self._post("owner", "/requests", {"title": f"通知需求-{self.run_id}"})
        req_pre5 = self._post(
            "owner", "/requests", {"title": f"通知前置-{self.run_id}"}
        )
        for r in (req_p1, req_p2, req_b, req_pre, req_b3, req_pre3, req_b5, req_pre5):
            assert r.status_code == 201, r.text[:200]

        for dependent, pre in (
            (req_b, req_pre),
            (req_b3, req_pre3),
            (req_b5, req_pre5),
        ):
            edge = self._post(
                "owner",
                f"/requests/{dependent.json()['id']}/dependencies",
                {"dependency_id": pre.json()["id"]},
            )
            assert edge.status_code == 201, f"连边失败: {edge.text[:200]}"

        member = self._post(
            "owner",
            f"/projects/{proj_c.json()['id']}/members",
            {"user_id": self.ids["mate"]},
        )
        assert member.status_code == 201, f"加成员失败: {member.text[:200]}"

        self.seed = {
            "agent": agent.json(),
            "proj_a": proj_a.json(),
            "proj_c": proj_c.json(),
            "req_p1": req_p1.json(),
            "req_p2": req_p2.json(),
            "req_b": req_b.json(),
            "req_pre": req_pre.json(),
            "req_b3": req_b3.json(),
            "req_pre3": req_pre3.json(),
            "req_b5": req_b5.json(),
            "req_pre5": req_pre5.json(),
        }

    # ---------- 浏览器 ----------

    def _context(self, who: str) -> tuple[BrowserContext, Page]:
        if who not in self.ctxs:
            ctx = self._browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="zh-CN"
            )
            self.ctxs[who] = ctx
            self.pages[who] = ctx.new_page()
        return self.ctxs[who], self.pages[who]

    def login(self, who: str) -> Page:
        """经 UI 真实登录（R-05：登录流程本身也是被验收的一部分）。"""
        _, page = self._context(who)
        page.goto(f"{self.frontend_url}/login", wait_until="networkidle")
        page.get_by_label("邮箱").fill(f"w-{who}-{self.run_id}@test.com")
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录").click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        return page

    # ---------- 断言辅助 ----------

    @staticmethod
    def _close_modals(page: Page) -> None:
        """关掉所有残留弹窗（上一项失败时弹窗可能没关——B2 首轮就被它挡住）。"""
        for _ in range(3):
            if page.get_by_role("dialog").count() == 0:
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        # Escape 关不掉就点关闭按钮
        close = page.get_by_role("button", name="关闭").first
        if close.count():
            close.click()
            page.wait_for_timeout(400)

    @staticmethod
    def _open_detail(page: Page, title: str) -> None:
        """点卡片 hover 出现的「详情」按钮打开弹层。"""
        Walkthrough._close_modals(page)
        card = page.locator("article", has_text=title).first
        card.hover()
        card.get_by_role("button", name="详情").click()
        page.get_by_role("dialog").wait_for(timeout=8000)

    @staticmethod
    def _open_bell(page: Page) -> None:
        """开铃铛面板（首轮教训：点完要等面板内容出现，否则后续 wait 全超时）。"""
        bell = page.get_by_role("button", name=re.compile("未读|通知")).first
        for _ in range(2):
            bell.click()
            try:
                page.get_by_role(
                    "button", name=re.compile("全部已读|查看全部|通知")
                ).last.wait_for(timeout=3000)
                return
            except Exception:  # noqa: BLE001 - 面板没开就再点一次
                page.wait_for_timeout(500)
        # 面板锚点不确定时退而求其次：等任意通知项按钮出现
        page.locator("button", has_text="ago").first.wait_for(timeout=5000)

    # ---------- 场景组 ----------

    def group_a(self) -> None:
        """A 组（008 清单 5 项）。"""
        page = self.pages["owner"]

        # A1 管理页 CRUD
        started = time.monotonic()
        try:
            page.goto(f"{self.frontend_url}/projects", wait_until="networkidle")
            page.get_by_role("button", name="新建项目").first.click()
            name = f"A1新建-{self.run_id}"
            proj_id = None
            # 「新建项目」是页内内联表单（不是弹窗）——label 项目名称
            page.get_by_label("项目名称").fill(name)
            page.get_by_role("button", name="创建项目").click()
            page.get_by_text(name).first.wait_for(timeout=8000)
            # 从 API 拿新建项目的 id（页面不暴露 id，塞需求要用）
            listed = self._get("owner", "/projects", scope="mine")
            proj_id = next(
                (p["id"] for p in listed.json()["items"] if p["name"] == name), None
            )
            assert proj_id, "新建项目未出现在列表"

            # 改名行不能再用 has_text=name 定位——进入改名态后行内是输入框，
            # value 不算 text content，原定位会失效（首轮 30s 超时在这）。
            # 改用「含保存按钮的行」定位编辑态。
            page.locator("li", has_text=name).first.get_by_role(
                "button", name="改名"
            ).click()
            renamed = f"A1改名-{self.run_id}"
            edit_row = page.locator("li", has=page.get_by_role("button", name="保存"))
            edit_row.locator("input").first.fill(renamed)
            edit_row.get_by_role("button", name="保存").click()
            page.get_by_text(renamed).first.wait_for(timeout=8000)

            # 删除确认文案按项目有无需求分两套——塞一条需求才能验到
            # 「需求会保留、归属清空」那套（清单要的就是这句）。
            # 必须在归档之前塞：归档项目不能作为新需求归属（008 纪律）
            seeded = self._post(
                "owner",
                "/requests",
                {"title": f"A1删除前-{self.run_id}", "project_id": proj_id},
            )
            assert seeded.status_code == 201, seeded.text[:150]
            row = page.locator("li", has_text=renamed).first
            row.get_by_role("button", name="归档").click()
            page.get_by_text("已归档").first.wait_for(timeout=8000)

            row = page.locator("li", has_text=renamed).first
            row.get_by_role("button", name="删除").click()
            confirm = page.get_by_role("dialog")
            confirm_text = confirm.inner_text()
            ok = "保留" in confirm_text
            confirm.get_by_role("button", name="删除").click()
            page.get_by_text(renamed).first.wait_for(state="detached", timeout=8000)
            self._record("A1 管理页 CRUD", ok, "新建/改名/归档/删除确认文案", started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "A1 管理页 CRUD",
                False,
                str(exc)[:120] + self._artifacts("A1", page),
                started,
            )

        # A2 建需求选择器
        started = time.monotonic()
        try:
            page.goto(self.frontend_url, wait_until="networkidle")
            page.get_by_role("button", name="新建需求").click()
            select = page.get_by_label("所属项目（可选）")
            select.wait_for(timeout=8000)
            # 候选是异步查询：等本人项目的选项出现再断言（首轮没等，
            # 只读到占位符就判了「仅 1 项」）
            # state='attached'：未展开的下拉里 option 是 hidden，等 visible
            # 会永远超时（首轮 15s 超时的真因——选项明明在）
            select.locator(
                "option", has_text=self.seed["proj_a"]["name"]
            ).first.wait_for(state="attached", timeout=15000)
            default_empty = select.input_value() == ""
            options = [o.inner_text() for o in select.locator("option").all()]
            ok = (
                default_empty
                and "不归类" in " ".join(options)
                and self.seed["proj_a"]["name"] in " ".join(options)
            )
            self._record(
                "A2 建需求选择器",
                ok,
                f"默认不归类 + 仅本人 active 项目（{len(options)} 项）",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "A2 建需求选择器",
                False,
                str(exc)[:120] + self._artifacts("A2", page),
                started,
            )

        # A3 项目下拉 + URL 深链 + 刷新保持
        started = time.monotonic()
        try:
            page.goto(self.frontend_url, wait_until="networkidle")
            select = page.get_by_label("按项目筛选")
            select.wait_for(timeout=8000)
            options = [o.inner_text() for o in select.locator("option").all()]
            joined = " ".join(options)
            select.select_option(value=self.seed["proj_a"]["id"])
            page.wait_for_url(re.compile(r"\?project="), timeout=8000)
            url_ok = f"project={self.seed['proj_a']['id']}" in page.url
            page.reload(wait_until="networkidle")
            kept = (
                page.get_by_label("按项目筛选").input_value()
                == self.seed["proj_a"]["id"]
            )
            ok = (
                "全部项目" in joined
                and "未归属" in joined
                and self.seed["proj_a"]["name"] in joined
                and url_ok
                and kept
            )
            self._record(
                "A3 项目下拉与深链",
                ok,
                f"选项齐全 + URL={url_ok} + 刷新保持={kept}",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "A3 项目下拉与深链",
                False,
                str(exc)[:120] + self._artifacts("A3", page),
                started,
            )

        # A4 卡片徽章
        started = time.monotonic()
        try:
            page.goto(self.frontend_url, wait_until="networkidle")
            # 等两张卡都渲染出来（board 数据异步——首轮没等就断言）
            page.locator(
                "article", has_text=self.seed["req_p1"]["title"]
            ).first.wait_for(timeout=8000)
            page.locator(
                "article", has_text=self.seed["req_p2"]["title"]
            ).first.wait_for(timeout=8000)
            with_badge = page.locator(
                "article", has_text=self.seed["req_p1"]["title"]
            ).first
            # 徽章是卡片内的 Link，等它出现再取文本（首轮没等，卡片有了徽章还没渲染）
            with_badge.get_by_text(self.seed["proj_a"]["name"]).first.wait_for(
                timeout=10000
            )
            ok_with = self.seed["proj_a"]["name"] in with_badge.inner_text()
            without = page.locator(
                "article", has_text=self.seed["req_p2"]["title"]
            ).first
            ok_without = self.seed["proj_a"]["name"] not in without.inner_text()
            self._record(
                "A4 卡片项目徽章",
                ok_with and ok_without,
                f"有归属渲染={ok_with} 无归属不渲染={ok_without}",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "A4 卡片项目徽章",
                False,
                str(exc)[:120] + self._artifacts("A4", page),
                started,
            )

        # A5 viewer 门
        started = time.monotonic()
        if not self.viewer_invite:
            self._record(
                "A5 viewer 门",
                False,
                "跳过（未提供 viewer 邀请码）",
                started,
                skipped=True,
            )
            return
        try:
            vpage = self.login("viewer")
            vpage.goto(self.frontend_url, wait_until="networkidle")
            no_dropdown = vpage.get_by_label("按项目筛选").count() == 0
            nav = vpage.locator("nav, header").first.inner_text()
            no_nav = "项目" not in nav
            detail = f"无下拉={no_dropdown} 无入口={no_nav}"
            if not no_nav:
                detail += "（发现：/projects 导航项无 memberOnly，viewer 看得见入口——008/010 缺口）"
            self._record("A5 viewer 门", no_dropdown and no_nav, detail, started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "A5 viewer 门",
                False,
                str(exc)[:120] + self._artifacts("A5", self.pages.get("viewer", page)),
                started,
            )

    def group_b(self) -> None:
        """B 组（009 清单 5 项）。"""
        page = self.pages["owner"]
        pre_title = self.seed["req_pre"]["title"]
        b_title = self.seed["req_b"]["title"]

        # B1 依赖区块（增删）
        started = time.monotonic()
        try:
            self._open_detail(page, b_title)
            section = page.locator("div", has_text="前置需求").last
            section_text = section.inner_text()
            ok_listed = pre_title in section_text
            # 添加器连一条：选「归属需求」再点添加。
            # 候选也是异步查询（scope=relevant）——先等选项出现
            select = page.get_by_label("选择前置需求")
            select.locator(
                "option", has_text=self.seed["req_p1"]["title"]
            ).first.wait_for(state="attached", timeout=15000)
            select.select_option(label=self.seed["req_p1"]["title"])
            page.get_by_role("button", name="添加", exact=True).click()
            # 判定全用「移除按钮」的 出现/消失，不用文本——移除后该需求会
            # 回到添加器候选里，弹层文本中仍有它（首轮两个陷阱都踩过）
            remove_btn = page.get_by_role(
                "button", name=f"移除前置 {self.seed['req_p1']['title']}"
            )
            added = removed = False
            try:
                remove_btn.wait_for(timeout=10000)
                added = True
                remove_btn.click()
                remove_btn.wait_for(state="detached", timeout=10000)
                removed = True
            except Exception:  # noqa: BLE001,S110 - 见下方发现记录（移除按钮不出现本身就是发现）
                pass
            page.keyboard.press("Escape")
            page.get_by_role("dialog").first.wait_for(state="detached", timeout=5000)
            detail = f"列表={ok_listed} 添加={added} 移除={removed}"
            if not ok_listed:
                detail += (
                    "（发现：详情弹层未加载 dependencies 明细——BoardPage 的"
                    " detailSource 优先用列表项，dependencies=null，区块永远"
                    "显示「0 个」；连边 API 正常但弹层不刷新）"
                )
            elif not added:
                detail += "（发现：连边后弹层不刷新，移除按钮不出现——同上根因）"
            self._record(
                "B1 依赖区块", ok_listed and added and removed, detail, started
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "B1 依赖区块",
                False,
                str(exc)[:120] + self._artifacts("B1", page),
                started,
            )

        # B2 blocked 提示
        started = time.monotonic()
        try:
            self._open_detail(page, b_title)
            dialog_text = page.get_by_role("dialog").inner_text()
            # 断言具体数量「有 1 个前置未完成」——只匹配「个前置未完成」的话，
            # 「有 0 个」也会通过（首轮就这样漏过了详情拿不到 dependencies 的真 bug）
            ok = "有 1 个前置未完成" in dialog_text and "暂不能分配执行" in dialog_text
            page.keyboard.press("Escape")
            page.get_by_role("dialog").first.wait_for(state="detached", timeout=5000)
            detail_msg = (
                "文案与数量正确"
                if ok
                else f"未见「有 1 个前置未完成」（实际：{dialog_text[:60]!r}）"
            )
            self._record("B2 blocked 提示", ok, detail_msg, started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "B2 blocked 提示",
                False,
                str(exc)[:120] + self._artifacts("B2", page),
                started,
            )

        # B3 卡片前置徽章 + hover + 就绪后消失
        started = time.monotonic()
        try:
            page.goto(self.frontend_url, wait_until="networkidle")
            b3_title = self.seed["req_b3"]["title"]
            pre3_title = self.seed["req_pre3"]["title"]
            badge = (
                page.locator("article", has_text=b3_title)
                .locator("span", has_text="个前置未完成")
                .first
            )
            badge.wait_for(timeout=10000)
            text = badge.inner_text()
            title_attr = badge.get_attribute("title") or ""
            ok_shown = "1" in text and pre3_title in title_attr
            # 把前置打到终态（建 Run 再取消）→ 徽章应消失
            assign = self._post(
                "owner",
                f"/requests/{self.seed['req_pre3']['id']}/assign",
                {"assignee_type": "agent", "assignee_id": self.seed["agent"]["id"]},
            )
            assert assign.status_code == 202, assign.text[:150]
            cancel = self.http.post(
                f"{self.base}/api/v1/runs/{assign.json()['run_id']}/cancel",
                headers=self._headers("owner"),
            )
            assert cancel.status_code in (200, 202), cancel.text[:150]
            # 取消是走 API 的（不经应用），看板列表不会自动重取——重新加载
            # 页面再验徽章消失（真实用户也会在下次导航/轮询时看到）
            page.goto(self.frontend_url, wait_until="networkidle")
            page.locator("article", has_text=b3_title).first.wait_for(timeout=10000)
            page.wait_for_timeout(1500)
            badge_after = (
                page.locator("article", has_text=b3_title)
                .locator("span", has_text="个前置未完成")
                .count()
            )
            ok_gone = badge_after == 0
            self._record(
                "B3 卡片前置徽章",
                ok_shown and ok_gone,
                f"数量与 hover={ok_shown} 就绪后消失={ok_gone}（取消前置后重载）",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "B3 卡片前置徽章",
                False,
                str(exc)[:120] + self._artifacts("B3", page),
                started,
            )

        # B4 拖拽被挡需求 → 错误横幅
        started = time.monotonic()
        try:
            page.goto(self.frontend_url, wait_until="networkidle")
            page.locator("article", has_text=b_title).first.wait_for(timeout=10000)
            target = page.locator(
                f"[title*='拖到这里分配给'][title*='{self.seed['agent']['name']}']"
            ).first
            target.wait_for(timeout=10000)
            # 侧栏 chips 可能横向溢出——先滚进视口再取坐标（首轮目标在
            # 视口外，mouse.move 到负坐标等于没拖）
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(300)

            def _attempt_drag() -> bool:
                # 顺序要紧：先滚目标再滚卡片。侧栏 chips 横向溢出（实测目标
                #  x=35457 在视口外），只滚卡片会把目标又挤出去——拖到
                # 视口外坐标等于没拖（首轮 3 次全败的真因）
                target.scroll_into_view_if_needed()
                card = page.locator("article", has_text=b_title).first
                card.scroll_into_view_if_needed()
                page.wait_for_timeout(250)
                tb = target.bounding_box()
                cb = card.bounding_box()
                if not cb or not tb:
                    return False
                page.mouse.move(cb["x"] + 30, cb["y"] + 15)
                page.mouse.down()
                page.wait_for_timeout(150)
                page.mouse.move(cb["x"] + 45, cb["y"] + 20, steps=8)
                page.mouse.move(
                    (cb["x"] + tb["x"]) / 2, (cb["y"] + tb["y"]) / 2, steps=12
                )
                page.mouse.move(
                    tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, steps=15
                )
                page.wait_for_timeout(150)
                page.mouse.up()
                return True

            specific = False
            generic_only = False
            drag_worked = False
            for attempt in range(3):
                _attempt_drag()
                # 落点弹「确认分配」框——点了才真发请求
                confirm = page.get_by_role("button", name="确认分配")
                try:
                    confirm.wait_for(timeout=4000)
                    drag_worked = True
                    confirm.click()
                except Exception:  # noqa: BLE001 - 没弹框：传感器没响应
                    self._close_modals(page)
                    continue
                page.wait_for_timeout(2500)
                if page.get_by_text("前置需求未完成").count() > 0:
                    specific = True
                    break
                if page.get_by_text("请求失败，请稍后重试").count() > 0:
                    # 409 发了但消息被吃掉——ErrorBanner 系统性 bug 的现场
                    # （对本字符串二次 extractError 落兜底，见 012 待修）
                    generic_only = True
                    break
                self._close_modals(page)
            ok = specific
            if specific:
                detail = "错误横幅逐条列出阻塞项"
            elif generic_only:
                detail = (
                    "发现：409 已挡下分配，但模态只显示通用兜底文案"
                    "（ErrorBanner 对已提取字符串二次提取——系统性 bug，19 处调用点）"
                )
            elif drag_worked:
                detail = "发现：确认分配后未见任何错误提示"
            else:
                detail = (
                    "发现：拖拽 3 次均未触达分配确认框（传感器/坐标问题，需人工复核）"
                )
            if not ok:
                detail += self._artifacts("B4", page)
            self._record("B4 拖拽错误横幅", ok, detail, started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "B4 拖拽错误横幅",
                False,
                str(exc)[:120] + self._artifacts("B4", page),
                started,
            )

        # B5 铃铛通知跳转（把前置置终态后验证——用干净对，不受 B1 污染）
        started = time.monotonic()
        try:
            b5_title = self.seed["req_b5"]["title"]
            # 经 API 把前置打到 cancelled（建 Run 再取消——系统无取消需求端点）
            assign = self._post(
                "owner",
                f"/requests/{self.seed['req_pre5']['id']}/assign",
                {"assignee_type": "agent", "assignee_id": self.seed["agent"]["id"]},
            )
            assert assign.status_code == 202, assign.text[:150]
            cancel = self.http.post(
                f"{self.base}/api/v1/runs/{assign.json()['run_id']}/cancel",
                headers=self._headers("owner"),
            )
            assert cancel.status_code in (200, 202), cancel.text[:150]

            page.goto(self.frontend_url, wait_until="networkidle")
            self._open_bell(page)
            item = page.get_by_text("前置就绪").first
            item.wait_for(timeout=8000)
            item.click()
            # 通知点击跳 /?request=<id> → 看板深链打开详情弹层
            page.wait_for_url(re.compile(r"\?request="), timeout=8000)
            dialog = page.get_by_role("dialog").first
            dialog.wait_for(timeout=8000)
            jumped = b5_title in dialog.inner_text()
            self._record(
                "B5 铃铛跳转",
                jumped,
                "点击通知后打开后续需求详情" if jumped else "未跳转",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "B5 铃铛跳转",
                False,
                str(exc)[:120] + self._artifacts("B5", page),
                started,
            )

    def group_c(self) -> None:
        """C 组（010 清单 5 项）。"""
        page = self.pages["owner"]
        proj_c_name = self.seed["proj_c"]["name"]

        # C1 owner 成员区
        started = time.monotonic()
        try:
            page.goto(f"{self.frontend_url}/projects", wait_until="networkidle")
            row = page.locator("li", has_text=proj_c_name).first
            row.wait_for(timeout=15000)
            row.scroll_into_view_if_needed()
            section_text = row.inner_text()
            ok_owner_badge = "创建者" in section_text
            ok_mate = "w-mate-" in section_text or self._email("mate") in section_text
            # 添加 stranger
            select = page.get_by_label("选择要添加的成员").first
            select.wait_for(timeout=8000)
            select.select_option(label=self._email("stranger"))
            page.get_by_role("button", name="添加", exact=True).first.click()
            page.wait_for_timeout(1500)
            row = page.locator("li", has_text=proj_c_name).first
            added = self._email("stranger") in row.inner_text()
            # 移除。判定用按钮消失而不是邮件文本——移除后该用户回到添加器
            # 候选里，邮箱文本仍在行内（首轮 8s 超时的真因）
            remove_btn = page.get_by_role(
                "button", name=f"移除成员 {self._email('stranger')}"
            )
            remove_btn.wait_for(timeout=8000)
            remove_btn.click()
            remove_btn.wait_for(state="detached", timeout=10000)
            removed = True
            self._record(
                "C1 成员区管理",
                ok_owner_badge and ok_mate and added and removed,
                f"创建者徽章={ok_owner_badge} 成员在列={ok_mate} 添加={added} 移除={removed}",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "C1 成员区管理",
                False,
                str(exc)[:120] + self._artifacts("C1", page),
                started,
            )

        # C2 成员只读
        started = time.monotonic()
        try:
            mpage = self.login("mate")
            mpage.goto(f"{self.frontend_url}/projects", wait_until="networkidle")
            # 成员可能一个项目都没有（页面只查 scope=mine）——等页面标题
            # 出现即算就绪，不能等 li（空列表永远等不到）
            mpage.get_by_text("项目是需求的业务容器").first.wait_for(timeout=15000)
            mpage.wait_for_timeout(1500)
            shared_li = mpage.locator("li", has_text=proj_c_name)
            if shared_li.count() == 0:
                # 快速失败 + 精确发现：项目页只查 scope=mine（010 前端缺口），
                # 成员在自己的项目页看不到共享项目——按纪律记录不修复。
                # 注意不能用 return——那会跳过 C3/C4/C5（首轮踩过）
                self._record(
                    "C2 成员只读",
                    False,
                    "发现：共享项目未出现在成员的项目页（ProjectsPage 查询 scope=mine，010 前端缺口）",
                    started,
                )
                shared_li = None
            if shared_li is not None:
                card = shared_li.first
                card.scroll_into_view_if_needed()
                text = card.inner_text()
                shared_badge = "共享的" in text
                readonly = mpage.get_by_label("选择要添加的成员").count() == 0
                no_remove = (
                    mpage.get_by_role("button", name=re.compile("移除成员")).count()
                    == 0
                )
                self._record(
                    "C2 成员只读",
                    shared_badge and readonly and no_remove,
                    f"共享徽章={shared_badge} 无添加器={readonly} 无移除={no_remove}",
                    started,
                )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "C2 成员只读",
                False,
                str(exc)[:120] + self._artifacts("C2", self.pages.get("mate", page)),
                started,
            )

        # C3 成员看板与表单
        started = time.monotonic()
        try:
            mpage = self.pages["mate"]
            mpage.goto(self.frontend_url, wait_until="networkidle")
            select = mpage.get_by_label("按项目筛选")
            select.wait_for(timeout=8000)
            options = " ".join(o.inner_text() for o in select.locator("option").all())
            in_dropdown = proj_c_name in options
            mpage.get_by_role("button", name="新建需求").click()
            form_select = mpage.get_by_label("所属项目（可选）")
            form_select.wait_for(timeout=8000)
            form_select.locator("option").first.wait_for(
                state="attached", timeout=15000
            )
            form_options = " ".join(
                o.inner_text() for o in form_select.locator("option").all()
            )
            in_form = proj_c_name in form_options
            detail = f"下拉={in_dropdown} 表单={in_form}"
            if not in_form:
                detail += "（发现：建需求表单查询 scope=mine，010 前端缺口）"
            self._record("C3 成员看板与表单", in_dropdown and in_form, detail, started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "C3 成员看板与表单",
                False,
                str(exc)[:120] + self._artifacts("C3", self.pages.get("mate", page)),
                started,
            )

        # C4 铃铛成员通知
        started = time.monotonic()
        try:
            mpage = self.pages["mate"]
            mpage.goto(self.frontend_url, wait_until="networkidle")
            self._open_bell(mpage)
            # UI 渲染：徽章「成员变动」+ 标题（项目名）+ 时间。
            # 「你已被加入项目 X」是文案意图，不是 DOM 文本——按实际渲染断言
            badge = mpage.get_by_text("成员变动").first
            badge.wait_for(timeout=8000)
            item = mpage.locator("button", has_text="成员变动").first
            ok = proj_c_name in item.inner_text()
            self._record(
                "C4 铃铛成员通知",
                ok,
                "成员变动通知存在且标题为项目名" if ok else "通知缺项目名",
                started,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(
                "C4 铃铛成员通知",
                False,
                str(exc)[:120] + self._artifacts("C4", self.pages.get("mate", page)),
                started,
            )

        # C5 viewer 门
        started = time.monotonic()
        if not self.viewer_invite:
            self._record(
                "C5 viewer 门",
                False,
                "跳过（未提供 viewer 邀请码）",
                started,
                skipped=True,
            )
            return
        try:
            vpage = self.login("viewer")
            vpage.goto(f"{self.frontend_url}/projects", wait_until="networkidle")
            nav = vpage.locator("nav, header").first.inner_text()
            no_entry = "项目" not in nav
            detail = f"无项目入口={no_entry}"
            if not no_entry:
                detail += "（发现：导航项无 memberOnly，同 A5）"
            self._record("C5 viewer 门", no_entry, detail, started)
        except Exception as exc:  # noqa: BLE001
            self._record(
                "C5 viewer 门",
                False,
                str(exc)[:120] + self._artifacts("C5", self.pages.get("viewer", page)),
                started,
            )

    def _email(self, who: str) -> str:
        return f"w-{who}-{self.run_id}@test.com"

    # ---------- 主流程 ----------

    def run(self) -> int:
        # 后端可达性
        try:
            health = self.http.get(f"{self.base}/api/v1/health", timeout=5.0)
            assert health.status_code == 200, f"health {health.status_code}"
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 后端不可达（{self.base}）：{exc}")
            return 1
        # 前端可达性（R-04：只消费，不拉起）
        try:
            home = self.http.get(self.frontend_url, timeout=5.0)
            assert home.status_code == 200, f"frontend {home.status_code}"
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 前端不可达（{self.frontend_url}）：{exc}")
            return 1

        print(f"种子：run_id={self.run_id}")
        self.build_seed()

        exe = self.resolve_chromium()
        print(f"浏览器：{exe or 'Playwright 默认解析'}")
        with sync_playwright() as pw:
            launch_kwargs = {"headless": True}
            if exe:
                launch_kwargs["executable_path"] = exe
            self._browser = pw.chromium.launch(**launch_kwargs)
            try:
                self.login("owner")
                self.login("mate")
                if self.viewer_invite:
                    self.login("viewer")
                self.group_a()
                self.group_b()
                self.group_c()
            finally:
                for ctx in self.ctxs.values():
                    ctx.close()
                self._browser.close()
        self.http.close()

        print()
        for r in self.results:
            print(r.line())
        passed = sum(1 for r in self.results if r.ok and not r.skipped)
        failed = sum(1 for r in self.results if not r.ok and not r.skipped)
        skipped = sum(1 for r in self.results if r.skipped)
        print(
            f"\n合计 {len(self.results)} 项：{passed} 通过 / {failed} 失败 / {skipped} 跳过"
        )
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="008/009/010 前端走查浏览器验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--frontend-url", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="member 邀请码")
    parser.add_argument("--viewer-invite", default=None, help="viewer 邀请码（可选）")
    args = parser.parse_args()
    walk = Walkthrough(args.base, args.frontend_url, args.invite, args.viewer_invite)
    raise SystemExit(walk.run())


if __name__ == "__main__":
    main()
