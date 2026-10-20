import logging
import threading
from contextlib import contextmanager

from app.services.broker_runtime_store import BrokerRuntimeStore

logger = logging.getLogger(__name__)


class CTraderRuntimeService:
    """Serializes recovery, management, manual cycles and scheduled submissions."""

    def __init__(self, client, provider, persistence, store=None, *, observation_enabled=False):
        self._observation_enabled = observation_enabled
        self.client, self.provider, self.persistence = client, provider, persistence
        self.store = store or BrokerRuntimeStore(client.account_id)
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.pending_alert = threading.Event()
        self.pause_requested = threading.Event()
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.paused = True
        self.safety_epoch = 0
        self.critical_error = None
        self.last_error = None
        self.recovered_generation = None
        self.position_reports = []
        self.worker = None
        self.started = False
        provider.runtime = self
        provider.position_management.safety_callback = self.critical
        client.set_tracking_error_handler(self.critical)
        client.state_listener = self.connection_changed
        client.order_guard = self.order_allowed
        client.close_guard = self.close_allowed

    @property
    def observation_enabled(self):
        return self._observation_enabled

    def observation_block_reason(self):
        # Entry pause remains intact; only read-only collection bypasses that gate.
        if self.stopping.is_set() or not self.started:
            return "runtime_stopped"
        if not self.client.authenticated:
            return "broker_disconnected"
        if self.critical_error or self.pending_alert.is_set():
            return "critical_unsafe_state"
        if not self.client.demo_verified:
            return "broker_not_verified_demo"
        if not self.ready.is_set() or self.recovered_generation != self.client.generation:
            return "broker_recovering"
        return None

    def connection_changed(self):
        # Called by the reactor: do not wait for locks or touch the DB here.
        self.ready.clear()
        self.wake.set()

    def order_allowed(self):
        return (
            not self.observation_enabled
            and self.started
            and not self.stopping.is_set()
            and self.ready.is_set()
            and self.client.authenticated
            and self.client.demo_verified
            and self.recovered_generation == self.client.generation
            and not self.paused
            and not self.pause_requested.is_set()
            and self.critical_error is None
            and not self.pending_alert.is_set()
        )

    def close_allowed(self):
        return (
            not self.observation_enabled
            and self.started
            and not self.stopping.is_set()
            and self.ready.is_set()
            and self.client.authenticated
            and self.client.demo_verified
            and self.recovered_generation == self.client.generation
            and self.critical_error is None
            and not self.pending_alert.is_set()
        )

    def block_reason(self):
        if self.stopping.is_set() or not self.started:
            return "runtime_stopped"
        if not self.client.authenticated:
            return "broker_disconnected"
        if self.critical_error:
            return "critical_unsafe_state"
        if not self.ready.is_set():
            return "broker_recovering"
        if self.observation_enabled:
            return "observation_only"
        if self.paused or self.pause_requested.is_set():
            return "trading_paused"
        if not self.order_allowed():
            return "broker_not_ready"
        return None

    @contextmanager
    def operation(self):
        with self.lock:
            yield

    def critical(self, message):
        self.ready.clear()
        self.pending_alert.set()
        self.safety_epoch += 1
        message = str(message)
        self.critical_error = message
        logger.critical(
            "cTrader safety latch account=%s reason=%s", self.client.account_id, message
        )
        with self.lock:
            # An operator resume waiting on this lock must not erase a newer alert.
            self.critical_error = message
            self.store.save(self.paused, message)
            self.pending_alert.clear()

    def _inspect(self, allow_close=False):
        pending = self.provider.get_pending_order_ids()
        if pending:
            self.critical(f"Unresolved broker pending orders: {pending}")
        reports = self.provider.position_management.manage(
            allow_close=allow_close and not pending
        )
        self.position_reports = [r.model_dump(mode="json") for r in reports]
        logger.info(
            "Broker position verification account=%s reports=%s",
            self.client.account_id,
            self.position_reports,
        )
        for report in reports:
            if report.protection in ("unsafe", "unconfirmed"):
                if (
                    report.broker_position_id
                    or report.message == "Unsupported account exposure"
                ):
                    self.critical(
                        report.message or "Broker protection cannot be confirmed"
                    )
                raise RuntimeError(report.message or "Broker state cannot be confirmed")
        if pending:
            raise RuntimeError("Unresolved broker pending orders")
        return reports

    def recover(self):
        with self.lock:
            if self.stopping.is_set() or not self.started:
                raise RuntimeError("Runtime is stopping or has not started")
            self.ready.clear()
            self.store.assert_owner()
            if not self.client.authenticated or not self.client.demo_verified:
                raise RuntimeError(
                    "Recovery requires a verified authenticated DEMO account"
                )
            generation = self.client.generation
            logger.info(
                "Broker recovery started account=%s generation=%s",
                self.client.account_id,
                generation,
            )
            self.provider.recover_executions(self.persistence)
            self._inspect(allow_close=False)
            if generation != self.client.generation or not self.client.authenticated:
                raise RuntimeError("Connection changed during recovery")
            self.recovered_generation = generation
            self.last_error = None
            self.ready.set()
            logger.info(
                "Broker recovery completed account=%s unresolved=%s",
                self.client.account_id,
                len(self.persistence.get_unresolved()),
            )

    def start(self, timeout=30):
        if self.started:
            raise RuntimeError("Runtime already started")
        self.store.acquire()
        try:
            state = self.store.load()
            self.paused, self.critical_error = state["paused"], state["critical_error"]
            if self.observation_enabled:
                self.paused = True
                self.store.save(True, self.critical_error)
            self.started = True
            self.client.start()
            if not self.client.wait_until_authenticated(timeout):
                raise RuntimeError("cTrader DEMO authentication failed or timed out")
            try:
                self.recover()
            except Exception as exc:
                # Authenticated but unsafe/recovering: remain observable, never enter.
                self.last_error = str(exc)
                logger.exception("Startup recovery incomplete; new entries blocked")
            self.worker = threading.Thread(
                target=self._monitor, name="ctrader-recovery", daemon=True
            )
            self.worker.start()
        except Exception:
            self.started = False
            try:
                self.client.stop()
            finally:
                self.store.release()
            raise

    def _monitor(self):
        while not self.stopping.is_set():
            self.wake.wait(10)
            self.wake.clear()
            if self.stopping.is_set():
                break
            try:
                with self.lock:
                    self.store.assert_owner()
                    if self.pending_alert.is_set():
                        self.store.save(self.paused, self.critical_error)
                        self.pending_alert.clear()
                    if self.client.authenticated:
                        if (
                            not self.ready.is_set()
                            or self.recovered_generation != self.client.generation
                        ):
                            self.recover()
                        else:
                            self._inspect(allow_close=False)
            except Exception as exc:
                self.ready.clear()
                self.last_error = str(exc)
                logger.error("Broker monitoring/recovery blocked: %s", exc)

    def manage_positions(self):
        with self.lock:
            if not self.client.authenticated:
                self.ready.clear()
                logger.warning("Position management unavailable: disconnected")
                return []
            if not self.ready.is_set():
                self.recover()
            return self._inspect(allow_close=self.close_allowed())

    def before_open(self, request):
        # Invoked inside operation(), with another check on the reactor before send.
        self.store.assert_owner()
        reason = self.block_reason()
        if request.symbol != "XAUUSD":
            reason = "unsupported_symbol"
        if reason:
            raise RuntimeError(reason)
        self._inspect(allow_close=False)
        if not self.order_allowed():
            raise RuntimeError(self.block_reason())
        if self.provider.get_open_positions():
            raise RuntimeError("position_already_open")
        if any(
            r.request_id != request.request_id
            for r in self.persistence.get_unresolved()
        ):
            raise RuntimeError("unresolved_broker_execution")
        if self.persistence.get_unresolved_closes(self.client.account_id):
            raise RuntimeError("unresolved_position_close")

    def pause(self):
        self.safety_epoch += (
            1  # Invalidate proposals whose inference overlaps an operator pause.
        )
        self.pause_requested.set()
        self.paused = True  # Immediately closes the reactor-side gate.
        with self.lock:
            self.paused = True
            self.store.save(True, self.critical_error)
            self.pause_requested.clear()
            logger.warning(
                "Trading paused by operator account=%s", self.client.account_id
            )
        return self.status()

    def resume(self):
        if self.observation_enabled:
            raise RuntimeError("Observation mode is read-only; restart without observation and explicitly resume trading")
        with self.lock:
            if self.pause_requested.is_set():
                raise RuntimeError("An operator pause is awaiting persistence")
            if self.pending_alert.is_set():
                raise RuntimeError("A safety alert is awaiting durable persistence")
            epoch = self.safety_epoch
            self.recover()  # Explicit acknowledgement cannot bypass current safety checks.
            if epoch != self.safety_epoch or not self.ready.is_set():
                raise RuntimeError("A new safety alert arrived during resume")
            self.store.save(False, None)
            self.critical_error = None
            self.paused = False
            logger.warning(
                "Trading resumed by operator account=%s", self.client.account_id
            )
            return self.status()

    def status(self):
        return {
            "mode": "ctrader_demo",
            "observation_enabled": self.observation_enabled,
            "observation_block_reason": self.observation_block_reason(),
            "account_id": self.client.account_id,
            "demo_verified": self.client.demo_verified,
            "authenticated": self.client.authenticated,
            "new_orders_enabled": self.order_allowed(),
            "block_reason": self.block_reason(),
            "paused": self.paused,
            "critical_error": self.critical_error,
            "last_error": self.last_error,
            "positions": self.position_reports,
        }

    def stop(self):
        self.stopping.set()
        self.ready.clear()
        self.wake.set()
        if self.worker is not None:
            self.worker.join()  # SDK sends are bounded; never release the lease while recovering.
        with self.lock:
            try:
                self.client.stop()
            finally:
                self.store.release()
                self.started = False
