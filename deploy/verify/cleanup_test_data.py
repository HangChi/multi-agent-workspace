"""清理验收脚本攒下的测试账号（014 配套工具）。

背景：s1–s12 每个验收脚本都用「注册新用户」做种子（随机后缀防撞名），
但跑完从不清理——用户表被撑到 303，其中 302 是测试账号，看板的
「拖到目标」面板被淹没得没法用（见 014 的 UI 优化）。

安全设计：
- **dry-run 默认**：只打印将删什么，`--execute` 才真删；
- 删除范围按邮箱前缀白名单识别（w-/dep-/disp-/verify-/proj-/share-/strategy-），
  保护名单（默认 owner@example.com）永远不碰；
- **两阶段删除**（前两版的教训）：
  ① 污染传播——从受害用户沿 FK 图向下算每张表的「污染行 id 集」
     （用户 → 其项目 → 项目成员行 → …）。只按用户列删会漏掉
     group_members 这类没有用户列的中间表（第二版就这样回滚过）；
  ② 按子 → 父序删——FK 约束要求孩子先删；
- `users.invite_code_id → invite_codes` 与 `invite_codes.created_by → users`
  成环：先把受害者该列置 NULL 破环；保留用户若引用受害者创建的码，
  删码时排除仍被引用的；
- 全程一个事务：任何一步失败整体回滚，不留半清状态。

用法：
    backend/.venv/bin/python deploy/verify/cleanup_test_data.py            # dry-run
    backend/.venv/bin/python deploy/verify/cleanup_test_data.py --execute  # 真删
"""

import argparse
import asyncio

import asyncpg

DEFAULT_PATTERNS = [
    "w-%",
    "dep-%",
    "disp-%",
    "verify-%",
    "proj-%",
    "share-%",
    "strategy-%",
]
DEFAULT_PROTECTED = ["owner@example.com"]
DSN = "postgresql://aw:test-password-123@127.0.0.1:5432/agent_workbench"


async def fk_edges(conn: asyncpg.Connection) -> list[tuple[str, str, str]]:
    """全部外键边：(子表, 子表列, 父表)。"""
    rows = await conn.fetch(
        """
        SELECT tc.table_name AS child, kcu.column_name AS col, ccu.table_name AS parent
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
        """
    )
    return [(r["child"], r["col"], r["parent"]) for r in rows]


