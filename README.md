# Investment Data Pipeline & Analytics

Bài toán của project là đưa dữ liệu từ API đến báo cáo và kiểm tra số liệu ở từng
lớp: dữ liệu gốc, bảng chuẩn hóa, fact, mart và dashboard. Nguồn giá là nến ngày
BTC/ETH/SOL từ Coinbase Exchange. Phần giao dịch gồm 1.000 khách hàng và 50.000
matched trades **synthetic**, dùng để kiểm tra phép tổng hợp và phân nhóm khách hàng.

Stack triển khai: Python, PostgreSQL, dbt Core, Metabase và Docker Compose.
Pipeline chạy batch thủ công trên máy local; Airflow và GitHub Actions là hướng mở rộng.
Không mô phỏng số dư, holdings, P&L, SCD2, retention hay RFM scoring.

## Dashboard preview

Ảnh chụp Metabase local với dataset `portfolio-v1`, cutoff **2026-10-27 UTC**.
Khách hàng và giao dịch là synthetic; giá tham chiếu lấy từ Coinbase.

**Tổng quan giao dịch:** volume, phí, số giao dịch, distinct active customers và
biểu đồ theo ngày.

![Metabase: tổng quan giao dịch và biểu đồ volume, phí theo ngày](docs/screenshots/dashboard_1.png)

**Phân tích chi tiết:** volume theo asset/BUY/SELL, customer segments tại cutoff
và khách hàng theo tổng phí. Hai customer cards giữ snapshot khi lọc ngày/asset.

![Metabase: volume theo asset, customer segments và bảng khách hàng theo phí](docs/screenshots/dashboard_2.png)

## Các lựa chọn thiết kế

| Lựa chọn | Lý do và đánh đổi |
| --- | --- |
| Lưu raw trước khi normalize | Có thể kiểm tra payload và replay khi logic thay đổi; số raw rows tăng khi fetch lại. |
| Upsert giá theo source/product/ngày UTC | Chạy lại giữ nguyên grain, nhận được giá lịch sử đã sửa; bảng chuẩn hóa chỉ giữ phiên bản mới nhất. |
| Cố định seed, khoảng ngày và price-input hash | So sánh kết quả giữa các lần chạy; dataset đã đăng ký sẽ từ chối input khác thay vì tự sinh lại. |
| dbt models dạng table, staging dạng view | Dễ đối chiếu ở quy mô 50.000 trades; cần build lại tables sau khi sources đổi. |
| Fixture nhỏ chạy qua cùng dbt models | Có expected totals tính độc lập để bắt lỗi join, cutoff và rounding mà tổng lớn có thể che mất. |
| Metabase dùng SELECT role riêng | Dashboard đọc analytics; app metadata nằm ở database và volume riêng. |

Chi tiết ở [decisions](docs/decisions.md), [model contracts](docs/models.md) và
[verification](docs/verification.md).

## Setup macOS

Kiểm tra môi trường trước khi cài dependencies:

```bash
uname -m
python3 --version
docker --version
docker compose version
docker info --format '{{.OSType}} {{.Architecture}}'
git --version
```

