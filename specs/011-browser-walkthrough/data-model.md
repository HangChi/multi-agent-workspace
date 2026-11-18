# Data Model: 011 浏览器走查验收

本特性**零 schema 变更**——它是纯验收基础设施。数据模型只有两样新东西：
脚本内的种子数据构造（走 API，不落库Schema）与产物目录结构。

## 1. 种子数据（走 API，随机后缀防重跑冲突）

| 实体 | 构造 | 用途 |
|---|---|---|
| owner | `POST /auth/register`（invite=member） | A/B/C 三组的主操作者 |
| mate | 同上 | B/C 组的被通知者/成员 |
| stranger | 同上 | A/C 组的隔离对照 |
| viewer | 同上（invite=`--viewer-invite`） | A5/C5 的角色门（未提供则相关项诚实跳过） |
| agent | `POST /agents`（owner 名下，Deepseek-V4.1-Flash） | B4 的分配目标 |
| 项目 ×2 | `POST /projects`（owner） | A 组管理对象；C 组共享对象 |
| 需求 ×N | `POST /requests`（含/不含 project_id） | 卡片/徽章/筛选的素材 |
| 依赖边 | `POST /requests/{id}/dependencies` | B 组的阻塞素材 |
| 成员 | `POST /projects/{id}/members` | C 组的共享素材 |

所有名字带 `w-<随机 6 位>` 后缀（同 s1–s11 纪律）——重跑不冲突，
断言不依赖空库（SC-004）。

## 2. 产物目录（失败证据）

```
deploy/verify/.artifacts/s12-<UTC timestamp>/
  A3-project-filter.png        # 失败项截图（项编号 + 简述）
  A3-project-filter.txt        # 该页可见文本摘要（innerText 截断）
  ...
```

- 按项命名：`<编号>-<简述>.png/.txt`；一项失败可能有多张（失败时的
  每一步状态）。
- `.gitignore` 加 `deploy/verify/.artifacts/`（截图是证据不是交付物，
  R-07）。

## 3. 脚本内状态（不持久化）

- `Result(name, ok, detail, elapsed_s)`：与 s1–s11 同构的逐项结果行。
- 浏览器上下文：owner/mate/viewer 各一个 `BrowserContext`（cookie 隔离，
  不反复登出登入）；dev server 子进程句柄（finally 回收）。

## 4. 明确不做

- ❌ 任何数据库表/列/迁移变更（纯验收特性）。
- ❌ Playwright Test 框架/页面对象模型/Allure 报告（FR 级排除，
  spec Key Entities）。
- ❌ 应用代码改动（R-08：发现的 bug 另开特性）。
- ❌ 直连数据库的种子工具（R-03：API 种子已覆盖且顺带验链路）。
