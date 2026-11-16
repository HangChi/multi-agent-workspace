"""Index artifact ownership lookups (run detail and artifact download).

`artifacts.run_id` and `artifacts.step_id` have never been indexed since 0001,
while `GET /runs/{id}` loads a run's artifacts on every poll and the artifact
download path resolves a single row. On the 002 acceptance workload (20
concurrent run polls plus the measured core API sampler) the run-detail
artifacts query therefore degrades to a sequential scan, spending API and
database time that the measured core API latency has to share.

The index names match the model's ``__table_args__`` so a future autogenerate
sees no drift. Downgrade only removes the indexes; artifact rows are untouched.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_artifact_run_step_index"
down_revision: str | Sequence[str] | None = "0005_external_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_artifacts_run"), "artifacts", ["run_id"], unique=False)
    op.create_index(op.f("ix_artifacts_step"), "artifacts", ["step_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_artifacts_step"), table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_run"), table_name="artifacts")
