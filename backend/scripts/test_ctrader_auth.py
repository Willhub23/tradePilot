import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, TcpProtocol, EndPoints, Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
)

load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID_RAW = os.getenv("CTRADER_ACCOUNT_ID")
ENVIRONMENT = os.getenv("CTRADER_ENVIRONMENT", "demo").lower()

if not CLIENT_ID:
    raise RuntimeError("Missing CTRADER_CLIENT_ID")

if not CLIENT_SECRET:
    raise RuntimeError("Missing CTRADER_CLIENT_SECRET")

if not ACCESS_TOKEN:
    raise RuntimeError("Missing CTRADER_ACCESS_TOKEN")

if not ACCOUNT_ID_RAW:
    raise RuntimeError("Missing CTRADER_ACCOUNT_ID")

if ENVIRONMENT not in {"demo", "live"}:
    raise RuntimeError("CTRADER_ENVIRONMENT must be 'demo' or 'live'")

ACCOUNT_ID = int(ACCOUNT_ID_RAW)

REQUEST_TIMEOUT_SECONDS = 15

received = {
    "trader": False,
    "symbols": False,
    "symbol_details": False,
    "reconcile": False,
}


host = (
    EndPoints.PROTOBUF_LIVE_HOST
    if ENVIRONMENT == "live"
    else EndPoints.PROTOBUF_DEMO_HOST
)

client = Client(
    host,
    EndPoints.PROTOBUF_PORT,
    TcpProtocol,
)


def stop_reactor():
    if reactor.running:
        reactor.stop()


def on_request_error(failure):
    print("\n=== cTrader request failed ===")
    print(failure)
    stop_reactor()


def send(request):
    deferred = client.send(
        request,
        responseTimeoutInSeconds=REQUEST_TIMEOUT_SECONDS,
    )

    deferred.addErrback(on_request_error)

    return deferred


def maybe_stop():
    if all(received.values()):
        print("\n=== READ-ONLY CTRADER TEST COMPLETE ===")
        stop_reactor()


def connected(client):
    print(f"Connected to cTrader ({ENVIRONMENT})")

    # Step 1: authenticate the TradePilot application.
    request = ProtoOAApplicationAuthReq()
    request.clientId = CLIENT_ID
    request.clientSecret = CLIENT_SECRET

    send(request)


def disconnected(client, reason):
    print("Disconnected:", reason)


def on_message_received(client, message):
    payload_type = message.payloadType

    # ------------------------------------------------------------
    # 1. Application authentication
    # ------------------------------------------------------------
    if payload_type == ProtoOAApplicationAuthRes().payloadType:
        print("Application authorized")

        request = ProtoOAGetAccountListByAccessTokenReq()
        request.accessToken = ACCESS_TOKEN

        send(request)
        return

    # ------------------------------------------------------------
    # 2. Find configured account
    # ------------------------------------------------------------
    if payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
        response = Protobuf.extract(message)

        print("\n=== ACCOUNTS AVAILABLE ===")

        for account in response.ctidTraderAccount:
            print(f"account_id={account.ctidTraderAccountId} " f"live={account.isLive}")

        account = next(
            (
                account
                for account in response.ctidTraderAccount
                if account.ctidTraderAccountId == ACCOUNT_ID
            ),
            None,
        )

        if account is None:
            print(
                f"\nConfigured account {ACCOUNT_ID} "
                "was not found for this access token."
            )
            stop_reactor()
            return

        expected_live = ENVIRONMENT == "live"

        if account.isLive != expected_live:
            print(
                f"\nAccount/environment mismatch: "
                f"account live={account.isLive}, "
                f"environment={ENVIRONMENT}"
            )
            stop_reactor()
            return

        print(f"\nAuthenticating account {ACCOUNT_ID}")

        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = ACCOUNT_ID
        request.accessToken = ACCESS_TOKEN

        send(request)
        return

    # ------------------------------------------------------------
    # 3. Account authentication succeeded
    # ------------------------------------------------------------
    if payload_type == ProtoOAAccountAuthRes().payloadType:
        response = Protobuf.extract(message)

        print(f"Account {response.ctidTraderAccountId} " "authorized successfully")

        # Account details
        trader_request = ProtoOATraderReq()
        trader_request.ctidTraderAccountId = ACCOUNT_ID
        send(trader_request)

        # Symbol list
        symbols_request = ProtoOASymbolsListReq()
        symbols_request.ctidTraderAccountId = ACCOUNT_ID
        symbols_request.includeArchivedSymbols = False
        send(symbols_request)

        # Current open positions / pending orders
        reconcile_request = ProtoOAReconcileReq()
        reconcile_request.ctidTraderAccountId = ACCOUNT_ID
        send(reconcile_request)

        return

    # ------------------------------------------------------------
    # 4. Account details
    # ------------------------------------------------------------
    if payload_type == ProtoOATraderRes().payloadType:
        response = Protobuf.extract(message)

        print("\n=== TRADER ACCOUNT ===")
        print(response.trader)

        received["trader"] = True
        maybe_stop()
        return

    # ------------------------------------------------------------
    # 5. Resolve XAUUSD
    # ------------------------------------------------------------
    if payload_type == ProtoOASymbolsListRes().payloadType:
        response = Protobuf.extract(message)

        print("\n=== SEARCHING FOR XAUUSD ===")

        xauusd = next(
            (
                symbol
                for symbol in response.symbol
                if symbol.symbolName.upper() == "XAUUSD"
            ),
            None,
        )

        if xauusd is None:
            print("XAUUSD was not found for this account.")
            stop_reactor()
            return

        print(
            f"Found XAUUSD: "
            f"symbol_id={xauusd.symbolId}, "
            f"name={xauusd.symbolName}"
        )

        received["symbols"] = True

        request = ProtoOASymbolByIdReq()
        request.ctidTraderAccountId = ACCOUNT_ID
        request.symbolId.append(xauusd.symbolId)

        send(request)

        maybe_stop()
        return

    # ------------------------------------------------------------
    # 6. Full XAUUSD metadata
    # ------------------------------------------------------------
    if payload_type == ProtoOASymbolByIdRes().payloadType:
        response = Protobuf.extract(message)

        print("\n=== XAUUSD FULL SYMBOL DATA ===")

        if not response.symbol:
            print("No symbol details returned.")
            stop_reactor()
            return

        for symbol in response.symbol:
            print(symbol)

        received["symbol_details"] = True
        maybe_stop()
        return

    # ------------------------------------------------------------
    # 7. Existing positions / pending orders
    # ------------------------------------------------------------
    if payload_type == ProtoOAReconcileRes().payloadType:
        response = Protobuf.extract(message)

        print("\n=== OPEN POSITIONS ===")
        print(f"Open positions: {len(response.position)}")

        for position in response.position:
            print(position)

        print(f"Pending orders: {len(response.order)}")

        for order in response.order:
            print(order)

        received["reconcile"] = True
        maybe_stop()
        return

    print(
        "\nReceived unhandled payload:",
        Protobuf.extract(message),
    )


client.setConnectedCallback(connected)
client.setDisconnectedCallback(disconnected)
client.setMessageReceivedCallback(on_message_received)

client.startService()

reactor.run()
