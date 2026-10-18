from dotenv import load_dotenv

from app.integrations.ctrader.client import CTraderClient
from app.providers.ctrader_broker_provider import CTraderBrokerProvider

load_dotenv()


client = CTraderClient()
client.start()

if not client.wait_until_authenticated(timeout=15):
    raise RuntimeError("cTrader authentication timed out")

print("cTrader client authenticated successfully")

provider = CTraderBrokerProvider(client)

positions = provider.get_open_positions()

print("Open positions:", len(positions))

for position in positions:
    print(position.model_dump())
