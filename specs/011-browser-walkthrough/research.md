# Research: 011 浏览器走查验收

编号 `R-01…`。每条写**决策 / 依据（带实测）/ 被否决的方案及理由**。

## R-01：Chromium 用 executablePath 指向缓存副本，不跑 `playwright install`

- **决策**：脚本按 `S12_CHROMIUM_PATH` → 扫描 `PLAYWRIGHT_BROWSERS_PATH`
  （默认 `~/Library/Caches/ms-playwright`）取**最高版本**的
  `chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell`
  → Playwright 默认顺序解析；命中缓存副本时用 `executable_path` 启动。
- **依据**：实测 2026-11-18——Python binding 1.65.2 与 Node 版 1.64.0
  期望的 revision 都与缓存（1234/1243/1254）不完全一致；`playwright install`
  需要联网下载（沙箱直连 pythonhosted 502，需代理），而缓存副本
  （headless shell 1254）实测可直接启动（`page.set_content` +
  `text_content` 验证通过）。headless shell 是无头专用构建，正是验收
  脚本需要的形态。
- **被否决**：① `playwright install chromium`——每次新环境都要联网，
  且版本随 binding 漂移；② 只用 Playwright 默认解析——期望 revision
  不在缓存里时直接失败，错误信息还指不回「你有缓存副本」这个事实。

## R-02：Playwright 只进 dev extras（lock 的 dev 组），运行时零新增

- **决策**：`pyproject.toml` 的 `[project.optional-dependencies] dev`
  加 `playwright>=1.40`，`uv lock` 同步（标记 `extra == 'dev'`）；
  Dockerfile 的 `uv sync --locked --no-dev` 不装它。
- **依据**：实测 Dockerfile 第 39/48 行是 `uv sync --locked --no-dev
  (--no-editable)`——dev extras 永不进生产镜像；lock 里
  `{ name = "playwright", marker = "extra == 'dev'", specifier = ">=1.40" }`
  确认分组正确。这与 pytest/ruff/mypy 的定位一致：验收工具，不是运行时。
  安装需代理（用户 2026-11-18 提示 7897）：沙箱直连 pythonhosted 502，
  `HTTPS_PROXY=http://127.0.0.1:7897 uv pip install` 成功。
- **被否决**：① 加进运行时 dependencies——直接违反宪法「技术栈零新增」
  的运行时不变量，且撑大生产镜像；② 用 Node 版 Playwright（本机受管
  工作区已装）——偏离用户明确选择的「Python 脚本入 deploy/verify」形态，
  且验收家族出现两套调度（Python/Node）。

## R-03：种子数据走 API，不直连数据库

- **决策**：脚本用 httpx 注册用户/建项目/需求/边/成员/Agent，再驱动 UI。
- **依据**：与 s1–s11 一致（它们全部走 API）；更重要的是直连数据库的
  种子**绕过应用层**——校验（如 010 的「停用用户不能加成员」）、审计、
  通知都不经手，验收就只验了 UI 渲染，没验「数据是怎么合法进来的」。
  且 API 种子顺带覆盖了注册/登录链路本身。
- **被否决**：直连测试库 INSERT——快但绕过应用层，且需要数据库凭据
  （s 系列脚本一直不需要，这是刻意的轻依赖）。

## R-04：不拉 dev server，驱动栈上前端容器（8080）

- **决策**：脚本只消费 `--frontend-url`（默认 `http://localhost:8080`）——
  即栈的前端容器（nginx + 构建产物）。**不**用子进程管理 vite dev server。
- **依据**：实测 2026-11-18——`vite.config.ts` 的代理目标写死
  `http://127.0.0.1:8000`，而 API 容器的 8000 **未发布到宿主机**
  （`docker ps`：aw-api 只有网络内 8000/tcp；宿主持有的是 aw-frontend
  的 8080）。宿主机上跑 vite dev，`/api` 全数 404。改 vite.config 加
  env 覆盖属于应用代码改动（违反 FR-009/R-08）。而栈上的前端容器
  由 deploy.sh `compose build` 重建——验收合并后的特性时它本来就是
  最新的，且与用户真实使用的形态一致（SPA + nginx 代理）。
- **被否决**：① 子进程拉起 `npm run dev`——宿主机上 API 全 404，
  验收必然全挂；② 给 vite.config 加 `VITE_API_TARGET` env 覆盖——
  能跑但破了「零应用改动」边界，且让验收环境与部署形态分叉；
  ③ 发布 API 端口到宿主机——动 compose 是部署面变更，为一个验收
  脚本不值得。
- **残余价值**：`--frontend-url` 保留——开发者本机若起了可用 dev
  server（如配了隧道或改了代理），可指向它验工作树未合并的改动。

## R-05：登录走 UI，不用 localStorage 注入

- **决策**：所有角色经 `/login` 页面真实登录（填邮箱密码提交）。
- **依据**：登录流程本身是前端的一部分（App.tsx 的「启动时恢复会话」
  就在验证它）；用 localStorage 注入 token 等于把「登录能用」这条
  永远跳过——而它恰恰是任何走查的前置。
- **被否决**：localStorage 注入 token——快半秒，但验收 coverage 缺一块，
  且 token 过期/刷新逻辑（恢复会话）永远不被走查碰到。

## R-06：拖拽用真实指针事件，传感器不响应时退化并注明

- **决策**：009 的被挡分配断言用 `mouse.move → down → move → up` 序列
  拖卡片到 Agent 侧栏目标；若 dnd-kit PointerSensor 不响应（无头环境
  偶发），退化为点选分配（AssignTargets 面板）并在输出行注明
  「（退化：点选）」。
- **依据**：dnd-kit 的 PointerSensor 监听 pointer 事件，Playwright 的
  mouse 序列会派发对应 pointer 事件，通常可用；但无头 + 合成事件下
  距离阈值（activation constraint）可能不满足——与其让整项假失败，
  不如退化到同样打到 assign 端点的路径，并诚实标注走了哪条。
- **被否决**：① 只保留拖拽——传感器抖动时整项 ❌，但 UI 其实是好的；
  ② 只保留点选——清单原文写的是「拖」，绕过去等于改了验收对象；
  ③ 拖拽失败静默转点选——不注明就是假装拖拽通过（欺骗性验收）。

## R-07：产物目录按次运行分目录 + 进 .gitignore

- **决策**：失败项的截图 PNG + 页面文本摘要落
  `deploy/verify/.artifacts/s12-<UTC 时间戳>/`；该目录进 `.gitignore`。
- **依据**：按次分目录避免第二次跑覆盖第一次的证据（SC-005 的「人为
  破坏一次」复现实验需要前后两次的产物都在）；截图是排查证据不是
  交付物，进 git 只会污染历史（同理 s 系列从不提交 .artifacts）。
- **被否决**：固定文件名覆盖——复现实验没法对比；提交截图——仓库
  不是图床。

## R-08：本特性零应用代码改动，发现的问题另开特性

- **决策**：s12 只新增脚本与文档；走查中发现的任何前端 bug 记录到
  quickstart 对应条目下（保持 ⏳ + 失败原因），另开特性修。
- **依据**：特性边界纪律——「发现」与「修」混在一个特性里，会让
  Constitution Check 与回归面失控（011 的回归面就是「什么都没改」
  所以零风险；一旦顺手改应用代码，这个性质就没了）。
- **被否决**：走查发现问题当场修——回归面瞬间变大，且 s12 的
  「15/15」会被「顺手修的两个 bug」污染成不可比的历史。
