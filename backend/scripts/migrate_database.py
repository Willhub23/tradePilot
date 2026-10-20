"""Idempotent additive migrations. Never creates/replaces existing trading tables."""

import argparse
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from dotenv import load_dotenv


def _tables(include_strategy=True):
    from app.db_models import (
        BrokerPositionCloseRecord,
        BrokerRuntimeState,
        StrategyEvaluationRecord,
    )

    return (BrokerPositionCloseRecord.__table__, BrokerRuntimeState.__table__) + (
        (StrategyEvaluationRecord.__table__,) if include_strategy else ()
    )


def verify_table(connection, table):
    inspector = inspect(connection)
    if not inspector.has_table(table.name):
        raise RuntimeError(f"Missing table {table.name}; run the migration command")
    actual = {column["name"]: column for column in inspector.get_columns(table.name)}
    for expected in table.columns:
        column = actual.get(expected.name)
        if (
            column is None
            or column["type"]._type_affinity != expected.type._type_affinity
        ):
            raise RuntimeError(f"Incompatible column {table.name}.{expected.name}")
        if column["nullable"] != expected.nullable:
            raise RuntimeError(f"Incompatible nullability {table.name}.{expected.name}")
        if connection.dialect.name == "postgresql" and hasattr(
            expected.type, "timezone"
        ):
            if column["type"].timezone != expected.type.timezone:
                raise RuntimeError(
                    f"Incompatible timezone {table.name}.{expected.name}"
                )
    actual_pk = set(inspector.get_pk_constraint(table.name)["constrained_columns"])
    if actual_pk != {c.name for c in table.primary_key}:
        raise RuntimeError(f"Incompatible primary key {table.name}")


def verify_database(engine, require_strategy=None):
    if require_strategy is None:
        import os

        require_strategy = os.getenv("TRADING_STRATEGY", "existing") == "quick_trade"
    with engine.connect() as connection:
        for table in _tables(include_strategy=require_strategy):
            verify_table(connection, table)
        inspector = inspect(connection)
        for name in ("agent_decisions", "paper_trades", "broker_executions"):
            if not inspector.has_table(name):
                raise RuntimeError(f"Missing existing application table: {name}")


def migrate_database(engine):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Migration command requires PostgreSQL")
    with engine.begin() as connection:
        if not connection.scalar(text("SELECT pg_try_advisory_xact_lock(741852963)")):
            raise RuntimeError("Stop the owning TradePilot runtime before migrating")
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        for table in _tables():
            if inspect(connection).has_table(table.name):
                verify_table(connection, table)
        root = Path(__file__).resolve().parents[1] / "migrations"
        for name in (
            "001_broker_position_closes.sql",
            "002_broker_runtime_state.sql",
            "003_strategy_evaluations.sql",
        ):
            connection.execute(text((root / name).read_text()))
        for table in _tables():
            verify_table(connection, table)
    verify_database(engine, require_strategy=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply additive migrations in one transaction",
    )
    args = parser.parse_args()
    load_dotenv(args.env_file)
    import os

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(url)
    try:
        if args.apply:
            migrate_database(engine)
        else:
            verify_database(engine, require_strategy=True)
        print(
            "Verified broker_position_closes, broker_runtime_state and strategy_evaluations; existing trading tables preserved."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
