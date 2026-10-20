from datetime import datetime, timezone
import threading

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.db_models import BrokerExecutionRecord, BrokerPositionCloseRecord
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


# Also serialize writers in SQLite tests; PostgreSQL row locks protect other processes.
_RESULT_LOCK = threading.RLock()


class BrokerExecutionPersistenceService:
    _result_fields = ("status", "broker_order_id", "broker_position_id",
                      "filled_volume_lots", "fill_price", "error_code", "error_message")

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
        # Both immediate responses and asynchronous events use this merge. A late
        # acceptance/timeout must never erase an already confirmed execution.
        with _RESULT_LOCK, SessionLocal() as db:
            record = db.get(BrokerExecutionRecord, result.request_id, with_for_update=True)
            if record is None:
                raise ValueError(f"Execution request not found: {result.request_id}")

            before = tuple(getattr(record, field) for field in self._result_fields)
            for field in ("broker_order_id", "broker_position_id"):
                incoming = getattr(result, field)
                existing = getattr(record, field)
                if incoming and existing and incoming != existing:
                    raise ValueError(f"Conflicting {field} for {result.request_id}")
                if incoming:
                    setattr(record, field, incoming)

            previous_status = record.status
            previous_volume = record.filled_volume_lots
            incoming_volume = result.filled_volume_lots
            has_partial_fill = previous_status == "pending" or (
                previous_volume is not None and previous_volume > 0
            )
            if previous_status == "filled":
                pass
            elif result.status == "filled":
                record.status = "filled"
            elif result.status == "pending":
                if previous_status not in {"rejected", "cancelled"} or (
                    incoming_volume is not None and incoming_volume > 0
                ):
                    record.status = "pending"
            elif result.status in {"rejected", "cancelled"} and has_partial_fill:
                # A rejected remainder is not proof that the filled exposure vanished.
                record.status = "pending"
            elif previous_status not in {"rejected", "cancelled"}:
                ranks = {"prepared": 0, "unknown": 1, "submitted": 2, "pending": 3, "rejected": 4, "cancelled": 4}
                if ranks[result.status] >= ranks[previous_status]:
                    record.status = result.status

            # Never sum deal volumes. A later cumulative fill can enrich the
            # immediate response, which may contain only the final deal's size.
            volume_advances = incoming_volume is not None and (
                previous_volume is None or incoming_volume > previous_volume
            )
            can_enrich_terminal = (
                previous_status != "filled"
                or previous_volume is None
                or result.status == "filled"
            )
            if volume_advances and can_enrich_terminal:
                record.filled_volume_lots = incoming_volume
                if result.fill_price is not None and (
                    previous_status != "filled"
                    or record.fill_price is None
                    or result.status == "filled"
                ):
                    record.fill_price = result.fill_price
            elif result.fill_price is not None and (
                record.fill_price is None
                or (
                    previous_status != "filled" and result.status == "filled"
                    and (incoming_volume is None or previous_volume is None
                         or incoming_volume >= previous_volume)
                )
            ):
                record.fill_price = result.fill_price

            for field in ("error_code", "error_message"):
                incoming = getattr(result, field)
                if incoming:
                    setattr(record, field, incoming)
            tracking_changed = False
            if record.request_data.get("opportunity_id") and result.status == "filled":
                data = dict(record.request_data)
                tracking = dict(data.get("execution_tracking", {}))
                original = dict(tracking)
                if result.broker_event_at is not None:
                    tracking.setdefault("broker_fill_event_at", result.broker_event_at.isoformat())
                    if result.broker_event_time_source is not None:
                        tracking.setdefault("broker_event_time_source", result.broker_event_time_source)
                first_receipt = "fill_confirmation_received_at" not in tracking
                earlier_receipt = (result.receipt_process_id is not None
                    and tracking.get("receipt_process_id") == result.receipt_process_id
                    and result.receipt_monotonic_ns is not None
                    and tracking.get("receipt_monotonic_ns") is not None
                    and result.receipt_monotonic_ns < tracking["receipt_monotonic_ns"])
                if result.received_at is not None and (first_receipt or earlier_receipt):
                    tracking["fill_confirmation_received_at"] = result.received_at.isoformat()
                    tracking["receipt_process_id"] = result.receipt_process_id
                    tracking["receipt_monotonic_ns"] = result.receipt_monotonic_ns
                    from app.services.execution_timing import PROCESS_ID
                    timing = data.get("timing") or {}
                    if (timing.get("process_id") == PROCESS_ID == result.receipt_process_id
                            and result.receipt_monotonic_ns is not None
                            and timing.get("submission_monotonic_ns") is not None):
                        duration = result.receipt_monotonic_ns - timing["submission_monotonic_ns"]
                        if duration >= 0:
                            tracking["submission_to_fill_confirmation_ms"] = duration / 1e6
                if tracking != original:
                    data["execution_tracking"] = tracking
                    record.request_data = data
                    tracking_changed = True
            if before == tuple(getattr(record, field) for field in self._result_fields) and not tracking_changed:
                return
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
                # Ambiguous identifiers must fail rather than select an arbitrary row.
                return db.scalars(
                    select(BrokerExecutionRecord).where(
                        BrokerExecutionRecord.broker_order_id == broker_order_id
                    )
                ).one_or_none()

            return None

    def find_position_execution(self, position_id):
        with SessionLocal() as db:
            # Multiple executions/netted positions do not have a single holding policy.
            return db.scalars(select(BrokerExecutionRecord).where(
                BrokerExecutionRecord.broker_position_id == str(position_id),
            )).one_or_none()

    def get_close(self, account_id, position_id):
        with SessionLocal() as db:
            return db.get(BrokerPositionCloseRecord, (str(account_id), str(position_id)))

    def get_unresolved_closes(self, account_id):
        with SessionLocal() as db:
            return list(db.scalars(select(BrokerPositionCloseRecord).where(
                BrokerPositionCloseRecord.account_id == str(account_id),
                BrokerPositionCloseRecord.status.in_(("prepared", "submitted", "unknown")),
            )).all())

    def claim_close(self, account_id, position_id, volume):
        """Commit before sending. The composite PK serializes concurrent processes."""
        now = datetime.now(timezone.utc)
        with _RESULT_LOCK, SessionLocal() as db:
            db.add(BrokerPositionCloseRecord(
                account_id=str(account_id), broker_position_id=str(position_id),
                volume=volume, status="prepared", created_at=now, updated_at=now,
            ))
            try:
                db.commit()
                return True
            except IntegrityError:
                db.rollback()
                if db.get(BrokerPositionCloseRecord, (str(account_id), str(position_id))) is None:
                    raise
                return False

    def update_close(self, account_id, result):
        with _RESULT_LOCK, SessionLocal() as db:
            row = db.get(BrokerPositionCloseRecord,
                         (str(account_id), result.broker_position_id), with_for_update=True)
            if row is None:
                raise ValueError("Close attempt not found")
            before = (row.status, row.broker_order_id, row.message)
            if result.broker_order_id:
                if row.broker_order_id and row.broker_order_id != result.broker_order_id:
                    raise ValueError("Conflicting closing order ID")
                row.broker_order_id = result.broker_order_id
            if result.status == "closed" or row.status not in ("closed", "rejected"):
                # Late timeouts cannot overwrite broker acceptance.
                if not (row.status == "submitted" and result.status == "unknown"):
                    row.status = result.status
            if result.message and row.status != "closed":
                row.message = result.message
            if before != (row.status, row.broker_order_id, row.message):
                row.updated_at = datetime.now(timezone.utc)
                db.commit()