Đã chạy trên macOS arm64, Python 3.13.5, Docker Linux aarch64. Cần Python >=3.11.
Chạy các lệnh từ repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp -n .env.example .env
chmod 600 .env
```

Nếu tạo `.env` mới, thay placeholder `POSTGRES_PASSWORD` bằng mật khẩu local
ngẫu nhiên trước khi chạy database. Có thể tạo bằng:
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Giữ nguyên password nếu dùng database đã khởi tạo; sửa env không tự đổi password
trong volume đã có. Không commit `.env`. API đã chọn không dùng API key.

```bash
.venv/bin/python scripts/init_metabase_env.py
docker compose up -d --wait postgres
.venv/bin/python -m pipeline.init_db
```

PostgreSQL bind `127.0.0.1:55432`, có healthcheck và volume riêng. Không đụng
PostgreSQL khác ở 5432. Nếu host port bận, đổi `POSTGRES_PORT` trong `.env`.
`init_db` chạy các migration `sql/NNN_*.sql` theo thứ tự và ghi vào
`public.schema_migrations`, toàn bộ trong transaction, có advisory lock.
Chạy lại không xóa bảng/volume. Migration `002` mở whitelist product và đổi tên
`volume_btc` thành `volume_base`, giữ nguyên dữ liệu đã có.
Không sửa nội dung migration đã áp dụng; thêm migration mới khi đổi schema.

## Thu thập 180 ngày giá live

Khoảng kiểm chứng cố định: **[2026-05-01, 2026-10-28) UTC**, gồm đúng 180 ngày.
Truyền rõ cấu hình để kết quả không phụ thuộc khoảng ngày có sẵn trong `.env`:

```bash
START_DATE=2026-05-01 END_DATE=2026-10-28 \
PRODUCTS=BTC-USD,ETH-USD,SOL-USD REQUEST_CHUNK_DAYS=60 \
.venv/bin/python -m pipeline.collector
```

Với ba sản phẩm và 180 ngày đầy đủ, kết quả là 540 normalized rows. Collector
kiểm tra coverage từng ngày; product metadata không đủ để kết luận có đủ lịch sử.
Nếu bỏ cả START_DATE/END_DATE trong env và `.env`, mặc định 180 ngày UTC hoàn tất
gần nhất. Chỉ đặt một trong hai biến là lỗi. Shell environment ưu tiên `.env`.

Nguồn: [Coinbase product candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles).
Giá là daily close trên sàn Coinbase, quote USD, không phải market-wide average.
Cả ba product đã trả HTTP 200 và có đủ lịch sử **trong khoảng đã kiểm chứng**;
không suy ra mọi mốc cũ hơn đều được hỗ trợ.

- `granularity=86400`; API time là đầu bucket, integer epoch seconds, UTC midnight.
- API có thể trả ngày ở end hoặc trước start, thứ tự giảm dần. Client lọc `[start,end)`,
  sắp xếp, từ chối duplicate/missing day hoặc bucket không phải UTC midnight.
- Mỗi window mặc định 60 ngày; các biên chung không tạo duplicate vì end-exclusive.
  Tối đa 299 ngày/window để chừa một inclusive-end candle trong mức 300 nến nguồn.
  Client cho phép tổng range 1..3650 ngày, tách request; không bảo đảm lịch sử sẵn có.
- Tất cả product/window phải qua validation trước khi commit **một normalized batch**.
  Bất kỳ window thiếu ngày thì không ghi normalized của toàn bộ run. Các raw attempts
  đã commit vẫn còn. Không nội suy hoặc dùng fixture để điền thiếu.
- `HTTP_REQUEST_INTERVAL` mặc định 1 giây, tối thiểu 0.2 giây, giữa các request.
  [Giới hạn public theo tài liệu](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits):
  10 req/s/IP, burst 15; không flood để thử quota. Probe vượt 300 nến thực tế trả 400.
- Connect/read timeout mặc định 5s/20s; read timeout là socket inactivity, không phải
  deadline toàn run. Tối đa 3 attempts/request (hard cap 5). Retry timeout, connection
  error, 408/429/500/502/503/504; không retry TLS certificate errors hoặc permanent 4xx.
  Exponential backoff, đọc Retry-After; nếu delay vượt budget thì dừng để chạy lại sau.

## Raw, grain và upsert

```text
Coinbase daily candles (live)
  → raw.price_fetches (commit mỗi HTTP attempt; body bytes + SHA-256 + metadata)
  → kiểm tra UTC, shape, completeness, Decimal và OHLC
  → market.daily_prices (transaction cho toàn bộ run, upsert source/product/day)
  → synthetic generator đọc snapshot daily closes
  → synthetic.datasets → synthetic.customers → synthetic.trades

