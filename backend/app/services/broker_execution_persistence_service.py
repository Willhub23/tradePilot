from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.db_models import BrokerExecutionRecord
from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)

UNRESOLVED_STATUSES = (
    "prepared",
    "submitted",
    "pending",
    "unknown",
)


class BrokerExecutionPersistenceService:
    def save_request(
        self,
        request: BrokerExecutionRequest,
    ) -> None:
        now = datetime.now(timezone.utc)

        record = BrokerExecutionRecord(
            request_id=request.request_id,
            agent_decision_id=request.agent_decision_id,
            symbol=request.symbol,
            status="prepared",
            request_data=request.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )

        with SessionLocal() as db:
            db.add(record)
            db.commit()

    def update_result(
        self,
        result: BrokerExecutionResult,
    ) -> None:
        with SessionLocal() as db:
            record = db.get(
                BrokerExecutionRecord,
                result.request_id,
            )

            if record is None:
                raise ValueError(f"Execution request not found: {result.request_id}")

            record.status = result.status
            record.broker_order_id = result.broker_order_id
            record.broker_position_id = result.broker_position_id
            record.filled_volume_lots = result.filled_volume_lots
            record.fill_price = result.fill_price
            record.error_code = result.error_code
            record.error_message = result.error_message
            record.updated_at = datetime.now(timezone.utc)

            db.commit()

    def get_unresolved(
        self,
    ) -> list[BrokerExecutionRecord]:
        with SessionLocal() as db:
            records = db.scalars(
                select(BrokerExecutionRecord)
                .where(BrokerExecutionRecord.status.in_(UNRESOLVED_STATUSES))
                .order_by(BrokerExecutionRecord.created_at)
            ).all()

            return list(records)

    def find_execution(
        self,
        request_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerExecutionRecord | None:
        if not request_id and not broker_order_id:
            raise ValueError("request_id or broker_order_id is required")

        with SessionLocal() as db:
            if request_id:
                record = db.get(
                    BrokerExecutionRecord,
                    request_id,
                )
                if record is not None:
                    return record

            if broker_order_id:
                return db.scalar(
                    select(BrokerExecutionRecord).where(
                        BrokerExecutionRecord.broker_order_id == broker_order_id
                    )
                )

            return None
