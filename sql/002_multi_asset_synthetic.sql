-- Preserve all stage 1 rows; migration runner applies this once, atomically.
ALTER TABLE raw.price_fetches DROP CONSTRAINT price_fetches_product_id_check;
ALTER TABLE raw.price_fetches ADD CHECK (product_id IN ('BTC-USD','ETH-USD','SOL-USD'));
ALTER TABLE market.daily_prices DROP CONSTRAINT daily_prices_product_id_check;
ALTER TABLE market.daily_prices ADD CHECK (product_id IN ('BTC-USD','ETH-USD','SOL-USD'));
ALTER TABLE market.daily_prices RENAME COLUMN volume_btc TO volume_base;

CREATE SCHEMA IF NOT EXISTS synthetic;
CREATE TABLE synthetic.datasets (
    dataset_id TEXT PRIMARY KEY,
    generator_version TEXT NOT NULL,
    seed BIGINT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK (end_date > start_date),
    customer_count INTEGER NOT NULL CHECK (customer_count > 0),
    trade_count INTEGER NOT NULL CHECK (trade_count > 0),
    prices_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'synthetic' CHECK (origin = 'synthetic'),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE synthetic.customers (
    customer_id UUID PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES synthetic.datasets(dataset_id),
    customer_number INTEGER NOT NULL CHECK (customer_number > 0),
    created_at TIMESTAMPTZ NOT NULL,
    activity_profile TEXT NOT NULL CHECK (activity_profile IN ('never','regular','ceased')),
    UNIQUE (dataset_id, customer_number),
    UNIQUE (dataset_id, customer_id)
);
CREATE TABLE synthetic.trades (
    trade_id UUID PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES synthetic.datasets(dataset_id),
    trade_number INTEGER NOT NULL CHECK (trade_number > 0),
    customer_id UUID NOT NULL,
    traded_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (source = 'coinbase_exchange'),
    product_id TEXT NOT NULL,
    price_date DATE NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    status TEXT NOT NULL DEFAULT 'MATCHED' CHECK (status = 'MATCHED'),
    quantity NUMERIC(20,6) NOT NULL CHECK (quantity > 0),
    reference_close NUMERIC NOT NULL CHECK (reference_close > 0),
    slippage_bps INTEGER NOT NULL CHECK (slippage_bps BETWEEN -25 AND 25),
    execution_price NUMERIC(20,2) NOT NULL CHECK (execution_price > 0),
    notional_usd NUMERIC(20,2) NOT NULL CHECK (notional_usd > 0),
    fee_usd NUMERIC(20,2) NOT NULL CHECK (fee_usd >= 0),
    reference_fetch_id UUID NOT NULL REFERENCES raw.price_fetches(fetch_id),
    UNIQUE (dataset_id, trade_number),
    FOREIGN KEY (dataset_id, customer_id) REFERENCES synthetic.customers(dataset_id, customer_id),
    FOREIGN KEY (source, product_id, price_date) REFERENCES market.daily_prices(source, product_id, price_date),
    CHECK (traded_at >= price_date::timestamp AT TIME ZONE 'UTC'
       AND traded_at < (price_date + 1)::timestamp AT TIME ZONE 'UTC'),
    CHECK (execution_price = round(reference_close * (1 + slippage_bps::numeric / 10000), 2)),
    CHECK (notional_usd = round(quantity * execution_price, 2)),
    CHECK (fee_usd = round(notional_usd * 0.001, 2))
);
CREATE INDEX ON synthetic.trades (dataset_id, customer_id, traded_at);
