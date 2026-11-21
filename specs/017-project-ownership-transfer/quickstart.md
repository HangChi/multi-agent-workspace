# Quickstart：项目所有权移交（017）真实栈验收

前置：全栈已起（`docker compose -p aw -f deploy/docker-compose.yml up -d`），
`http://127.0.0.1:8080/api/v1/health` 返回 200，且有一个可用邀请码。

```bash
BASE=http://127.0.0.1:8080/api/v1
INVITE=$(docker exec aw-postgres psql -U aw -d agent_workbench -tA \
  -c "select code from invite_codes where revoked_at is null and used_count < max_uses limit 1;")
SUF=$(date +%s)
```

## 1. 造三个人（A 创建者 / B 接手人 / C 陌生人）

```bash
for w in a b c; do
  curl -s -X POST "$BASE/auth/register" -H 'content-type: application/json' \
    -d "{\"email\":\"t17-$w-$SUF@test.com\",\"password\":\"TestPass123!\",\"invite_code\":\"$INVITE\"}" \
    | jq -r ".tokens.access" > /tmp/t17_$w.tok
done
A=$(cat /tmp/t17_a.tok); B=$(cat /tmp/t17_b.tok); C=$(cat /tmp/t17_c.tok)
BID=$(curl -s "$BASE/auth/me" -H "authorization: Bearer $B" | jq -r .id)
echo "B 的 user id = $BID"
```

## 2. A 建项目 P 并把 B 加为成员

```bash
P=$(curl -s -X POST "$BASE/projects" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' -d "{\"name\":\"移交验收-$SUF\"}" | jq -r .id)
curl -s -X POST "$BASE/projects/$P/members" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' -d "{\"user_id\":\"$BID\"}" | jq -r '.is_owner'
# 塞一条需求，验证移交后需求归属不受影响
curl -s -X POST "$BASE/requests" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' -d "{\"title\":\"移交前需求-$SUF\",\"project_id\":\"$P\"}" > /dev/null
```

## 3. 陌生人 C 调移交 → 404「项目不存在」

```bash
curl -s -o /tmp/t17_c.json -w '%{http_code}\n' -X POST "$BASE/projects/$P/transfer" \
  -H "authorization: Bearer $C" -H 'content-type: application/json' \
  -d "{\"new_owner_id\":\"$BID\"}"
jq -r '.error.message' /tmp/t17_c.json   # 期望：项目不存在
```

## 4. A 自移交 → 409

```bash
AID=$(curl -s "$BASE/auth/me" -H "authorization: Bearer $A" | jq -r .id)
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE/projects/$P/transfer" \
  -H "authorization: Bearer $A" -H 'content-type: application/json' \
  -d "{\"new_owner_id\":\"$AID\"}"   # 期望 409
```

## 5. 正常移交（保留为成员）

```bash
curl -s -X POST "$BASE/projects/$P/transfer" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' \
  -d "{\"new_owner_id\":\"$BID\",\"keep_as_member\":true}" | jq '{owner_id, member_count}'
# 期望 owner_id == $BID，member_count == 1（只有 A）
```

核对四个不变式：

```bash
# ① B 的 mine 列表有 P，A 的 mine 没有
curl -s "$BASE/projects?scope=mine" -H "authorization: Bearer $B" | jq --arg p "$P" '.items[] | select(.id==$p) | .name'
curl -s "$BASE/projects?scope=mine" -H "authorization: Bearer $A" | jq --arg p "$P" '[.items[] | select(.id==$p)] | length'   # 0
# ② A 仍能看到（成员身份）：relevant 里有
curl -s "$BASE/projects?scope=relevant" -H "authorization: Bearer $A" | jq --arg p "$P" '[.items[] | select(.id==$p)] | length'  # 1
# ③ 成员列表只有 A，不含 B
curl -s "$BASE/projects/$P/members" -H "authorization: Bearer $B" | jq '[.items[] | .user_id]'
# ④ 需求仍在项目里
curl -s "$BASE/requests?project_id=$P" -H "authorization: Bearer $B" | jq '.total'
```

## 6. 权限真的换了手（四个 owner 动作）

```bash
# B 能改名
curl -s -X PATCH "$BASE/projects/$P" -H "authorization: Bearer $B" \
  -H 'content-type: application/json' -d "{\"name\":\"移交后-$SUF\"}" | jq -r .name
# A 不能改名 → 403
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH "$BASE/projects/$P" \
  -H "authorization: Bearer $A" -H 'content-type: application/json' -d '{"name":"x"}'
# A 不能管成员 → 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE/projects/$P/members" \
  -H "authorization: Bearer $A" -H 'content-type: application/json' -d "{\"user_id\":\"$BID\"}"
```

归档 / 删除不必在验收里真做（会销毁数据），由集成测试覆盖。

## 7. 同名冲突：归属不变

```bash
NAME="撞名-$SUF"
# B 先建一个同名项目
curl -s -X POST "$BASE/projects" -H "authorization: Bearer $B" \
  -H 'content-type: application/json' -d "{\"name\":\"$NAME\"}" > /dev/null
# A 也建一个同名，然后试图移交给 B
P2=$(curl -s -X POST "$BASE/projects" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' -d "{\"name\":\"$NAME\"}" | jq -r .id)
curl -s -o /tmp/t17_dup.json -w '%{http_code}\n' -X POST "$BASE/projects/$P2/transfer" \
  -H "authorization: Bearer $A" -H 'content-type: application/json' \
  -d "{\"new_owner_id\":\"$BID\"}"      # 期望 409
jq -r '.error.message' /tmp/t17_dup.json  # 期望带项目名的可读中文
# 归属没变：
curl -s "$BASE/projects/$P2" -H "authorization: Bearer $A" | jq -r .owner_id  # == $AID
```

## 8. 完全退出（keep_as_member=false）

```bash
curl -s -X POST "$BASE/projects/$P2/transfer" -H "authorization: Bearer $A" \
  -H 'content-type: application/json' -d "{\"new_owner_id\":\"$BID\",\"keep_as_member\":false}" > /dev/null
# 先给 P2 改名避免与 B 的同名项目冲突（第 7 步造的），若上一步 409 则先改名再试
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/projects/$P2" -H "authorization: Bearer $A"  # 期望 404
curl -s "$BASE/projects?scope=relevant" -H "authorization: Bearer $A" \
  | jq --arg p "$P2" '[.items[] | select(.id==$p)] | length'   # 期望 0
```

## 9. UI 与 e2e

- 浏览器：A 打开 `/projects` → 卡片「移交」→ 选 B → 确认 → 徽章由「我的」变「共享的」；
  B 打开 `/projects` → 徽章「我的」，点「改名」能成功。
- 自动化：`cd e2e && AW_E2E_INVITE=$INVITE ../backend/.venv/bin/python -m pytest -q`
  （新增 `tests/test_transfer.py` 3 例）。

## 10. 清理与回滚

- 清理验收数据：`backend/.venv/bin/python deploy/verify/cleanup_test_data.py`
  （先 dry-run，确认后加 `--execute`）。
- 代码回滚：直接回退提交，**不需要** alembic downgrade（本特性无迁移）。
- 数据回滚：已移交的项目不会自动退回。按 `audit_logs` 里
  `action='project_transfer'` 的 `from_user_id` / `to_user_id` 人工改回，
  并补/删对应成员行。
