-- Additive opportunity journal; existing trading data is preserved.
CREATE TABLE IF NOT EXISTS strategy_evaluations (
    id VARCHAR PRIMARY KEY,
    opportunity_id VARCHAR UNIQUE,
    strategy_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    agent_decision_id VARCHAR REFERENCES agent_decisions(id),
    request_id VARCHAR UNIQUE REFERENCES broker_executions(request_id),
    data JSONB NOT NULL
);
