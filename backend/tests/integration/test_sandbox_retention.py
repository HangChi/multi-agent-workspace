"""沙箱执行数据保留策略测试。"""

from datetime import UTC, datetime, timedelta

from app.tasks.celery_app import celery_app
from app.tasks.maintenance import purge_expired_sandbox_data

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_purges_old_payloads_but_preserves_fingerprint_and_metadata(
    db_session, sandbox_factory
):
    """90 天前的代码和输出清空，但复现指纹及执行元数据保留。"""
    old = await sandbox_factory(status="failed", code="print('secret')", exit_code=7)
    old.created_at = NOW - timedelta(days=90, seconds=1)
    old.stdout = "stdout payload"
    old.stderr = "stderr payload"
    old.stdout_truncated = True
    old.stdout_full_bytes = 1234
    old.duration_ms = 321
    old.cpu_ms = 55
    old.mem_peak_bytes = 66
    old.error_category = "timeout"
    old.error_message = "execution timed out"
    old_image = old.image
    old_limits = old.limits
    fingerprint = old.code_sha256
    await db_session.commit()

    deleted = await purge_expired_sandbox_data(db_session, now=NOW)
    await db_session.commit()
    await db_session.refresh(old)

    assert deleted == 1
    assert old.code == ""
    assert old.stdout is None
    assert old.stderr is None
    assert old.code_sha256 == fingerprint
    assert old.status == "failed"
    assert old.exit_code == 7
    assert old.stdout_truncated is True
    assert old.stdout_full_bytes == 1234
    assert old.duration_ms == 321
    assert old.cpu_ms == 55
    assert old.mem_peak_bytes == 66
    assert old.error_category == "timeout"
    assert old.error_message == "execution timed out"
    assert old.image == old_image
    assert old.limits == old_limits


async def test_keeps_payloads_newer_than_retention_window(db_session, sandbox_factory):
    """保留窗口内的执行记录不受影响。"""
    fresh = await sandbox_factory(code="print('keep')")
    fresh.created_at = NOW - timedelta(days=90)
    fresh.stdout = "keep stdout"
    fresh.stderr = "keep stderr"
    await db_session.commit()

    deleted = await purge_expired_sandbox_data(db_session, now=NOW)
    await db_session.commit()
    await db_session.refresh(fresh)

    assert deleted == 0
    assert fresh.code == "print('keep')"
    assert fresh.stdout == "keep stdout"
    assert fresh.stderr == "keep stderr"


async def test_daily_retention_beat_is_registered():
    """保留任务由 Beat 每日调度。"""
    schedule = celery_app.conf.beat_schedule["purge-expired-sandbox-data"]

    assert schedule["task"] == "app.tasks.maintenance.purge_expired_sandbox_data"
    assert schedule["schedule"].minute == {0}
    assert schedule["schedule"].hour == {3}
