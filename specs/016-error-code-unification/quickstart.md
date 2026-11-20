# Quickstart: 016 错误码语义统一

## 1. 自动化验证

```bash
cd backend
# 只跑本特性的用例
DATABASE_URL="postgresql+asyncpg://aw:test-password-123@127.0.0.1:5432/agent_workbench_test" \
  ./.venv/bin/python -m pytest -q tests/integration/test_error_code_unification.py

# 全量 + 覆盖率门禁
DATABASE_URL="postgresql+asyncpg://aw:test-password-123@127.0.0.1:5432/agent_workbench_test" \
  ./.venv/bin/python -m pytest -q --cov=app --cov-report=term-missing:skip-covered

# 静态检查
./.venv/bin/python -m ruff check app tests && ./.venv/bin/python -m ruff format --check app tests
./.venv/bin/python -m mypy app
```

期望：`test_error_code_unification.py` 9 passed；全量无 failed；覆盖率 ≥ 85%。

## 2. 手工验证（真实栈，两个账号）

前置：`docker compose -p aw -f deploy/docker-compose.yml up -d`，前端 8080。

用 A 建一条需求（记下 id），用 B 的 token 验证：

```bash
API=http://127.0.0.1:8000/api/v1

# B 访问 A 的需求 → 404
curl -s -o /tmp/foreign.json -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN_B" "$API/requests/$REQ_ID"

# B 访问一个不存在的 id → 404
curl -s -o /tmp/missing.json -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN_B" "$API/requests/$(uuidgen | tr 'A-Z' 'a-z')"

# 两个响应体必须完全一致
diff <(jq -S . /tmp/foreign.json) <(jq -S . /tmp/missing.json) && echo "同构 OK"
```

对照 A（owner）访问自己的需求必须是 200。

## 3. 白名单回归（必须仍是 403）

```bash
# viewer 创建需求 → 403（角色不足）
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/requests" \
  -H "Authorization: Bearer $VIEWER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"x","description":"y","expected_output":"z","priority":"normal"}'
# 期望 403，不是 404
```

内置模板编辑、项目成员管理成员、admin 改他人调度同理——见
[contracts/api.md §4](./contracts/api.md) 的白名单表。

## 4. 前端回归

```bash
cd frontend
npx tsc --noEmit && npx eslint src --ext .ts,.tsx && npx vitest run && npx vite build
```

前端不改逻辑，但要看错误提示是否还合理：B 打开 A 的需求链接时，
ErrorBanner 应显示「需求不存在」而不是「无权限」。

## 5. 验收判据

| 项 | 判据 |
|---|---|
| 陌生人 → 404 | `test_error_code_unification.py` 全绿 |
| 响应体同构 | 上面 `diff` 无输出 |
| 白名单仍 403 | viewer / 成员 / 内置模板用例绿 |
| 无回归 | 全量 pytest 无 failed，覆盖率 ≥85% |
