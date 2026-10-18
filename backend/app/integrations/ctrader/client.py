from __future__ import annotations

import os
import threading
from concurrent.futures import Future
from typing import Any

from ctrader_open_api import Client, EndPoints, TcpProtocol, Protobuf

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAAccountAuthReq,
    ProtoOAExecutionEvent,
    ProtoOAOrderErrorEvent,
)
from twisted.internet import reactor


class CTraderClient:
    def __init__(self) -> None:
        self.client_id = os.getenv("CTRADER_CLIENT_ID")
        self.client_secret = os.getenv("CTRADER_CLIENT_SECRET")
        self.access_token = os.getenv("CTRADER_ACCESS_TOKEN")
        self.account_id = int(os.getenv("CTRADER_ACCOUNT_ID", "0"))
        self.environment = os.getenv("CTRADER_ENVIRONMENT", "demo").lower()

        if not self.client_id:
            raise ValueError("CTRADER_CLIENT_ID is missing")
        if not self.client_secret:
            raise ValueError("CTRADER_CLIENT_SECRET is missing")
        if not self.access_token:
            raise ValueError("CTRADER_ACCESS_TOKEN is missing")
        if not self.account_id:
            raise ValueError("CTRADER_ACCOUNT_ID is missing")

        if self.environment not in {"demo", "live"}:
            raise ValueError("CTRADER_ENVIRONMENT must be 'demo' or 'live'")

        host = (
            EndPoints.PROTOBUF_DEMO_HOST
            if self.environment == "demo"
            else EndPoints.PROTOBUF_LIVE_HOST
        )

        self._client = Client(
            host,
            EndPoints.PROTOBUF_PORT,
            TcpProtocol,
        )

        self._authenticated = threading.Event()
        self._started = False

        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message_received)

    @property
    def authenticated(self) -> bool:
        return self._authenticated.is_set()

    def start(self) -> None:
        if self._started:
            return

        self._started = True
        self._client.startService()

        if not reactor.running:
            thread = threading.Thread(
                target=self._run_reactor,
                daemon=True,
                name="ctrader-reactor",
            )
            thread.start()

    def wait_until_authenticated(
        self,
        timeout: float = 15.0,
    ) -> bool:
        return self._authenticated.wait(timeout)

    def send(
        self,
        request: Any,
        timeout: float = 15.0,
    ) -> Any:
        if not self.authenticated:
            raise RuntimeError("cTrader client is not authenticated")

        future: Future[Any] = Future()

        def send_request() -> None:
            deferred = self._client.send(
                request,
                responseTimeoutInSeconds=timeout,
            )

            deferred.addCallbacks(
                lambda response: future.set_result(Protobuf.extract(response)),
                lambda failure: future.set_exception(
                    failure.value
                    if hasattr(failure, "value")
                    else RuntimeError(str(failure))
                ),
            )

        reactor.callFromThread(send_request)

        return future.result(timeout=timeout + 1)

    def stop(self) -> None:
        if not self._started:
            return

        reactor.callFromThread(self._client.stopService)
        self._authenticated.clear()
        self._started = False

    def _run_reactor(self) -> None:
        reactor.run(installSignalHandlers=False)

    def _on_connected(self, client: Client) -> None:
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret

        deferred = client.send(
            request,
            responseTimeoutInSeconds=15,
        )

        deferred.addCallbacks(
            self._on_application_authenticated,
            self._on_error,
        )

    def _on_application_authenticated(self, response: Any) -> None:
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token

        deferred = self._client.send(
            request,
            responseTimeoutInSeconds=15,
        )

        deferred.addCallbacks(
            self._on_account_authenticated,
            self._on_error,
        )

    def _on_account_authenticated(self, response: Any) -> None:
        self._authenticated.set()

    def _on_disconnected(
        self,
        client: Client,
        reason: Any,
    ) -> None:
        self._authenticated.clear()

    def _on_message_received(
        self,
        client: Client,
        message: Any,
    ) -> None:
        decoded = Protobuf.extract(message)

        if isinstance(decoded, ProtoOAExecutionEvent):
            print(
                "CTRADER EXECUTION EVENT:",
                decoded,
            )

        elif isinstance(decoded, ProtoOAOrderErrorEvent):
            print(
                "CTRADER ORDER ERROR:",
                decoded,
            )

    def _on_error(self, failure: Any) -> None:
        self._authenticated.clear()
        print(f"cTrader error: {failure}")
