-- Apply before using cTrader position management. Existing tables are unchanged.
CREATE TABLE IF NOT EXISTS broker_position_closes (
    account_id VARCHAR NOT NULL,
    broker_position_id VARCHAR NOT NULL,
    volume BIGINT NOT NULL,
    status VARCHAR NOT NULL,
    broker_order_id VARCHAR,
    message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, broker_position_id)
);
