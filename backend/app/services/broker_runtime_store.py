from datetime import datetime, timezone

from sqlalchemy import text
from app.database import engine, SessionLocal
from app.db_models import BrokerRuntimeState


class BrokerRuntimeStore:
    """Durable safety latch and a PostgreSQL session lock for one serving owner."""

    LOCK_ID = 741852963

    def __init__(self, account_id):
        self.account_id = str(account_id)
        self.connection = None
        self.backend_pid = None

    def acquire(self):
        if self.connection is not None:
            raise RuntimeError("Runtime already owns the database lease")
        if engine.dialect.name != "postgresql":
            raise RuntimeError("cTrader runtime requires PostgreSQL")
        connection = engine.connect()
        try:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self.LOCK_ID}
            )
            if not acquired:
                raise RuntimeError(
                    "Another TradePilot runtime already owns this database"
                )
            self.backend_pid = connection.scalar(text("SELECT pg_backend_pid()"))
            connection.commit()
            self.connection = connection
        except Exception:
            # A session-level advisory lock must never leak back into the pool.
            connection.invalidate()
            connection.close()
            raise

    def assert_owner(self):
        if self.connection is None or self.connection.invalidated:
            raise RuntimeError("Runtime database ownership lost")
        pid = self.connection.scalar(text("SELECT pg_backend_pid()"))
        self.connection.commit()
        if pid != self.backend_pid:
            raise RuntimeError("Runtime database session changed; restart required")

    def release(self):
        if self.connection is not None:
            try:
                if not self.connection.invalidated:
                    self.connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": self.LOCK_ID}
                    )
                    self.connection.commit()
            except Exception:
                self.connection.invalidate()
                raise
            finally:
                self.connection.close()
                self.connection = None

    def load(self):
        with SessionLocal() as db:
            row = db.get(BrokerRuntimeState, self.account_id)
            if row is None:
                row = BrokerRuntimeState(
                    account_id=self.account_id,
                    paused=True,
                    critical_error=None,
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
            return {"paused": row.paused, "critical_error": row.critical_error}

    def save(self, paused, critical_error):
        with SessionLocal() as db:
            row = db.get(BrokerRuntimeState, self.account_id, with_for_update=True)
            if row is None:
                raise RuntimeError("Runtime safety state is missing")
            row.paused = paused
            row.critical_error = critical_error
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