market/synthetic → dbt staging → dimensions/fact → daily/customer marts
Fixture riêng → PostgreSQL test tmpfs → cùng dbt models; không load fixture vào DB live
```

Raw grain: một HTTP attempt, PK `fetch_id`, unique `(run_id,attempt_number)`.
`run_id` nhóm retries **của một request/window**, không phải cả collector invocation.
Raw lưu endpoint/product/params, start/fetched time UTC, HTTP status, body nguyên
bytes sau HTTP decompression, SHA-256, transport error và headers allowlist.
Không lưu authorization/cookie. Transport failure có body/status NULL.
Nếu JSON/data validation lỗi, raw vẫn tồn tại; nếu database down thì dừng,
chưa có disk spool. Raw là quy ước append-only trong ứng dụng, chưa phân role riêng.

Giá chuẩn hóa grain/key: **(source, product_id, price_date UTC)**.
`bucket_start` = 00:00 UTC của ngày đã hoàn tất. `ON CONFLICT DO UPDATE` cập nhật
OHLCV và lineage; lần xử lý commit sau cùng thắng. Chạy collector tuần tự.
`Decimal` → `NUMERIC` giữ precision; `volume_base` theo base asset.
`last_fetch_id` và `loaded_at` có thể đổi khi rerun; idempotency giá đo bằng key và
business values. Raw giữ response history, normalized không giữ version history.

## Synthetic customers và matched trades

Chỉ chạy sau khi có đủ 540 giá live cho khoảng cố định:

```bash
.venv/bin/python -m pipeline.synthetic \
  --dataset-id portfolio-v1 --seed 20261101 --start 2026-05-01 --end 2026-10-28
```

Chạy lại cùng lệnh để kiểm chứng. Không có API trade thật trong dataset này.
Toàn bộ dữ liệu sinh nằm trong schema **synthetic**, tách khỏi raw/market.

- 1.000 customers: 100 `never`, 600 `regular`, 300 `ceased`. Created_at nằm 1..30
  ngày trước start; customers được INSERT trước trades, trong cùng transaction.
- 50.000 trades trạng thái `MATCHED`, BUY/SELL, chia vòng qua 900 customers hoạt động.
  Mỗi khách hoạt động có 55/56 trades; regular trải đều từ ngày đầu đến ngày cuối
  (cách nhau tối đa 4 ngày ở bộ 180 ngày), ceased chỉ giao dịch 120 ngày đầu và
  không giao dịch 60 ngày cuối. Profile là kịch bản sinh dữ liệu, chưa phải churn model.
- UUID5 từ generator version + dataset_id + entity number; cùng dataset thì IDs ổn định.
  RNG seed cố định quyết định timestamps, quantity, slippage và created_at.
- Quantity 0.001000..1.000000 base asset, scale 6. Reference là daily close live của
  product/ngày đó. Slippage nguyên -25..25 bps. Tất cả tính bằng Decimal:

```text
execution_price = round_half_up(reference_close × (1 + slippage_bps / 10000), 2)
notional_usd    = round_half_up(quantity × execution_price, 2)
fee_usd         = round_half_up(notional_usd × 0.001, 2)   # 10 bps, round từng trade
```

**Giả định:** timestamp intraday nhưng execution dựa trên close cùng ngày, có
look-ahead nếu dùng cho backtest. Chỉ để tạo dữ liệu synthetic; không mô tả đây là
lịch sử khớp lệnh thực hay dùng đánh giá chiến lược. Không kiểm tra khả năng bán,
không mô hình hóa funds/inventory, không tính số dư, holdings hay P&L.

`synthetic.datasets` lưu seed/range/version, checksum price inputs và business data.
Rerun dùng INSERT ON CONFLICT DO NOTHING và đối chiếu toàn bộ business checksum.
Nếu cùng dataset_id nhưng seed/date/price snapshot/content thay đổi, báo lỗi;
không tự ghi đè. Dùng dataset_id mới khi chủ đích tạo bộ khác. Giá live cập nhật
lineage mà close không đổi vẫn cho phép rerun; reference_fetch_id gốc trong trade
được giữ để truy nguyên. Không tự refresh synthetic theo giá lịch sử đã sửa.

FK ràng buộc dataset/customer, market price key và raw fetch provenance; check
constraints ràng buộc side, status, số dương và các công thức tiền. Customer-time
ordering và cohort behavior được kiểm tra ở bước verification, chưa có trigger.

## Kiểm chứng và reconciliation fixture

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.verify_stage2a --dataset-id portfolio-v1
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_2a.sql
```

