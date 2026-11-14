/**
 * S4/S5 前端真实浏览器验证（Playwright）。
 *
 * 覆盖 quickstart 里「前端手动验证」的部分：
 * - S4：看板把需求卡拖到 Agent 卡 → 确认弹窗 → 分配成功 → 卡片转「执行中」
 * - S5：点「查看运行」→ 运行详情页展示状态流转与执行步骤（WS 实时 + 兜底轮询）
 *
 * 用法（在仓库根目录）：
 *   NODE_PATH=$HOME/.workbuddy/binaries/node/workspace/node_modules \
 *   node deploy/verify/ui_s4_s5.cjs
 */

const path = require('path')
const { chromium } = require('playwright')

const BASE = process.env.BASE || 'http://localhost:8080'
const API = `${BASE}/api/v1`
const INVITE = process.env.INVITE || 'VERIFY-MEMBER-A'
const EMAIL = `ui-verify-${Date.now()}@test.com`
const PASSWORD = 'Verify123!'
const SHOT_DIR = path.join(__dirname, 'artifacts')

const results = []
function record(name, ok, detail) {
  results.push({ name, ok, detail })
  console.log(`${ok ? '✅' : '❌'} ${name} — ${detail}`)
}

async function api(method, url, body, token) {
  const res = await fetch(`${API}${url}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data = {}
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { raw: text }
  }
  return { status: res.status, data }
}

async function drag(page, srcBox, dstBox) {
  await page.mouse.move(srcBox.x + srcBox.width / 2, srcBox.y + srcBox.height / 2)
  await page.mouse.down()
  // dnd-kit PointerSensor：先小幅移动激活，再分步移动到目标
  await page.mouse.move(srcBox.x + srcBox.width / 2 + 12, srcBox.y + srcBox.height / 2 + 12, { steps: 6 })
  await page.waitForTimeout(120)
  await page.mouse.move(dstBox.x + dstBox.width / 2, dstBox.y + dstBox.height / 2, { steps: 24 })
  await page.waitForTimeout(180)
  await page.mouse.up()
}

async function main() {
  // ---------- 1. 准备数据（API 直建，聚焦 UI 交互） ----------
  const reg = await api('POST', '/auth/register', {
    email: EMAIL,
    password: PASSWORD,
    invite_code: INVITE,
  })
  if (reg.status !== 201) throw new Error(`注册失败 ${reg.status} ${JSON.stringify(reg.data).slice(0, 200)}`)
  const token = reg.data.tokens.access

  const agentName = `UI拖拽目标-${Date.now().toString().slice(-5)}`
  const agent = await api(
    'POST',
    '/agents',
    {
      name: agentName,
      description: '前端拖拽验证用 Agent',
      system_prompt: '你是任务执行助手',
      model: 'gpt-4o-mini',
      max_steps: 5,
      cost_limit_cents: 10000,
      tools: ['web_search'],
    },
    token,
  )
  if (agent.status !== 201) throw new Error(`建 Agent 失败 ${agent.status}`)

  const reqTitle = `UI拖拽需求-${Date.now().toString().slice(-5)}`
  const req = await api(
    'POST',
    '/requests',
    { title: reqTitle, description: '前端拖拽分配验证', expected_output: '三行要点', priority: 'normal' },
    token,
  )
  if (req.status !== 201) throw new Error(`建需求失败 ${req.status}`)

  // ---------- 2. 浏览器 ----------
  let browser
  try {
    browser = await chromium.launch({ channel: 'chrome' })
  } catch {
    browser = await chromium.launch()
  }
  const context = await browser.newContext({ viewport: { width: 1500, height: 950 } })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160))
  })
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 160)}`))

  try {
    // 登录页（顺带验证 S1 前端登录可用）
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
    await page.fill('input[type=email]', EMAIL)
    await page.fill('input[type=password]', PASSWORD)
    await page.click('button[type=submit]')
    await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
    record('S1 前端登录', true, `跳转至 ${new URL(page.url()).pathname}`)

    // 看板出现刚创建的需求卡
    const cardText = page.getByText(reqTitle, { exact: true }).first()
    await cardText.waitFor({ timeout: 15000 })
    record('S4 看板渲染待分配卡', true, `卡片「${reqTitle}」在「待分配」列`)

    const srcCard = cardText.locator('xpath=..')
    const dstAgent = page.getByText(agentName, { exact: true }).first().locator('xpath=../..')

    const srcBox = await srcCard.boundingBox()
    const dstBox = await dstAgent.boundingBox()
    if (!srcBox || !dstBox) throw new Error('拖拽源/目标不可见')

    await drag(page, srcBox, dstBox)

    // 确认弹窗（用 heading 精确定位：弹窗内标题与按钮同名「确认分配」）
    const modalTitle = page.getByRole('heading', { name: '确认分配', exact: true })
    const modalShown = await modalTitle
      .waitFor({ timeout: 8000 })
      .then(() => true)
      .catch(() => false)
    await page.screenshot({ path: path.join(SHOT_DIR, 's4-assign-modal.png') })
    record('S4 拖拽触发确认弹窗', modalShown, modalShown ? '弹窗标题「确认分配」出现' : '弹窗未出现')
    if (!modalShown) throw new Error('拖拽未触发确认弹窗')

    const modalText = await modalTitle.locator('xpath=../..').innerText()
    record(
      'S4 弹窗展示目标与优先级',
      modalText.includes(agentName) && /优先级/.test(modalText),
      modalText.replace(/\s+/g, ' ').slice(0, 120),
    )

    await page.getByRole('button', { name: '确认分配' }).click()

    // 分配成功后卡片进「执行中」（乐观更新 → 后端确认 → 列表刷新）
    const running = await page
      .getByText('执行中', { exact: true })
      .first()
      .waitFor({ timeout: 15000 })
      .then(() => true)
      .catch(() => false)
    // run_id 由 assign 的 onSuccess 写入，链接在回调后才渲染，必须显式等待
    const runLink = page.getByRole('link', { name: '查看运行' }).first()
    const hasRunLink = await runLink
      .waitFor({ state: 'visible', timeout: 20000 })
      .then(() => true)
      .catch(() => false)
    await page.screenshot({ path: path.join(SHOT_DIR, 's4-after-assign.png'), fullPage: true })
    record(
      'S4 分配成功卡片转执行中',
      running && hasRunLink,
      `执行中列出现=${running}，查看运行链接=${hasRunLink}`,
    )
    if (!hasRunLink) throw new Error('分配后未出现「查看运行」链接')

    // ---------- S5 运行详情 ----------
    await runLink.scrollIntoViewIfNeeded()
    const runHref = await runLink.getAttribute('href')
    try {
      await runLink.click({ timeout: 10000 })
    } catch {
      await page.goto(`${BASE}${runHref}`) // 兜底：点击被遮挡时直接跳转
    }
    await page.waitForURL(/\/runs\//, { timeout: 15000 })
    const t0 = Date.now()
    const stepsHeading = page.getByText('执行步骤', { exact: true })
    await stepsHeading.waitFor({ timeout: 15000 })
    const doneOrRunning = await page
      .locator('text=/已完成|执行中/')
      .first()
      .waitFor({ timeout: 30000 })
      .then(() => true)
      .catch(() => false)

    // 等终态：页面出现「已完成」
    const completed = await page
      .getByText('已完成', { exact: true })
      .first()
      .waitFor({ timeout: 60000 })
      .then(() => true)
      .catch(() => false)
    const elapsed = (Date.now() - t0) / 1000

    const bodyText = await page.locator('body').innerText()
    const costMatch = bodyText.match(/成本[\s\S]{0,40}/)
    const stepCount = (bodyText.match(/LLM 调用|工具调用/g) || []).length
    const currentStepMatch = bodyText.match(/当前步骤\s*(\d+)/)
    await page.screenshot({ path: path.join(SHOT_DIR, 's5-run-detail.png'), fullPage: true })

    record(
      'S5 运行详情实时流转到已完成',
      doneOrRunning && completed,
      `${elapsed.toFixed(2)}s 内到「已完成」，成本行=${costMatch ? costMatch[0].replace(/\s+/g, ' ').slice(0, 30) : '未找到'}`,
    )
    record(
      'S5 执行步骤渲染',
      stepCount >= 2,
      `步骤标题出现 ${stepCount} 次（LLM/工具调用）`,
    )
    record(
      'S5 当前步骤随执行推进',
      currentStepMatch !== null && Number(currentStepMatch[1]) > 0,
      currentStepMatch ? `当前步骤=${currentStepMatch[1]}` : '未找到「当前步骤」字段',
    )

    record(
      '前端无 console error',
      consoleErrors.length === 0,
      consoleErrors.length ? consoleErrors.slice(0, 2).join(' | ') : 'clean',
    )
  } catch (err) {
    await page.screenshot({ path: path.join(SHOT_DIR, 'ui-failure.png'), fullPage: true }).catch(() => {})
    record('UI 验证异常', false, String(err).slice(0, 220))
  } finally {
    await browser.close()
  }

  const ok = results.every((r) => r.ok)
  console.log(`\n===== UI 验证：${ok ? '✅ 全部通过' : '❌ 存在失败'}（${results.length} 项）=====`)
  process.exit(ok ? 0 : 1)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
