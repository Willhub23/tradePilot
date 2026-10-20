-- Safety state only: no order/trading data is changed. Initial activation is paused.
CREATE TABLE IF NOT EXISTS broker_runtime_state (
    account_id VARCHAR PRIMARY KEY,
    paused BOOLEAN NOT NULL,
    critical_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
