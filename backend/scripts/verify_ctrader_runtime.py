"""Read-only broker verification: authentication, recovery and protection; NO orders."""
import argparse
import json
import logging
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    load_dotenv(args.env_file)
    from app.database import engine
    from scripts.migrate_database import verify_database
    from app.integrations.ctrader.client import CTraderClient
    from app.providers.ctrader_broker_provider import CTraderBrokerProvider
    from app.services.broker_runtime_store import BrokerRuntimeStore
    verify_database(engine)
    logging.basicConfig(level=logging.INFO)
    client = CTraderClient()
    store = BrokerRuntimeStore(client.account_id)
    store.acquire()
    try:
        client.start()
        if not client.wait_until_authenticated(30):
            raise RuntimeError("DEMO authentication failed")
        provider = CTraderBrokerProvider(client)
        unresolved = provider.recover_executions()
        reports = provider.position_management.manage(allow_close=False)
        print(json.dumps({"account_id": client.account_id, "demo_verified": client.demo_verified,
                          "unresolved_executions": [r.request_id for r in unresolved],
                          "pending_broker_orders": provider.get_pending_order_ids(),
                          "positions": [r.model_dump(mode="json") for r in reports],
                          "orders_submitted": False}, indent=2))
    finally:
        try: client.stop()
        finally: store.release()


if __name__ == "__main__":
    main()
