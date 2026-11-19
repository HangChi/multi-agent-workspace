"""Request board indexes: owner+created_at (015 优化).

看板默认 scope=relevant 的查询形状是
``WHERE (owner_id = :me OR assignee_id = :me) AND ... ORDER BY created_at DESC
LIMIT 20``。

先说清楚现状（避免误判）：``owner_id`` **已有**单列索引
``ix_requests_owner_id``（001 建表时以 index=True 声明），分派侧也有
``ix_requests_assignee_status``。所以这条查询不存在「全表扫」问题。

真正的代价在**排序**：只有单列 owner_id 索引时，``scope=mine`` 那一支
（``WHERE owner_id = :me ORDER BY created_at DESC LIMIT 20``）会先把该用户
**全部**需求取出来再排序，只为了取前 20 条。活跃用户积累几千条之后，每次
翻页都要排一次全量。

``(owner_id, created_at DESC)`` 把等值列放前、排序列放后，这条查询退化成
一次索引扫描取前 20 行，不再排序。scope=relevant 的 OR 形态仍需要合并
两个分支后再排序（PostgreSQL 无法用单个复合索引消掉 OR 上的排序），
所以本索引的收益主要落在「我创建的」范围切换上——这也是看板最常用的视图。

不加 (status, created_at)：状态过滤是可选的、选择性低（一张看板本来就
分散在 8 个状态里），维护成本大于收益。
不加 (project_id, created_at)：008 已有 project_id 单列索引，单项目需求量级
远小于全局，排序开销可接受。

索引同时声明在 app/models/request.py 的 __table_args__ 里——测试库走
create_all，只在迁移里加会让 prod/test 静默分叉。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_request_board_indexes"
down_revision: str | Sequence[str] | None = "0014_project_member"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_requests_owner_created",
        "requests",
        ["owner_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_requests_owner_created", table_name="requests")
