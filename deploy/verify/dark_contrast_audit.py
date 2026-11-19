"""深色主题对比度审计（015 T023）：真实浏览器逐页量文本对比度。

为什么不靠肉眼：深色模式下「灰字压灰底」肉眼很难判定，而 WCAG 有确定阈值
（正文 4.5:1、大号字 3:1）。这个脚本把每个可见文本元素的 color 与它实际
叠在其上的背景色取出来算比值，把不合格项连选择器一起报出来。

做法要点：
- 背景色要**向上找**：元素自身背景可能透明，真正的底色可能在祖先上；
  一路找到 alpha > 0.05 的那一层（都没有就用 body 的）。
- 大号字按 WCAG：≥24px，或 ≥18.66px 且 bold ≥700 → 阈值 3.0；其余 4.5。
- 跳过不可见元素（display:none / visibility:hidden / 0 尺寸 / opacity 0），
  否则隐藏的抽屉与被裁掉的内容会制造大量假阳性。
- 每个页面最多报 8 条：目的是「发现问题并定位」，不是生成一份三千行的报表。

用法：
    backend/.venv/bin/python deploy/verify/dark_contrast_audit.py \
      --base http://localhost:8080 \
      --email w-owner-6a204a@test.com --password 'TestPass123!'

退出码：0 = 全部达标；1 = 有不合格项（可直接进 CI）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PAGES = [
    "/",
    "/projects",
    "/dashboard",
    "/agents",
    "/groups",
    "/runs",
    "/settings",
    "/settings/templates",
    "/settings/schedules",
    "/settings/marketplace",
    "/settings/admin",
]

# 页面内执行的对比度计算（返回不合格项）
_AUDIT_JS = r"""
() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((x) => parseFloat(x.trim()));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  // 背景必须**逐层合成**，不能「找到第一个不透明就停」：
  // 徽章底色常是 bg-emerald-500/10（alpha=0.1），直接当实色算会把
  // 「浅色字 + 深色底」误判成「浅色字 + 亮绿底」，报出一堆假阳性。
  const blend = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  // 渐变（背景图）算不出单一底色：头像、主按钮都是渐变底，直接按父级纯色
  // 算会得到「白字压白底」这种假阳性。链路里出现渐变就跳过该元素。
  const hasGradient = (el) => {
    let node = el;
    while (node) {
      const bgImage = getComputedStyle(node).backgroundImage;
      if (bgImage && bgImage !== 'none') return true;
      node = node.parentElement;
    }
    return false;
  };
  const backdrop = (el) => {
    const layers = [];
    let node = el;
    while (node) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0.001) layers.push(bg);
      node = node.parentElement;
    }
    // 从最外层（先压的）往里叠
    let out = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i -= 1) out = blend(layers[i], out);
    return out;
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const selector = (el) => {
    const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  };

  const worst = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = (node.textContent || '').trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || !visible(el)) continue;
    if (el.closest('svg')) continue;
    if (hasGradient(el)) continue;
    const s = getComputedStyle(el);
    const fg = parse(s.color);
    if (!fg) continue;
    const bg = backdrop(el);
    const px = parseFloat(s.fontSize);
    const weight = parseInt(s.fontWeight, 10) || 400;
    const large = px >= 24 || (px >= 18.66 && weight >= 700);
    const need = large ? 3.0 : 4.5;
    const got = ratio(fg, bg);
    if (got + 0.01 < need) {
      worst.push({
        selector: selector(el),
        text: text.slice(0, 28),
        fg: s.color,
        bg: `rgb(${bg.r}, ${bg.g}, ${bg.b})`,
        px: Math.round(px * 10) / 10,
        ratio: Math.round(got * 100) / 100,
        need,
      });
    }
  }
  return worst;
}
"""


def _login(page: Page, base: str, email: str, password: str) -> None:
    page.goto(f"{base}/login", wait_until="networkidle")
    page.get_by_label("邮箱").fill(email)
    page.get_by_label("密码", exact=True).fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--max-per-page", type=int, default=8)
    parser.add_argument(
        "--mode",
        choices=["dark", "light"],
        default="dark",
        help="审哪个主题（两者都要过，浅色的失败点通常不同）",
    )
    args = parser.parse_args()

    chromium = os.environ.get("S12_CHROMIUM_PATH")
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not chromium and cache.is_dir():
        candidates = sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
        if candidates:
            chromium = str(candidates[-1])

    total = 0
    findings: dict[str, list[dict]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.new_page()
        _login(page, args.base, args.email, args.password)
        # 用应用自己的存储键切深色，避免「脚本改了 class 但业务没切主题」的假验证。
        # 注意：zustand/persist 存的是 JSON（{"state":{"mode":"dark"}}），
        # 直接写 'dark' 会解析失败静默退回 system——曾因此量了一整轮浅色。
        page.evaluate(
            f"localStorage.setItem('aw_theme', JSON.stringify({{state:{{mode:'{args.mode}'}},version:0}}))"
        )

        for path in PAGES:
            page.goto(f"{args.base}{path}", wait_until="networkidle")
            page.wait_for_timeout(400)
            is_dark = page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            )
            if is_dark != (args.mode == "dark"):
                page.evaluate(
                    f"localStorage.setItem('aw_theme', JSON.stringify({{state:{{mode:'{args.mode}'}},version:0}}))"
                )
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(300)
            bad = page.evaluate(_AUDIT_JS)
            findings[path] = bad
            total += len(bad)
            mark = "OK " if not bad else "FAIL"
            print(f"[{mark}] {path}  {len(bad)} 项低于阈值")
            for item in bad[: args.max_per_page]:
                print(
                    f"       {item['selector']:<34} 对比 {item['ratio']}:1"
                    f"（需 {item['need']}，{item['px']}px）"
                    f" 前景 {item['fg']} / 背景 {item['bg']}  «{item['text']}»"
                )

        browser.close()

    out = Path(__file__).parent / "artifacts" / "dark-contrast.json"
    if not out.parent.exists():  # 沙箱里 mkdir(exist_ok=True) 会抛 EEXIST
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {total} 项不合格（明细：{out}）")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