Verifier đọc dữ liệu, không ghi; xác minh coverage từng product, checksum synthetic,
counts, FK, thời gian customer/trade, profiles, công thức giá/notional/phí và raw lineage.
`sql/verify_2a.sql` có literals cho khoảng kiểm chứng và dataset_id mặc định, trả
các counters cần bằng 0 cùng totals. SQL minh họa nhanh:

```sql
SELECT product_id, count(*), min(price_date), max(price_date)
FROM market.daily_prices
WHERE price_date >= '2026-05-01' AND price_date < '2026-10-28'
GROUP BY product_id;

SELECT count(*), sum(notional_usd), sum(fee_usd)
FROM synthetic.trades WHERE dataset_id = 'portfolio-v1';
```

Replay **đúng body live đã lưu** hai lần (chọn fetch mới nhất của window/product):

```bash
.venv/bin/python -m pipeline.verify_idempotency --fetch-id <UUID-response-200>
```

Replay có ghi upsert normalized; không thêm raw và không gọi API. Script so sánh
count + OHLCV giữa hai transaction. Không replay fetch cũ đè phiên bản giá mới hơn.
Bằng chứng hai collector runs và synthetic reruns: [docs/verification.md](docs/verification.md).

Bộ nhỏ [tests/fixtures/reconciliation](tests/fixtures/reconciliation/README.md):
4 customers, 6 trades, giá hoàn toàn giả, **475.94 USD notional và 0.48 USD phí**.
Có CSV riêng và expected totals viết rõ theo customer/product/side/date, gồm ca
rounding 0.055 → 0.06 và 30.075 → 30.08. Source `fixture` không hợp lệ với live
schema; unit tests chỉ đọc file. Integration tests chạy chính SQL dbt models trên fixture
ở DB test và đối chiếu expected JSON.

## File chính và vận hành

| File | Vai trò |
| --- | --- |
| `AGENTS.md` | Scope hiện tại và quy tắc làm việc |
| `compose.yaml`, `.env.example` | PostgreSQL và cấu hình local |
| `pipeline/config.py`, `collector.py` | Window, HTTP retry, raw và điều phối load |
| `pipeline/normalize.py`, `db.py` | UTC/quality/Decimal và upsert |
| `pipeline/init_db.py`, `sql/00*.sql` | Migrations không reset dữ liệu |
| `pipeline/synthetic.py` | Generator, stable IDs và load synthetic |
| `pipeline/verify_stage2a.py`, `verify_idempotency.py` | Verification, replay |
| `sql/verify_2a.sql` | SQL kiểm tra coverage, FK, thời gian và totals |
| `tests/fixtures/reconciliation/` | Dataset nhỏ và expected totals cho reconciliation |
| `analytics/models/`, `analytics/tests/`, `analytics/macros/` | dbt models, data tests và scope/key macros |
| `scripts/run_dbt.py`, `scripts/verify_dbt.py`, `compose.test.yaml` | Env wrapper và isolated fixture/migration tests |
| `docs/models.md`, `sql/verify_2b.sql` | Analytics contract và SQL đối soát demo |
| `docs/` | Quyết định thiết kế, model contracts và bằng chứng kiểm chứng |

Xem `docker compose ps`, `docker compose logs --tail=100 postgres` khi lỗi.
Dừng bằng `docker compose stop`, khởi động lại bằng `docker compose up -d --wait`.
Không xóa volume/data để sửa lỗi. Image khóa major `postgres:16-bookworm`, chưa pin
image digest; Python khóa direct dependency versions, chưa có transitive lockfile.
Chưa có scheduler, alerting, run-status table, production roles hoặc kiểm chứng concurrency
cho collector. Các giới hạn/chưa chạy được ghi trong verification.

## dbt analytics

