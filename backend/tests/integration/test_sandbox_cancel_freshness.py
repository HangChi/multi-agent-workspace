"""Cancellation must observe another transaction, not Worker identity-map cache."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run
from app.tasks.execute_run import _is_cancelled


async def test_cancellation_poll_reads_external_commit_without_overwriting_local_run(
    db_session, sandbox_factory
):
    execution = await sandbox_factory()
    cached = await db_session.get(Run, execution.run_id)
    assert cached.status == "running"
    assert not await _is_cancelled(db_session, cached.id)
    async with AsyncSession(bind=db_session.bind, expire_on_commit=False) as cancelling:
        await cancelling.execute(
            update(Run).where(Run.id == cached.id).values(status="cancelled")
        )
        await cancelling.commit()
    assert cached.status == "running", "test requires a stale ORM identity-map entry"
    assert await _is_cancelled(db_session, cached.id)
    assert cached.status == "running", (
        "poll must not overwrite unrelated unflushed run data"
    )
