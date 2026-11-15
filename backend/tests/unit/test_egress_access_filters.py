"""时间检索纯单测：不加载 DB fixtures、不建立数据库连接。"""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.errors import AppError
from app.services.egress_service import list_accesses


async def test_time_filters_are_shared_by_items_total_and_summary():
    start = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    end = datetime(2026, 1, 1, 1, tzinfo=UTC)
    results = [Mock(), Mock(), Mock(), Mock()]
    results[0].scalar_one.return_value = 2
    results[1].scalars.return_value.all.return_value = []
    results[2].all.return_value = [("allow", 2)]
    results[3].scalar_one.return_value = 1
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))

    rows, total, summary = await list_accesses(
        db, from_time=start, to_time=end, run_id="run-1", action="allow", page=3, page_size=1
    )

    assert rows == []
    assert total == 2
    assert summary == {"allow": 2, "deny": 0, "ip_direct": 1}
    assert db.execute.await_count == 4
    for call in db.execute.await_args_list:
        statement = call.args[0]
        compiled = statement.compile()
        sql = str(compiled)
        assert "egress_accesses.occurred_at >=" in sql
        assert "egress_accesses.occurred_at < " in sql
        assert compiled.params["occurred_at_1"] == start.astimezone(UTC)
        assert compiled.params["occurred_at_1"].tzinfo is UTC
        assert compiled.params["occurred_at_2"] == end
        assert "egress_accesses.run_id =" in sql
        assert "egress_accesses.action =" in sql


@pytest.mark.parametrize("start,end", [
    (datetime(2026, 1, 1), None),
    (None, datetime(2026, 1, 1)),
    (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
])
async def test_invalid_time_bounds_are_rejected_before_querying(start, end):
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(AppError):
        await list_accesses(db, from_time=start, to_time=end)
    db.execute.assert_not_awaited()
