import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import audit_guard
from app.db import SessionLocal, engine


@pytest.fixture
async def guarded():
    async with engine.begin() as c:
        for stmt in audit_guard.CREATE:
            await c.exec_driver_sql(stmt)
    yield
    async with engine.begin() as c:
        for stmt in audit_guard.DROP:
            await c.exec_driver_sql(stmt)


async def test_audit_rows_can_be_added_but_not_changed_or_removed(guarded):
    async with SessionLocal() as db:
        await db.execute(text("INSERT INTO audit_log (at, actor_type, action) VALUES (now(), 'system', 'test.event')"))
        await db.commit()
    for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        async with SessionLocal() as db:
            with pytest.raises(DBAPIError, match="append-only"):
                await db.execute(text(sql))
                await db.commit()
    async with SessionLocal() as db:
        assert (await db.execute(text("SELECT count(*) FROM audit_log"))).scalar() == 1