Python hiện có 3.13.5. dbt-postgres 1.11.0 công bố hỗ trợ Python 3.13;
đã cài và build thực tế thành công với dbt Core 1.11.0 trên máy này.
Dùng môi trường riêng để không thay dependency collector:

```bash
python3 -m venv .venv-dbt
.venv-dbt/bin/python -m pip install -r requirements-dbt.txt
.venv-dbt/bin/dbt --version
.venv-dbt/bin/python -m pip check
```

Project/profiles ở `analytics/`, không cần dbt packages bên ngoài. Profile không có
credentials literal. Wrapper đọc `.env`, giữ ưu tiên shell env và truyền password
qua DBT_ENV_SECRET_PASSWORD, không qua command arguments.
DBT_DATABASE mặc định POSTGRES_DB; DBT_SCHEMA mặc định analytics, chỉ được là
analytics hoặc analytics_* để bảo vệ source schemas. Sources mặc định synthetic/market.

```bash
.venv/bin/python scripts/run_dbt.py build --vars '{"dataset_id":"portfolio-v1","as_of_date":"2026-10-27"}'
```

as_of_date inclusive UTC tới trước 2026-10-28T00:00:00Z, không dùng CURRENT_DATE.
dataset_id/as_of_date mặc định cố định trong dbt_project.yml, có thể override qua
--vars. Customer/trade chỉ được giữ nếu created_at/traded_at trước hết ngày chốt.
Một schema chứa một dataset/cutoff snapshot; dùng DBT_SCHEMA=analytics_other_snapshot
khi muốn giữ snapshot khác song song. Build thay output dbt, không sửa source data.

8 models:

- stg_customers, stg_trades, stg_asset_prices: views, scope rõ dataset/source/cutoff.
- dim_assets, dim_date: source/product và calendar UTC.
- fact_trades: một execution/dataset, LEFT JOIN trên full customer/asset/price keys.
- mart_daily_trading: một dataset/ngày có trade, tổng mọi asset/side; COUNT DISTINCT
  trực tiếp theo ngày, không cộng distinct counts từ các nhóm con.
- mart_customer_summary: giữ cả zero-trade customers; totals zero, dates/recency NULL.

Trade volume = SUM(notional_usd), fees = SUM(fee_usd) đã round mỗi trade; không gọi
volume là revenue. Segment demo: never_traded; active <=30 ngày; at_risk 31..59;
inactive >=60. Tính từ trades tới cutoff, không SELECT activity_profile của generator.
Không RFM/churn inference. [Models, columns, grain, keys và lineage](docs/models.md).

Demo đã build 8 models + 88 data tests; các tầng khớp 50.000 trades,
615.063.969,51 USD volume và 615.063,60 USD phí; summary đủ 1.000 customers.
Daily mart có 116 ngày có trade, không tự thêm zero-trade days.
Grain đã kiểm tra là (dataset_id,trade_date), không phải ngày × asset × side.
Regular customers giao dịch cách 3–4 ngày theo hai lịch chung (55/56 trades),
ceased dùng lịch 55 trades trong 120 ngày đầu. Union lịch regular 91 ngày với
ceased 55 ngày, trùng 30 ngày → 116 ngày trade và 64 ngày zero-trade.
Các lịch trong cùng cohort được đồng bộ, nên số ngày hoạt động không đại diện
cho phân bố khách hàng thực. Asset/side breakdown đọc fact.

```bash
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_2b.sql
.venv/bin/python scripts/run_dbt.py docs generate
# Tùy chọn mở docs local:
.venv/bin/python scripts/run_dbt.py docs serve --port 8081
```

Truy vấn kiểm tra nhanh:

```sql
SELECT count(*), sum(notional_usd), sum(fee_usd) FROM analytics.fact_trades;
SELECT sum(trade_count), sum(trade_volume_usd), sum(fees_usd) FROM analytics.mart_daily_trading;
SELECT activity_segment, count(*) FROM analytics.mart_customer_summary GROUP BY 1;
-- Global distinct: không SUM active_customers của daily mart.
SELECT count(DISTINCT customer_id) FROM analytics.fact_trades;
```

### Fixture thực thi qua dbt và fresh migration test

