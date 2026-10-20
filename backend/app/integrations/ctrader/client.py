from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from time import perf_counter_ns
from concurrent.futures import Future
from typing import Any

from ctrader_open_api import Client, EndPoints, TcpProtocol, Protobuf
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAClientPermissionScope
from twisted.internet import reactor, threads
from twisted.application.internet import ClientService

logger = logging.getLogger(__name__)
_REACTOR_LOCK = threading.Lock()
_REACTOR_STARTED = False


class CTraderClient:
    def __init__(self, execution_event_handler=None, *, read_only=False) -> None:
        self._read_only = read_only
        self.client_id = os.getenv("CTRADER_CLIENT_ID")
        self.client_secret = os.getenv("CTRADER_CLIENT_SECRET")
        self.access_token = os.getenv("CTRADER_ACCESS_TOKEN")
        self.account_id = int(os.getenv("CTRADER_ACCOUNT_ID", "0"))
        self.environment = os.getenv("CTRADER_ENVIRONMENT", "demo").lower()
        for key, value in (("CTRADER_CLIENT_ID", self.client_id), ("CTRADER_CLIENT_SECRET", self.client_secret),
                           ("CTRADER_ACCESS_TOKEN", self.access_token), ("CTRADER_ACCOUNT_ID", self.account_id)):
            if not value: raise ValueError(f"{key} is missing")
        if self.account_id <= 0: raise ValueError("Invalid cTrader account ID")
        if self.environment != "demo":
            raise ValueError("This TradePilot version permits only CTRADER_ENVIRONMENT=demo")
        self._client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        if execution_event_handler is None:
            from app.services.ctrader_execution_event_service import CTraderExecutionEventService
            execution_event_handler = CTraderExecutionEventService(account_id=self.account_id)
        self._execution_event_handler = execution_event_handler
        self._authenticated = threading.Event()
        self._started = False
        self.demo_verified = False
        self.generation = 0
        self.state_listener = lambda: None
        self.close_guard = lambda: True
        self.order_guard = lambda: False  # Opening orders require a recovered runtime.
        self._reauth_call = None
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message_received)

    def set_tracking_error_handler(self, handler):
        self._execution_event_handler.error_callback = handler

    def set_position_protection_verifier(self, verifier) -> None:
        self._execution_event_handler.protection_verifier = verifier

    @property
    def authenticated(self) -> bool:
        return self._authenticated.is_set()

    def start(self) -> None:
        global _REACTOR_STARTED
        if self._started: return
        with _REACTOR_LOCK:
            if not reactor.running:
                if _REACTOR_STARTED or getattr(reactor, "_startedBefore", False):
                    raise RuntimeError("Stopped Twisted reactor cannot be restarted; restart the process")
                _REACTOR_STARTED = True
                threading.Thread(target=self._run_reactor, daemon=True, name="ctrader-reactor").start()
            self._started = True
            reactor.callFromThread(self._client.startService)

    def wait_until_authenticated(self, timeout: float = 30.0) -> bool:
        return self._authenticated.wait(timeout)

    def _validate_send(self, request):
        if self._read_only and type(request) not in (
            M.ProtoOATraderReq, M.ProtoOAAssetListReq, M.ProtoOAReconcileReq,
            M.ProtoOAGetPositionUnrealizedPnLReq, M.ProtoOADealListReq,
            M.ProtoOASymbolsListReq, M.ProtoOASymbolByIdReq,
        ):
            raise RuntimeError("Read-only client refuses this broker request")
        if not self.authenticated or not self.demo_verified:
            raise RuntimeError("cTrader DEMO account is not authenticated/verified")
        if hasattr(request, "ctidTraderAccountId") and request.ctidTraderAccountId != self.account_id:
            raise RuntimeError("Unexpected account ID in cTrader request")
        if isinstance(request, M.ProtoOAClosePositionReq) and not self.close_guard():
            raise RuntimeError("Position close blocked by runtime safety gate")
        if isinstance(request, M.ProtoOANewOrderReq) and not self.order_guard():
            raise RuntimeError("New orders blocked by runtime safety gate")

    def send(self, request: Any, timeout: float = 15.0) -> Any:
        self._validate_send(request)
        future: Future[Any] = Future()
        def completed(value=None, error=None):
            if not future.done():
                if error is None: future.set_result(value)
                else: future.set_exception(error)
        def send_request():
            try:
                self._validate_send(request)  # Disconnect/pause can happen while queued.
                deferred = self._client.send(request, responseTimeoutInSeconds=timeout)
                deferred.addCallbacks(
                    lambda response: completed(Protobuf.extract(response)),
                    lambda failure: completed(error=getattr(failure, "value", RuntimeError("Broker transport failure"))),
                )
            except Exception as exc:
                completed(error=exc)
        reactor.callFromThread(send_request)
        return future.result(timeout=timeout + 1)

    def stop(self) -> None:
        self._authenticated.clear()
        self.demo_verified = False
        if not self._started: return
        self._started = False
        self.state_listener()
        finished = Future()
        def stop_service():
            if self._reauth_call is not None and self._reauth_call.active():
                self._reauth_call.cancel()
            # SDK stopService skips stopping while disconnected. Stop the underlying
            # reconnecting service too, otherwise it may reconnect after shutdown.
            deferred = ClientService.stopService(self._client)
            if deferred is None: finished.set_result(None)
            else: deferred.addBoth(lambda _: finished.set_result(None))
        reactor.callFromThread(stop_service)
        finished.result(timeout=20)
        # Keep the single process reactor alive; Twisted cannot restart it.

    def _run_reactor(self):
        reactor.run(installSignalHandlers=False)

    def _invalidate(self):
        self._authenticated.clear()
        self.demo_verified = False
        self.generation += 1
        self.state_listener()

    def _on_connected(self, client):
        self._invalidate()
        if not self._started: return
        if self._reauth_call is not None and self._reauth_call.active(): self._reauth_call.cancel()
        request = M.ProtoOAApplicationAuthReq(clientId=self.client_id, clientSecret=self.client_secret)
        client.send(request, responseTimeoutInSeconds=15).addCallbacks(self._on_application_authenticated, self._on_error)

    def _on_application_authenticated(self, response):
        if not self._started or not self._client.isConnected: return
        if not isinstance(Protobuf.extract(response), M.ProtoOAApplicationAuthRes):
            return self._on_error("Application authentication rejected")
        request = M.ProtoOAGetAccountListByAccessTokenReq(accessToken=self.access_token)
        self._client.send(request, responseTimeoutInSeconds=15).addCallbacks(self._on_accounts, self._on_error)

    def _on_accounts(self, response):
        if not self._started or not self._client.isConnected: return
        decoded = Protobuf.extract(response)
        if not isinstance(decoded, M.ProtoOAGetAccountListByAccessTokenRes):
            return self._on_error("Account identity verification failed")
        matches = [a for a in decoded.ctidTraderAccount if a.ctidTraderAccountId == self.account_id]
        if (decoded.accessToken != self.access_token or len(matches) != 1
                or not matches[0].HasField("isLive") or matches[0].isLive
                or not decoded.HasField("permissionScope")
                or decoded.permissionScope not in ((ProtoOAClientPermissionScope.SCOPE_VIEW, ProtoOAClientPermissionScope.SCOPE_TRADE)
                    if self._read_only else (ProtoOAClientPermissionScope.SCOPE_TRADE,))):
            return self._on_error("Expected DEMO account/token/trading permission could not be verified")
        self.demo_verified = True
        request = M.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.access_token)
        self._client.send(request, responseTimeoutInSeconds=15).addCallbacks(self._on_account_authenticated, self._on_error)

    def _on_account_authenticated(self, response):
        if not self._started or not self._client.isConnected: return
        decoded = Protobuf.extract(response)
        if (not isinstance(decoded, M.ProtoOAAccountAuthRes)
                or decoded.ctidTraderAccountId != self.account_id or not self.demo_verified or not self._started):
            return self._on_error("Account authentication mismatch")
        self._authenticated.set()
        logger.info("Verified DEMO account authenticated account_id=%s", self.account_id)
        self.state_listener()

    def _on_disconnected(self, client, reason):
        self._invalidate()
        logger.warning("cTrader disconnected; entries blocked")
        # SDK ClientService reconnects this same connection and calls _on_connected.

    def _on_message_received(self, client, message):
        decoded = Protobuf.extract(message)
        if (isinstance(decoded, M.ProtoOAClientDisconnectEvent)
                or isinstance(decoded, M.ProtoOAAccountDisconnectEvent) and decoded.ctidTraderAccountId == self.account_id
                or isinstance(decoded, M.ProtoOAAccountsTokenInvalidatedEvent) and self.account_id in decoded.ctidTraderAccountIds):
            self._on_error("Broker invalidated authentication")
        if isinstance(decoded, (M.ProtoOAExecutionEvent, M.ProtoOAOrderErrorEvent)):
            threads.deferToThread(self._execution_event_handler.process, decoded,
                received_at=datetime.now(timezone.utc), received_monotonic_ns=perf_counter_ns()).addErrback(self._on_execution_tracking_error)

    def _on_execution_tracking_error(self, failure):
        logger.error("cTrader execution tracking failed: %s", failure)

    def _on_error(self, failure):
        self._invalidate()
        logger.error("cTrader authentication failed; entries remain blocked")
        # Retry authentication only, never an order. Credentials/token may need operator renewal.
        if self._started and self._client.isConnected:
            if self._reauth_call is None or not self._reauth_call.active():
                self._reauth_call = reactor.callLater(10, self._on_connected, self._client)
