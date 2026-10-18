from dotenv import load_dotenv

from app.integrations.ctrader.client import CTraderClient
from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from app.models.execution_models import BrokerExecutionRequest

load_dotenv()

client = CTraderClient()
client.start()

if not client.wait_until_authenticated(timeout=15):
    raise RuntimeError("cTrader authentication timed out")

provider = CTraderBrokerProvider(client)

request = BrokerExecutionRequest(
    request_id="test-order-request-1",
    agent_decision_id="decision-1",
    symbol="XAUUSD",
    direction="long",
    trade_style="swing",
    confidence=70,
    thesis="manual request verification",
    volume_lots=0.01,
    reference_price=4180,
    stop_loss=4165,
    take_profit=4200,
    max_holding_minutes=2880,
)

order = provider.build_market_order_request(request)

print("account:", order.ctidTraderAccountId)
print("symbol:", order.symbolId)
print("orderType:", order.orderType)
print("tradeSide:", order.tradeSide)
print("volume:", order.volume)
print("clientOrderId:", order.clientOrderId)
print("relativeStopLoss:", order.relativeStopLoss)
print("relativeTakeProfit:", order.relativeTakeProfit)