```bash
.venv/bin/python scripts/verify_dbt.py
```

Script tự khởi động compose.test.yaml, project de_pj_2b_test, bind 127.0.0.1:55433.
Test PostgreSQL dùng tmpfs **không mount volume demo**. Có thể đổi TEST_POSTGRES_PORT
nếu bận; script từ chối dùng cùng port với demo. Tạo database verify_2b_<UUID> mới
mỗi lần, chạy migrations 001/002 và rerun, rồi load CSV vào fixture_input (không
write market/synthetic). Không gọi API, không tạo 50k trades mới, không xóa DB/volume.

Chạy chính 8 models/88 tests ở 4 cutoffs: 2026-05-03, 2026-05-01, 2026-06-02,
2026-07-02. Đối chiếu expected_totals.json ở baseline bằng SQL trên output dbt
cho global/customer/product/side/date; kiểm tra row-level money, daily active [1,1,2],
LEFT JOIN zero-trade, cutoff và recency 30/31/60. Thêm distractor dataset cùng UUID,
provider khác cùng pair/day và future rows để kiểm tra fan-out/scope.

Test DB/container được giữ để review; logs, manifest và run_results nằm trong ignored
data/verify_2b_*/. tmpfs chỉ là dữ liệu test tạm, không phải persistence/backup của demo.
Collector .venv và nguồn demo được giữ; source checksums được so sánh trước/sau.
Chưa đưa tests vào CI. analytics/target, analytics/logs và .venv-dbt được Git ignore.

## Metabase dashboard

Truy cập **http://localhost:3000/dashboard/2** trên máy hiện tại. Khi tái tạo ở
metadata DB mới, ID có thể khác: setup script in URL thực tế và lưu data/metabase-state.json.
Dashboard: **Investment Pipeline | Synthetic Trading**. Nhãn trên dashboard ghi rõ
synthetic customers/executions, Coinbase daily-close reference, portfolio-v1 và cutoff
2026-10-27 UTC. Không gọi trade volume là revenue.

### Setup và tái tạo

Dữ liệu analytics phải có sẵn. Với clone mới cần chạy các bước collector,
synthetic và dbt ở trên trước; Git không chứa database/volume của máy hiện tại.

```bash
.venv/bin/python scripts/init_metabase_env.py
.venv/bin/python scripts/setup_metabase_role.py
docker compose up -d --wait metabase
.venv/bin/python scripts/setup_metabase.py
.venv/bin/python scripts/verify_metabase.py
```

init_metabase_env chỉ tạo secret còn thiếu/placeholder và giữ giá trị đã có,
không tự rotate password/encryption key. POSTGRES_PASSWORD phải được cấu hình
như phần setup; secrets Metabase riêng được sinh tự động vào .env mode 0600.
Đăng nhập bằng METABASE_ADMIN_EMAIL (mặc định admin@investment.local) và
METABASE_ADMIN_PASSWORD trong .env; mở file bằng editor để xem, không đưa vào Git.
Đây là local administrator của ứng dụng, khác với database query user.

Image metabase/metabase:v0.64.1 đã kiểm tra manifest có linux/arm64 và đã chạy native
arm64 trên máy này. App bind loopback 3000; có thể đổi METABASE_PORT nếu bận.
Không sửa ports/services/volumes cũ. metabase_db là PostgreSQL metadata riêng,
không publish host port, dùng named volume metabase_metadata. Không dùng H2.
Metabase kết nối business DB qua postgres:5432 trong Compose network bằng
**metabase_reader**, không dùng investment/owner. Role chỉ có analytics SELECT,
không có UPDATE/INSERT/DELETE/TRUNCATE/schema CREATE; default privileges giữ SELECT
cho bảng/view do cùng dbt owner tạo mới. Setup role cần chạy bằng owner local,
nhưng dashboard query connection chỉ lưu reader credentials.

