"""Project entity: projects table + requests.project_id/project_name (008).

One new table (``projects``) and two nullable columns on ``requests``
(``project_id`` FK with ON DELETE SET NULL, ``project_name`` snapshot).
No backfill: every existing request simply stays unclassified
(``project_id IS NULL``), which is exactly the semantics the feature
defines for "no project yet".

Downgrade drops the two columns and the table. The downgrade **destroys
project entities themselves** (and therefore the classifications) — it is
documented in the quickstart as the rollback surface, same discipline as
004/006: migrations must be backward compatible (nullable columns first),
and destructive downgrades are an explicit operator decision, not
something deploy.sh automates.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_project_entity"
down_revision: str | Sequence[str] | None = "0011_request_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. projects 表（需求的业务容器；单人归属，无成员表——research R-01）。
    op.create_table(
        "projects",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # 同用户名下唯一（含已归档项目：归档仍占名字，防历史归属混淆）。
        # owner_id 非空，普通唯一约束即可——不需要 005 那种部分唯一索引。
        sa.UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
    )
    op.create_index(
        "ix_projects_owner_status", "projects", ["owner_id", "status"]
    )

    # 2. requests 两列（全可空：存量需求保持未归类，行为零变化）。
    op.add_column(
        "requests",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_requests_project_id",
        "requests",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_requests_project_id", "requests", ["project_id"])
    op.add_column(
        "requests",
        sa.Column("project_name", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requests", "project_name")
    op.drop_index("ix_requests_project_id", table_name="requests")
    op.drop_constraint("fk_requests_project_id", "requests", type_="foreignkey")
    op.drop_column("requests", "project_id")
    op.drop_index("ix_projects_owner_status", table_name="projects")
    op.drop_table("projects")
