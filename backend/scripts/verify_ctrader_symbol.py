from dotenv import load_dotenv

from app.integrations.ctrader.client import CTraderClient
from app.providers.ctrader_broker_provider import CTraderBrokerProvider

load_dotenv()

client = CTraderClient()
client.start()

if not client.wait_until_authenticated(timeout=15):
    raise RuntimeError("cTrader authentication timed out")

provider = CTraderBrokerProvider(client)

metadata = provider.get_symbol_metadata("XAUUSD")

print("symbolId:", metadata.symbolId)
print("lotSize:", metadata.lotSize)
print("minVolume:", metadata.minVolume)
print("stepVolume:", metadata.stepVolume)

print(
    "0.01 lots -> protocol volume:",
    provider.convert_lots_to_volume(
        0.01,
        metadata,
    ),
)
