"""add project_ownership_received to notification_type (017)

One new enum value for「你成为了项目创建者」. No table / column change:
ownership transfer only rewrites ``projects.owner_id`` and the membership rows
(``data-model.md`` §1).

Why a migration is needed at all: ``notifications.type`` is a PostgreSQL enum,
not a varchar. The first draft of 017 assumed it was a string column and the
integration test failed with
``invalid input value for enum notification_type`` — worth remembering as a
"check the column type before claiming no migration is needed" lesson.

``ADD VALUE`` runs in an autocommit block for the same reason as
0011/0013/0014: PostgreSQL permits it inside a transaction, but the value
cannot be *used* in that same transaction. Downgrade keeps the value (an old
worker may still reference it).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_project_ownership_transfer"
down_revision: str | Sequence[str] | None = "0016_invite_code_revoked"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS "
            "'project_ownership_received'"
        )


def downgrade() -> None:
    # 枚举值不删：见模块 docstring（同 0011/0013/0014 纪律）。
    pass
