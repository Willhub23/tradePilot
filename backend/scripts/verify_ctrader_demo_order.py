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
    request_id="manual-demo-order-1",
    agent_decision_id="manual-test",
    symbol="XAUUSD",
    direction="long",
    trade_style="swing",
    confidence=70,
    thesis="Manual cTrader demo integration test",
    volume_lots=0.01,
    reference_price=4180,
    stop_loss=4165,
    take_profit=4200,
    max_holding_minutes=2880,
)

result = provider.open_position(request)

print(result.model_dump_json(indent=2))