Giữ .env cùng METABASE_ENCRYPTION_KEY và metadata volume để có thể đọc lại database
credentials đã mã hóa. Không xóa/reset volume để xử lý login hoặc startup lỗi.
Các lệnh setup tái chạy update đúng collection/cards/dashboard theo tên, không nhân
bản project dashboard. Script quản lý layout và nội dung này; thay SQL trong repo
rồi chạy lại setup sẽ cập nhật cards tương ứng. Metabase API không versioned;
script đã kiểm tra với OpenAPI của chính container v0.64.1.

### Cards và filters

SQL của 9 cards ở metabase/cards/*.sql:

- Bốn KPI: trade volume USD, fees USD, matched trades, distinct active customers.
- Hai biểu đồ ngày: volume và fees; LEFT JOIN dim_date để đủ 180 ngày, có 64 ngày zero.
- Stacked bar volume theo asset và BUY/SELL, query fact; không đổi grain daily mart.
- Customer segments và bảng top 15 khách theo tổng phí: snapshot cố định 2026-10-27.

From/To là ngày UTC inclusive, mặc định 2026-05-01/2026-10-27; asset BTC/ETH/SOL
mặc định tất cả. Filters chỉ nối vào 7 trading cards. Distinct customers được tính
trực tiếp từ fact ở phạm vi đã lọc, không cộng distinct theo ngày/tài sản/side.
Calendar padding lọc trades trước LEFT JOIN; không đặt asset filter ở WHERE sau join
vì sẽ làm mất zero-trade days. Range demo luôn giới hạn trong 180 ngày dữ liệu.

Hai customer cards không có template tags hay filter mappings. Chúng giữ đủ 1.000
khách tại cutoff, kể cả khi chọn tháng 6/ETH; đây không phải customer segmentation
được tính lại theo asset hoặc date filter. Muốn đổi cutoff snapshot phải dbt build
với cấu hình mới và sửa dashboard contract có chủ đích; dashboard này không giả lập
snapshot mới bằng filter trên snapshot cũ.

### Kiểm chứng và ảnh thật

```bash
.venv/bin/python scripts/verify_metabase.py
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_3.sql
# Persistence: không xóa hoặc recreate metadata volume.
docker compose restart metabase
docker compose up -d --wait metabase
.venv/bin/python scripts/verify_metabase.py
```

Verifier gọi các cards thật qua Metabase API, so với SQL bound độc lập trên PostgreSQL
bằng reader role; kiểm tra toàn bộ 180/30 daily rows, asset/side totals, top 15, segments,
filter mappings và UPDATE bị ACL từ chối ngay cả khi tắt transaction_read_only.
Đã so dashboard/card IDs, SQL, filter definitions và visualization settings trước/sau restart.

| Phạm vi trade filters | Trades | Volume USD | Fees USD | Distinct customers |
| --- | ---: | ---: | ---: | ---: |
| Mặc định | 50000 | 615063969.51 | 615063.60 | 900 |
| 2026-06-01..2026-06-30 | 9300 | 100591957.72 | 100591.72 | 900 |
| ETH, toàn kỳ | 16667 | 17344749.48 | 17345.27 | 300 |
| Tháng 6 + ETH | 3100 | 2633325.78 | 2633.37 | 300 |

Ảnh chụp **UI thật, không mock**, ở viewport hẹp của in-app browser, chỉ thể hiện
phần dashboard đang thấy; không phải ảnh ghép toàn bộ dashboard desktop:
[default volume/fees](docs/screenshots/metabase-default-kpis.jpg),
[default trade/customer counts](docs/screenshots/metabase-default-counts.jpg),
[tháng 6 + ETH](docs/screenshots/metabase-june-eth.jpg).

Provisioning script tạo sẵn cards, layout và filter mappings. Mở URL được script
in ra, đăng nhập bằng credentials trong .env. Date/asset filters đã được kiểm tra
qua API và in-app browser; layout desktop Chrome/Safari chưa được agent kiểm chứng.
Chưa cấu hình SMTP, password reset qua email hoặc public dashboard sharing.

Tài liệu [verification](docs/verification.md) ghi bằng chứng và phần chưa chạy;
Airflow, CI, AI, RFM và retention nằm ngoài phạm vi triển khai.
