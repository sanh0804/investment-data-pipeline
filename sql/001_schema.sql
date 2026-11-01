CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS market;

-- Append-only: one row per HTTP attempt (including transient failures).
CREATE TABLE IF NOT EXISTS raw.price_fetches (
    fetch_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    source TEXT NOT NULL CHECK (source = 'coinbase_exchange'),
    product_id TEXT NOT NULL CHECK (product_id = 'BTC-USD'),
    endpoint TEXT NOT NULL,
    request_params JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    http_status INTEGER,
    response_headers JSONB NOT NULL,
    response_body BYTEA,
    response_sha256 TEXT,
    transport_error TEXT,
    UNIQUE (run_id, attempt_number),
    CHECK (fetched_at >= started_at)
);

-- Grain: one completed UTC daily candle per provider/product.
-- Price definition: Coinbase BTC-USD daily close, quoted in USD.
CREATE TABLE IF NOT EXISTS market.daily_prices (
    source TEXT NOT NULL CHECK (source = 'coinbase_exchange'),
    product_id TEXT NOT NULL CHECK (product_id = 'BTC-USD'),
    price_date DATE NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    open_price NUMERIC NOT NULL CHECK (open_price > 0),
    high_price NUMERIC NOT NULL CHECK (high_price > 0),
    low_price NUMERIC NOT NULL CHECK (low_price > 0),
    close_price NUMERIC NOT NULL CHECK (close_price > 0),
    volume_btc NUMERIC NOT NULL CHECK (volume_btc >= 0),
    last_fetch_id UUID NOT NULL REFERENCES raw.price_fetches(fetch_id),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, product_id, price_date),
    CHECK (bucket_start = (price_date::timestamp AT TIME ZONE 'UTC')),
    CHECK (low_price <= LEAST(open_price, close_price)),
    CHECK (high_price >= GREATEST(open_price, close_price)),
    CHECK (low_price <= high_price)
);