async def primary_key(conn: asyncpg.Connection, table: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public' AND tc.table_name = $1
        LIMIT 1
        """,
        table,
    )
    return row["column_name"] if row else None


async def main() -> int:
    parser = argparse.ArgumentParser(description="清理验收脚本攒下的测试账号")
    parser.add_argument("--execute", action="store_true", help="真删（默认 dry-run）")
    parser.add_argument(
        "--pattern", action="append", default=None, help="邮箱 LIKE 模式（可多次）"
    )
    parser.add_argument(
        "--protect", action="append", default=None, help="保护名单（可多次）"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="命中账号拥有过半需求时仍然执行（预检默认中止）",
    )
    args = parser.parse_args()

    patterns = args.pattern or DEFAULT_PATTERNS
    protected = args.protect or DEFAULT_PROTECTED

    conn = await asyncpg.connect(DSN)
    try:
        where = " OR ".join(f"email LIKE ${i + 1}" for i in range(len(patterns)))
        params: list = list(patterns)
        if protected:
            where += f" AND email <> ALL(${len(params) + 1}::text[])"
            params.append(protected)
        victims = await conn.fetch(
            f"SELECT id, email FROM users WHERE {where}", *params
        )
        print(f"命中测试账号：{len(victims)} 个（模式 {patterns}，保护 {protected}）")
        if not victims:
            return 0
        for v in victims[:3]:
            print(f"  - {v['email']}")
        if len(victims) > 3:
            print(f"  …另 {len(victims) - 3} 个")
        victim_ids = [v["id"] for v in victims]

        edges = await fk_edges(conn)
        # 子表 → [(列, 父表), ...]；破环：去掉 users → invite_codes 边
        # （users.invite_code_id → invite_codes.id 与 created_by → users 成环，
        #  执行阶段会把受害者该列置 NULL，这里同步断边让污染传播可终止）
        refs: dict[str, list[tuple[str, str]]] = {}
        for child, col, parent in edges:
            if child == "users" and parent == "invite_codes":
                continue  # 破环（见模块 docstring）
            if child == parent:
                continue  # 自引用（runs.parent_run_id / agents.source_agent_id）——
                # 删行即删关系，不参与排序；实测无「保留行引用受害行」的边案
            refs.setdefault(child, []).append((col, parent))

        # ---------- 阶段一：污染传播（不动点迭代） ----------
        # 为什么不用 BFS 分层：FK 图里同层可以有边（runs.owner_id → users 与
        # runs.request_id → requests，两张表都与 users 直连），分层会漏算；
        # 破环后图是 DAG，迭代到不动点即收敛（第三版的教训）。
        all_tables = sorted({c for c, _col, _p in edges} | {"users"})
        pks: dict[str, str | None] = {}
        for table in all_tables:
            pks[table] = await primary_key(conn, table)
        tainted: dict[str, set] = {t: set() for t in all_tables}
        tainted["users"] = set(victim_ids)
        for _round in range(len(all_tables) + 1):
            changed = False
            for table in all_tables:
                if table == "users" or pks[table] is None:
                    continue
                conds, q_params = [], []
                for col, parent in refs.get(table, []):
                    if tainted.get(parent):
                        conds.append(f'"{col}" = ANY(${len(q_params) + 1}::uuid[])')
                        q_params.append(list(tainted[parent]))
                if not conds:
                    continue
                rows = await conn.fetch(
                    f'SELECT "{pks[table]}" AS id FROM "{table}" WHERE {" OR ".join(conds)}',
                    *q_params,
                )
                fresh = {r["id"] for r in rows}
                if fresh != tainted[table]:
                    tainted[table] = fresh
                    changed = True
            if not changed:
                break
        else:
            raise RuntimeError("污染传播未收敛（FK 图可能有环未破）")

        # ---------- 阶段二：真拓扑排序（子先）后删 ----------
        non_empty = {t for t in tainted if tainted[t] and t != "users"}
        indeg = {t: 0 for t in non_empty}
        for child in non_empty:
            for _col, parent in refs.get(child, []):
                if parent in non_empty:
                    indeg[parent] += 1  # 被引用一次 = 删它前要先删一个孩子
        queue = sorted(t for t, d in indeg.items() if d == 0)
        delete_order: list[str] = []
        while queue:
            t = queue.pop(0)
            delete_order.append(t)
            for _col, parent in sorted(refs.get(t, [])):
                if parent in indeg:
                    indeg[parent] -= 1
                    if indeg[parent] == 0:
                        queue.append(parent)
            queue.sort()
        if len(delete_order) != len(non_empty):
            raise RuntimeError(
                f"删除序不完整，疑似有环：{non_empty - set(delete_order)}"
            )
        print(f"污染传播涉及 {len(non_empty) + 1} 张表；删除序（子先）：")
        print("  " + " → ".join(delete_order + ["users"]))

        # 预检：命中面过大时先停一下。
        # 2026-11-19 实测踩到：演示/联调数据都建在 w-owner 这类带前缀的账号下，
        # 而本脚本按邮箱前缀认「测试账号」——于是 --execute 会把整套演示数据
        # （50 需求 / 20 运行 / 13 Agent / 8 项目）一起删掉，看起来却像「清理
        # 了几百行垃圾」。命中需求过半时强制要求 --force，把「我确实要删」变成
        # 一次显式表态，而不是被 dry-run 的表格骗过去。
        owned = await conn.fetchval(
            "SELECT count(*) FROM requests WHERE owner_id = ANY($1::uuid[])", victim_ids
        )
        total_requests = await conn.fetchval("SELECT count(*) FROM requests") or 0
        if total_requests and owned / total_requests >= 0.5:
            print(
                f"\n⚠️  预检：命中账号拥有 {owned}/{total_requests} 条需求"
                f"（{owned / total_requests:.0%}），看起来不是「零星测试数据」。"
            )
            if args.execute and not args.force:
                print(
                    "   已中止，未删除任何行。确认要连这些数据一起删，请加 --force；"
                    "\n   只是想清 s12 等脚本产生的账号，用 --pattern 缩小前缀。"
                )
                return 1
            print("   （dry-run 继续打印将删内容，供你判断）")

        total = 0
        async with conn.transaction():
            if args.execute:
                await conn.execute(
                    "UPDATE users SET invite_code_id = NULL WHERE id = ANY($1::uuid[])",
                    victim_ids,
                )

                # ---------- 破环：把「待删的码」的 created_by 转给保留用户 ----------
                # `users.invite_code_id → invite_codes.id` 与
                # `invite_codes.created_by → users.id` 是一个二元环。只把受害者的
                # invite_code_id 置 NULL 只破了一半。
                #
                # 实测（2026-11-22）：VERIFY-012-MEMBER 由 owner@example.com 创建，
                # 却被 90 个**同样待删**的账号使用。删码那一步因为「仍被用户引用」
                # 排除了它；等删 users 时它还在，created_by 指向待删用户 → FK 违例，
                # 整笔事务回滚（两次都是这个错，数据无损但白跑）。
                #
                # 解法：在删用户之前，把「created_by 是待删用户」的码转交给一个
                # 保留用户。码因此活下来（那些账号本来也是用这个码注册的，语义上
                # 更合理），created_by 也不再悬空。
                keeper = await conn.fetchval(
                    "SELECT id FROM users WHERE NOT (id = ANY($1::uuid[])) ORDER BY created_at LIMIT 1",
                    victim_ids,
                )
                if keeper:
                    moved = await conn.fetchval(
                        "WITH moved AS ("
                        "  UPDATE invite_codes SET created_by = $2"
                        "  WHERE created_by = ANY($1::uuid[]) RETURNING 1"
                        ") SELECT count(*) FROM moved",
                        victim_ids,
                        keeper,
                    )
                    if moved:
                        print(f"  invite_codes（created_by 转交保留用户）: {moved} 行")

            for table in delete_order:
                pk = await primary_key(conn, table)
                ids = list(tainted[table])
                extra = ""
                if table == "invite_codes":
                    extra = (
                        " AND id NOT IN (SELECT invite_code_id FROM users"
                        " WHERE invite_code_id IS NOT NULL)"
                    )
                n = await conn.fetchval(
                    f'SELECT count(*) FROM "{table}" WHERE "{pk}" = ANY($1::uuid[]){extra}',
                    ids,
                )
                if n == 0:
                    continue
                print(
                    f"  {table}: {n} 行" + ("" if args.execute else "（dry-run，不删）")
                )
                total += n
                if args.execute:
                    await conn.execute(
                        f'DELETE FROM "{table}" WHERE "{pk}" = ANY($1::uuid[]){extra}',
                        ids,
                    )
            if args.execute:
                n_users = await conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", victim_ids
                )
                await conn.execute(
                    "DELETE FROM users WHERE id = ANY($1::uuid[])", victim_ids
                )
                print(f"  users: {n_users} 行")
                total += n_users
        print(
            f"\n合计触及 {total} 行"
            + ("，已删除。" if args.execute else "（dry-run）。")
        )
        if args.execute:
            print(f"剩余用户：{await conn.fetchval('SELECT count(*) FROM users')}")
            for t in ("requests", "projects", "agents", "runs", "audit_logs"):
                print(f"  剩余 {t}: {await conn.fetchval(f'SELECT count(*) FROM {t}')}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
