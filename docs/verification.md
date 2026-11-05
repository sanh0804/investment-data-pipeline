# Verification — chặng 1, 2A, 2B, 3, 4A và 4B

Trạng thái hiện tại: chặng 1 có commit `4153807`, 2A có `933071b`, 2B có `5993a26`.
Kết quả chặng 4B ở cuối tài liệu. Các phần trước giữ bằng chứng lịch sử tại thời điểm
kiểm chứng; các dòng “chưa commit/chưa Metabase” không mô tả trạng thái sau chặng 3.

Ngày kiểm tra: 2026-11-01 (Asia/Ho_Chi_Minh). Kết quả dưới đây là các lệnh đã chạy
trên máy local; phân biệt dữ liệu API live với unit tests dùng dữ liệu giả.

## Đã quan sát ban đầu

- macOS 26.4, build 25E246; `uname -m`: arm64.
- Python 3.13.5; Docker 28.3.3; Compose v2.39.2-desktop.1.
- Docker daemon: linux aarch64. Git 2.49.0. Host psql 16.11.
- Trước triển khai có `my-postgres` tại 5432 và `my-pgadmin` tại 8080.
- Probe live Coinbase BTC-USD ngày 01/11/2026: HTTP 200, không có API key.
  Params: granularity 86400, start 2026-10-25T00:00:00Z, end 2026-10-28T00:00:00Z.
  Response: 4 nến giảm dần, bao gồm nến tại end; cần lọc còn 3 ngày.

## Kết quả đã chạy

| Kiểm tra | Kết quả thực tế |
| --- | --- |
| Tạo virtualenv, cài requirements-dev | Thành công, binary psycopg cho macOS arm64 |
| `python -m pip check` | No broken requirements found |
| `docker compose up -d --wait` | `de_pj-postgres-1` healthy, bind `127.0.0.1:55432` |
| Architecture image | arm64; PostgreSQL server 16.15, aarch64 |
| `python -m pipeline.init_db` hai lần | Thành công; giữ nguyên 2 raw rows và 3 normalized rows |
| `python -m pytest -q` | 22 passed |
| Collector live lần 1 | HTTP 200, 3 normalized days |
| Collector live lần 2 | HTTP 200, vẫn 3 normalized days; raw tăng lên 2 |
| Replay cùng raw response trong hai transaction | 3 → 3 rows; toàn bộ business values bằng nhau |
| `sql/verify.sql` qua psql với ON_ERROR_STOP | Không duplicate key, không missing date, 0 invalid UTC buckets |
| Kiểm tra checksum | SHA-256 của body bytes khớp metadata |
| Upsert giá sửa trong transaction kiểm chứng | Cập nhật cùng key, vẫn 3 rows; rollback để giữ giá live |
| Batch có row thứ hai vi phạm volume >= 0 | CheckViolation; cả batch rollback, dữ liệu trước/sau bằng nhau |
| `.gitignore` | `git check-ignore` xác nhận .env và .venv bị bỏ qua |
| Restart PostgreSQL rồi đợi healthcheck | Healthy; dữ liệu vẫn raw=2, normalized=3 |

Image đã dùng:
`postgres@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938`.
Compose dùng tag `16-bookworm`; digest này là bằng chứng lần chạy, chưa pin trong Compose.

### Bằng chứng live

Request: BTC-USD, daily granularity 86400, `[2026-10-25, 2026-10-28)` UTC.
Response mỗi lần có 4 candles; lọc lại còn 3 ngày hoàn tất.
Không dùng fixture để load database.

| Lần chạy | fetch_id | fetched_at UTC |
| --- | --- | --- |
| 1 | `69752bdf-7d31-460c-b985-678de2707f94` | 2026-11-01 15:08:50.202176+00 |
| 2 | `4d1d2c0c-b408-40f5-b0be-07d47e32e99a` | 2026-11-01 15:08:50.752181+00 |

Cả hai body: 243 bytes, SHA-256
`dc35b3f2faff753af30b4248345a0ab52b46e1ff757dceb40b6bb796a69a28d4`.

| price_date UTC | close_price USD |
| --- | ---: |
| 2026-10-25 | 80053.16 |
| 2026-10-26 | 80008.79 |
| 2026-10-27 | 80799.51 |

Lệnh replay đã chạy:

```bash
.venv/bin/python -m pipeline.verify_idempotency --fetch-id 4d1d2c0c-b408-40f5-b0be-07d47e32e99a
```

Output: `passed=true`, `rows_after_first=3`, `rows_after_second=3`,
`business_values_equal=true`. Replay không gọi API và không thêm raw attempt.
Metadata `last_fetch_id` trỏ về fetch lần 2. Các SQL check trong `sql/verify.sql`
trả zero duplicate groups, zero missing dates và `invalid_utc_buckets=0`.

### Kiểm chứng lỗi và transaction

22 unit tests dùng synthetic arrays/HTTP mocks, kiểm tra precision Decimal,
timestamp seconds/midnight UTC, order và exclusive end, missing/duplicate day,
OHLC/volume lỗi, current day, retry timeout/503/429, Retry-After, attempts limit,
không retry 400/401/403/TLS errors và cấu hình thiếu một date boundary.
Đây là mô phỏng; không tuyên bố đã gây lỗi 429 trên API thật.

Kiểm chứng database bổ sung bằng Python/psycopg trên payload live đã lưu:
đổi close của ngày đầu thành low trong `transaction(force_rollback=True)`, xác nhận
upsert cập nhật giá cùng key và rollback về dữ liệu gốc. Sau đó gọi upsert cho
ngày đầu đã sửa cùng ngày thứ hai có `volume=-1` trong một transaction; PostgreSQL
raise CheckViolation, đối chiếu snapshot trước/sau bằng nhau. Không để lại giá giả
hoặc xóa dữ liệu. Các kiểm chứng này chạy thủ công, không nằm trong 22 unit tests.

### Khả năng tái chạy

Đã khởi tạo Git local trên branch `main`, chưa commit hoặc cấu hình remote.
`.env` được tạo bằng mật khẩu local ngẫu nhiên, quyền 0600, không xuất ra log.
Không dùng API key. Hướng dẫn setup, SQL và replay có trong README.

## Chưa kiểm chứng / ngoài phạm vi

- Không cố tình tạo 429 hoặc outage trên API thật. Retry/permanent failures đã kiểm tra bằng mocks.
- Chưa kiểm tra concurrent collector, lịch sử >299 ngày, production load hoặc network khác.
- Chưa thử Intel macOS, Linux host hay Windows; không suy ra hỗ trợ chỉ từ image multi-arch.
- Không triển khai dbt, Airflow, Metabase, GitHub Actions, ETH/SOL hay customer/trade datasets.
- Chưa push remote hoặc commit; không dùng API key.
- Chưa kiểm tra hard kill trong lúc commit, failover, backup/restore hay database outage.
- Chưa có run-status table: raw HTTP 200 không tự chứng minh normalized đã commit.

## Chặng 2A — kết quả đã chạy ngày 2026-11-01

### Git và kiểm tra trước khi triển khai

- `git status --branch --short`: main, working tree sạch; không có staged changes.
- `git log`: đã có `4153807 Complete stage 1: BTC daily price pipeline`.
  Không tạo commit rỗng vì chặng 1 đã được commit trước yêu cầu này.
- Đã xem diff/cached diff và tracked file list; không có .env, .venv hoặc data local.
  So khớp nội dung tracked với password local bằng script, không in password:
  không tìm thấy password trong tracked files. .env.example chỉ có placeholder.
- Chưa push hoặc thay đổi remote. Các thay đổi 2A để working tree cho user review.

### Xác minh nguồn và giới hạn thực tế

Probe live không dùng API key:

| Endpoint | Kết quả |
| --- | --- |
| `/products/BTC-USD` | HTTP 200, online, base BTC, quote USD, trading_disabled=false |
| `/products/ETH-USD` | HTTP 200, online, base ETH, quote USD, trading_disabled=false |
| `/products/SOL-USD` | HTTP 200, online, base SOL, quote USD, trading_disabled=false |
| BTC daily candles start 2025-12-01, end 2026-10-28 | HTTP 400, count of aggregations requested exceeds 300 |

Product tồn tại là một check riêng; coverage 180 ngày được xác minh bằng candles
thực tế, không suy ra từ product metadata. Không cố tình tạo 429 hay xác minh
thực nghiệm trần requests/second. Mức 10 req/s, burst 15 theo tài liệu Coinbase.
Chưa kiểm thử chính xác ngưỡng request 299/300 ngày trên API live; window 60 ngày
đã chạy và probe vượt 300 ngày xác nhận nguồn từ chối request quá lớn.

### Thu thập 180 ngày, biên và rerun

Range cố định `[2026-05-01,2026-10-28)` UTC = 180 completed days. Với mỗi product:

1. `[2026-05-01,2026-06-30)`
2. `[2026-06-30,2026-08-29)`
3. `[2026-08-29,2026-10-28)`

Hai lần collector live đều thành công 9 requests (3/product), tất cả HTTP 200 ở
attempt 1. Mỗi response thực tế 61 candles, sau lọc end-exclusive còn 60/window.
Lần 1 fetched_at UTC 15:39:55–15:40:08; lần 2 15:42:54–15:43:03 ngày 01/11/2026.

| Product | Count sau lần 1 | Count sau lần 2 | Min UTC day | Max UTC day | Missing days |
| --- | ---: | ---: | --- | --- | ---: |
| BTC-USD | 180 | 180 | 2026-05-01 | 2026-10-27 | 0 |
| ETH-USD | 180 | 180 | 2026-05-01 | 2026-10-27 | 0 |
| SOL-USD | 180 | 180 | 2026-05-01 | 2026-10-27 | 0 |

Raw total: 2 của chặng 1 → 11 sau lần 1 → 20 sau lần 2. Raw cố ý tăng;
normalized total vẫn 540. Hai raw fetch UUID chặng 1 vẫn tồn tại sau migration/rerun.
Đã so sánh full snapshot giá gồm source/product/date/bucket/OHLCV, loại metadata
load ra khỏi checksum. Trước/sau rerun giống nhau:
`ca771846c18a7a2d5a8cc5ff79eb5cb47a4d34d5cceeb6b8625b203dd844b211`.

Exact-body replay hai lần cho window cuối của cả ba product:

| Product | fetch_id mới nhất window Aug 29→Oct 28 | Kết quả |
| --- | --- | --- |
| BTC-USD | `c2483627-83c6-446b-8d6c-58468027ab54` | 60→60 rows, business values equal |
| ETH-USD | `6df8d25d-d10c-4a51-afdf-e75f3bbf481d` | 60→60 rows, business values equal |
| SOL-USD | `48aa777c-b7ce-43af-bbd5-40f64ddbe721` | 60→60 rows, business values equal |

Replay xác minh SHA-256 body trước load; không thêm raw hoặc gọi API. Không phát
lại tất cả 18 response; full-range idempotency được kiểm chứng bằng hai live runs
và snapshot tất cả 540 business rows ở trên.

### Synthetic cố định và kiểm tra lại

Lệnh đã chạy **hai lần**, trước và sau lần collector live thứ hai:

```bash
.venv/bin/python -m pipeline.synthetic --dataset-id portfolio-v1 --seed 20261101 --start 2026-05-01 --end 2026-10-28
.venv/bin/python -m pipeline.verify_stage2a --dataset-id portfolio-v1
```

| Check | Kết quả đã quan sát |
| --- | --- |
| Customers | 1.000 ở cả hai lần |
| Matched trades | 50.000 ở cả hai lần |
| Profile never / regular / ceased | 100 / 600 / 300 |
| Customers có giao dịch / chưa giao dịch | 900 / 100 |
| Regular spacing | Tối đa 4 ngày; trải từ ngày đầu đến ngày cuối |
| Ceased | Không có trade trong 60 ngày cuối |
| Orphan customer / price / raw lineage | 0 / 0 / 0 |
| Trade trước hoặc cùng created_at | 0 |
| Quantity/price/fee/formula violations | 0 |
| Notional tổng synthetic USD | 615063969.51 |
| Phí tổng synthetic USD (SUM per-trade fees) | 615063.60 |
| Stable IDs và full customer/trade rows khi rerun | Snapshot checksums bằng nhau |

Đây là tổng của **giao dịch synthetic**, không phải dữ liệu khớp lệnh thực, vốn,
holdings hoặc P&L. Execution_price có giả định daily close/slippage trong decisions.

Checksum business input closes (không gồm fetch metadata):
`0834a59786d9c3ee4199e4ebb10ace24428b674fc88ee5df43d67b7c46449074`.
Checksum dataset business content:
`89d347b57efe422381094b2a9d286c6c3444a0eb2b9d29dc68c55c12a921dc2e`.

Full snapshot hashes sau cả hai lần (bao gồm raw lineage gốc của trade):

- Customers: `2efe3e3e16c9136bf81c1783504be35a9734bef62bed2490b606d7cc30416a3c`.
- Trades: `60c2d51d69126023ff76483b82a3bcf4d3dbe9934835d7925cfe1db9069768ff`.

Đã thử gọi loader với cùng dataset_id nhưng seed `20261102` trong transaction:
ValueError “different inputs/content”, rollback, full customer/trade snapshots
không đổi. Check này chạy bằng script thủ công, không nằm trong unit test count.
Chưa gây price correction trên live table để thử nhánh changed-price; seed guard
đã được thử, không tuyên bố mọi nhánh registry guard đều đã integration-test.

### Fixture, SQL và automated checks

`python -m pytest -q`: **29 passed**. Gồm 22 tests cũ và 7 tests mới:
window covering/boundaries, missing later window hoặc product không ghi batch dở,
deterministic synthetic IDs/profiles/seed, money HALF_UP và fixture oracle.
Tests chỉ dùng in-memory/mock prices và CSV fixture; không có fixture DB loader.
Không dùng tests này làm bằng chứng HTTP live thành công.

Fixture riêng có 4 customers/6 trades; expected totals độc lập ở
`tests/fixtures/reconciliation/expected_totals.json`: notional 475.94 USD, fee 0.48 USD,
và tổng theo side/product/customer/date. Đã kiểm tra arithmetic từng dòng, FK nội
bộ, created_at ordering, stable keys, source=fixture và so mọi expected totals.
Fixture chỉ có trên filesystem, không ghi đè live.

Đã chạy `sql/verify_2a.sql` qua psql với `ON_ERROR_STOP=1`: 180 ngày/product,
không missing day, đúng profile counts, 0 orphan customer, 0 trades before customer,
0 invalid amounts; các totals khớp verifier Python. Verifier SQL tính lại công thức
bằng PostgreSQL NUMERIC/round, độc lập với hàm Decimal dùng để sinh tiền trong Python.
Đã chạy lại init_db: không áp dụng lại migrations, giữ dữ liệu.

Logs/checksum JSON local nằm trong ignored `data/` (không đưa vào Git).
Bằng chứng tóm tắt và fetch IDs ở tài liệu này đủ để đối chiếu DB hiện có;
clone repository không mang theo raw/DB volume hoặc các UUID local này.

Rà soát cuối: `git diff --check` không lỗi; candidate tracked/untracked files
không có .env/.venv/data hoặc password local, staged area vẫn rỗng. `pip check`
không có broken requirements, `docker compose config --quiet` hợp lệ và PostgreSQL
vẫn healthy. `.env` local chỉ đổi START_DATE sang 2026-05-01, giữ credentials.

### Chưa chạy / giới hạn sau 2A

- Chưa kiểm tra fresh database từ đầu; migration đã chạy trên DB có dữ liệu chặng 1
  và rerun migration runner. Không xóa volume/data để kiểm thử.
- Không xác minh lịch sử trước range 180 ngày, access từ mạng khác hoặc quota/throttling thực nghiệm.
- Chưa kiểm tra collector concurrency, price-correction registry integration, production load,
  recovery/hard kill, backup/restore, hay reproducibility xuyên mọi phiên bản Python.
- Chưa thêm run-status table, scheduler, dbt/reconciliation models, Airflow, Metabase hoặc CI.
- Chưa mô phỏng funds, balances, holdings, P&L, SCD2 hoặc retention.
- Chưa commit thay đổi 2A hoặc push. Dừng ở cuối 2A để user review.

## Chặng 2B — kết quả phiên 2026-11-01, rà soát cuối 2026-11-02

### Review, commit và môi trường

- Đã đọc code, docs, diff/cached diff, schema thực tế bằng information_schema và
  source counts: raw=20, prices=540, customers=1000, trades=50000.
- Trước khi sửa 2B, đã rà candidate filenames/content: không có .env, virtualenv,
  local data hoặc password local. Reviewed staged list rồi commit 2A thành
  `933071b Complete stage 2A: multi-asset prices and deterministic synthetic trades`.
  Không tạo commit rỗng và không push remote.
- Python thực tế 3.13.5. dbt-postgres 1.11.0 yêu cầu Python >=3.10.0, dbt-core
  1.11.0 yêu cầu >=3.10 theo installed package metadata. Adapter PyPI có classifier
  Python 3.13; cài/build thành công trong .venv-dbt riêng, psycopg2-binary 2.9.13.
- Collector requirements không thay đổi; `python -m pytest -q` vẫn **29 passed**,
  collector `pip check` không broken requirements. dbt packages không cài vào .venv.
- Credentials từ env qua profiles/wrapper; không ghi password vào YAML/CLI arguments.
  DBT_SCHEMA=market compile đã được thử: bị từ chối trước khi ghi source tables.

### Models và dbt build demo

Lệnh đã chạy:

```bash
.venv/bin/python scripts/run_dbt.py build --no-partial-parse --vars '{"dataset_id":"portfolio-v1","as_of_date":"2026-10-27"}'
```

Kết quả: **8 models, 88 data tests; PASS=96, WARN=0, ERROR=0, SKIP=0**.
Output vào analytics; sources synthetic/market hiện có. Không gọi API hoặc sinh lại
synthetic trong chặng 2B.

| Model | Materialization | Rows thực tế |
| --- | --- | ---: |
| stg_customers | view | 1000 |
| stg_trades | view | 50000 |
| stg_asset_prices | view | 540 |
| dim_assets | table | 3 |
| dim_date | table | 210 |
| fact_trades | table | 50000 |
| mart_daily_trading | table | 116 |
| mart_customer_summary | table | 1000 |

Daily mart có 116 ngày có trades, không fill ngày zero-trade; không nhầm với
180 ngày coverage giá. dim_date còn bao gồm customer creation từ 2026-04-01.
Cutoff inclusive 2026-10-27 UTC; max trade_date 2026-10-27, không có trade vượt cutoff.

### Đối soát trên dữ liệu synthetic portfolio-v1

Đã chạy sql/verify_2b.sql qua psql với ON_ERROR_STOP=1 và query output tables:

| Layer | Trade count | Trade volume USD | Fees USD |
| --- | ---: | ---: | ---: |
| staging | 50000 | 615063969.51 | 615063.60 |
| fact | 50000 | 615063969.51 | 615063.60 |
| daily mart | 50000 | 615063969.51 | 615063.60 |
| customer summary | 50000 | 615063969.51 | 615063.60 |

Trade volume không phải revenue; các executions và fees ở bảng này hoàn toàn
synthetic. Giá source daily close ở market vẫn là API live đã thu thập từ 2A.

- Fact không mất hoặc nhân đôi execution: unique tests, row-level preservation
  và totals reconciliation đều pass. Không dùng DISTINCT fact để chữa fan-out.
- Whole-range active customers = 900, COUNT DISTINCT trực tiếp từ fact, không cộng daily counts.
- Summary giữ 1000 customers của dataset đã tạo trước cutoff; customer-count difference=0.
- Segments từ trades: active=600, inactive=300, never_traded=100; không SELECT profile.
- 100 zero-trade customers đều totals=0, first/last dates và recency=NULL;
  invalid-never-traded counter=0. Không có customer bị mất vì INNER JOIN.
- Tests relationships, unique/not_null, accepted values, cutoff, money và các đối soát
  đều pass. docs/models.md và models/schema.yml ghi grain/keys/columns/metrics/lineage.

### Fresh migrations và fixture thực thi qua chính dbt models

Đã chạy `scripts/verify_dbt.py` thành công. Lần cuối:

- Server `de_pj_2b_test-postgres_test-1`, host port 55433, database mới
  `verify_2b_4d859dd00714`. Lần thành công trước đó là `verify_2b_6c6afd061714`.
- `docker inspect`: HostConfig.Tmpfs có /var/lib/postgresql/data, Mounts=[];
  không mount volume PostgreSQL demo. Test DB/container được giữ lại để review.
- Chạy migration 001/002 trên database mới, xác minh schema_migrations có 2 rows,
  bảng chuẩn rỗng và volume_base tồn tại; rerun không áp dụng lại migrations.
- Fixture chỉ load vào fixture_input. Các bảng market.daily_prices và synthetic.trades
  chuẩn trong test DB vẫn rỗng sau builds; demo DB không nhận fixture.

Mỗi case chạy cùng 8 models và 88 data tests, không có bản SQL models riêng cho fixture:

| Case | as_of_date UTC | Kết quả build | Kiểm chứng bổ sung |
| --- | --- | --- | --- |
| baseline | 2026-05-03 | 96 nodes thành công | 4 customers/6 trades; mọi expected JSON group khớp |
| early | 2026-05-01 | 96 nodes thành công | Chỉ 2 trades ngày đầu; khách chưa trade ở cutoff vẫn còn |
| threshold | 2026-06-02 | 96 nodes thành công | Recency 30 → active, 31 → at_risk |
| inactive | 2026-07-02 | 96 nodes thành công | Recency 60/61 → inactive; zero-trade vẫn NULL/never_traded |

Baseline output dbt được SQL-query và đối chiếu trực tiếp
`tests/fixtures/reconciliation/expected_totals.json`: 475.94 USD notional, 0.48 USD
phí, đúng các totals theo customer/product/side/date, cả customer không giao dịch.
Row-level quantity/execution/notional/fee cũng khớp CSV. Daily active customers
độc lập là [1,1,2], gồm ngày một khách giao dịch hai assets nhưng chỉ được đếm một lần.
Customer fixture profile ceased vẫn active tại baseline, chứng minh segment từ behavior.

Test inputs có thêm dataset distractor cùng customer/trade UUID, provider khác cùng
product/day và future customer/trade. Output selected fixture-v1 vẫn đúng 4 customers/6
trades ở baseline, không fan-out hoặc leak future rows. Các assertions ngày/recency,
zero totals và NULL semantics đều thực hiện trên output dbt SQL, không chỉ fixture Python.

Script so checksum toàn bộ raw/market/synthetic demo tables trước/sau; kết quả
`demo_sources_unchanged=true` ở cả hai lần chạy thành công. Không xóa DB/volume/data.
Logs, manifests, run_results và report.json nằm trong ignored data/verify_2b_*/;
artifacts mỗi case có target path riêng. Chúng là kiểm chứng fixture/synthetic,
không phải lần thu thập API live mới.

### Docs và giới hạn còn lại

`dbt docs generate` đã chạy thành công, catalog.json ở analytics/target. README
có setup/build/query/fixture commands; docs/models.md, decisions và learning-notes
đã cập nhật. Lần compile ban đầu guard schema gặp tên audit schema nội bộ của dbt;
đã sửa macro luôn trả validated target schema, rồi build/test thành công như trên.

Rà soát cuối: dbt `pip check` không broken requirements; diff/whitespace checks
không lỗi, staged area rỗng. Candidate file scan không có password local, .env,
virtualenv, dbt target/logs hoặc local data. Cả hai PostgreSQL services healthy;
source counts vẫn raw=20, prices=540, customers=1000, trades=50000.

- Chưa chạy docs serve (chỉ ghi lệnh tùy chọn), chưa deploy dashboard hoặc CI.
- Chưa thử Python/OS khác; direct dbt versions đã pin, chưa có transitive lockfile.
- Chưa có dbt role riêng SELECT-only trên sources; đang dùng tài khoản local dev,
  bảo vệ schema output bằng macro guard, không tuyên bố đây là database permission boundary.
- Chưa kiểm tra concurrent source writes, concurrent builds cùng schema, production
  performance/load, backup/restore hoặc fault injection. Staging views thay đổi theo
  sources; cần dbt build lại tables sau source updates để đối soát cùng snapshot.
- Chưa có incremental models/historical snapshots; mỗi schema là một dataset/cutoff build.
- Chưa triển khai Airflow, Metabase, AI, SCD2, retention, RFM scoring, balances, holdings hoặc P&L.
- Chưa commit thay đổi 2B hoặc push. Dừng ở cuối 2B để user review.

## Chặng 3 — Metabase dashboard, 2026-11-02

### Grain và 116/180 ngày: kiểm chứng trước khi triển khai

Đã đọc model SQL, columns thực tế và unique test của mart_daily_trading:
columns dataset_id/trade_date/as_of_date/trade_count/active_customers/trade_volume_usd/fees_usd.
**116 rows = 116 distinct(dataset_id,trade_date)**. Grain là dataset/ngày có trade,
không có asset hoặc side. Docs/models.md và README đã đồng bộ với thực tế; unique
key/test vẫn chính xác, không sửa model SQL hoặc dữ liệu.

| Tháng | Ngày giá/calendar | Ngày có trade | Trades |
| --- | ---: | ---: | ---: |
| 2026-05 | 31 | 20 | 10500 |
| 2026-06 | 30 | 22 | 9300 |
| 2026-07 | 31 | 26 | 10100 |
| 2026-08 | 31 | 23 | 9400 |
| 2026-09 | 30 | 16 | 5900 |
| 2026-10 tới ngày 27 | 27 | 9 | 4800 |
| Tổng | 180 | 116 | 50000 |

SQL theo customer/profile xác nhận: 100 never có 0 trades, 100 regular có 55 trades,
500 regular có 56, 300 ceased có 55. Regular spacing từng khách là 3/4 ngày:
24.200 gaps 3 ngày và 8.700 gaps 4 ngày. Ceased spacing 2/3 ngày: 12.900 gaps 2 ngày,
3.300 gaps 3 ngày, trade cuối 2026-08-28. Regular có union 91 ngày, ceased 55,
intersection xác nhận bằng INTERSECT là 30 → union 116, còn 64 ngày zero-trade.
Lịch trong cùng cohort được đồng bộ bởi generator; regular không có nghĩa daily
activity hoặc independent calendars. Không sửa input để làm đẹp phân bố.

### Git và services

Đã review diff/staged, so candidate content với password local và kiểm tra artifacts;
commit 2B thành **5993a26** (`Complete stage 2B: dbt analytics and isolated reconciliation tests`).
Không tạo commit rỗng, không push. Ảnh và code chặng 3 hiện để working tree cho review.

`uname -m` = arm64. Manifest image metabase/metabase:v0.64.1 có linux/arm64;
Docker image chạy native arm64. Thêm hai services vào compose.yaml:

- metabase: loopback 127.0.0.1:3000, healthcheck /api/health.
- metabase_db: PostgreSQL riêng lưu metadata vào named volume de_pj_metabase_metadata,
  không publish host port. MB_DB_TYPE=postgres; không dùng H2.

Các service/cổng cũ giữ nguyên: business PostgreSQL 55432, test PostgreSQL 55433,
my-postgres 5432, my-pgadmin 8080. Không xóa/reset volume hoặc dữ liệu business.
METABASE_DB_PASSWORD, reader/admin passwords và encryption key mới chỉ ở .env mode 0600.
init_metabase_env.py đã chạy: 0 values bị rotate/ghi đè khi env đã đầy đủ.

### Read-only connection và dashboard provisioning

Tạo metabase_reader có LOGIN, không SUPERUSER/CREATEDB/CREATEROLE/REPLICATION,
không role memberships/ownership của business tables; chỉ analytics USAGE/SELECT,
default_transaction_read_only=on và statement timeout 30s. Default privileges cấp
SELECT cho analytics tables/views mới do cùng owner tạo. Tài khoản owner/dev chỉ
chạy setup grants; **Metabase data source connection user=metabase_reader**.

Đã thực thi SELECT bằng reader và thử UPDATE analytics.fact_trades WHERE false
sau SET LOCAL transaction_read_only=off: **InsufficientPrivilege**, chứng minh ACL
ngăn ghi, không chỉ dựa vào transaction default. Direct raw.price_fetches SELECT
cũng bị từ chối. Không row business nào được sửa bởi kiểm tra này.

Qua API local v0.64.1 đã bootstrap admin, cấu hình report timezone UTC, collection,
read-only database connection, 9 SQL questions và một project dashboard:

- Dashboard ID 2, collection 5, analytics data source ID 2.
- Cards 40..48, SQL được version ở metabase/cards/.
- URL máy này: http://localhost:3000/dashboard/2.
- Rerun setup giữ cùng dashboard/card IDs, không nhân đôi project dashboard.
- Nhãn: synthetic customers/executions, Coinbase daily-close reference,
  portfolio-v1, cutoff 2026-10-27 UTC, trade volume không phải revenue.

Date from/to và asset chỉ map vào 7 trading cards. Hai customer snapshot cards có
zero parameter mappings và không có native template tags; không giả vờ tái tính
snapshot theo các filters. Asset/side breakdown query fact, không đổi grain mart.
Daily volume/fees dùng dim_date + filtered fact LEFT JOIN: 180 rows, 64 zero-trade days.

### Đối soát cards thật với PostgreSQL SQL độc lập

scripts/verify_metabase.py gọi POST card/query trên các saved cards, so với bound SQL
độc lập bằng reader connection. Đã chạy thành công trước **và sau** restart:

| Trade scope | Count | Volume USD | Fees USD | Distinct active customers | Calendar / zero days |
| --- | ---: | ---: | ---: | ---: | --- |
| Default 2026-05-01..2026-10-27 | 50000 | 615063969.51 | 615063.60 | 900 | 180 / 64 |
| June 1..30 | 9300 | 100591957.72 | 100591.72 | 900 | 30 / 8 |
| ETH toàn kỳ | 16667 | 17344749.48 | 17345.27 | 300 | 180 / 64 |
| June + ETH | 3100 | 2633325.78 | 2633.37 | 300 | 30 / 8 |

So từng daily date/volume/fees, tổng asset/side, snapshot segment counts và top 15
customer fees đều khớp SQL. Segments: active=600, inactive=300, never_traded=100.
Global distinct lấy từ fact trong đúng scope, không SUM daily distinct. SQL thuần
sql/verify_3.sql cũng đã chạy qua psql ON_ERROR_STOP=1 và khớp các con số trên.
Không gọi API giá mới, không tạo fixture hoặc sinh lại trades ở chặng 3.

### Persistence và UI đã thực hiện

Đã lưu snapshot dashboard ID, card IDs, toàn bộ SQL queries/template tags, filters và
visualization settings; chạy docker compose restart metabase, đợi healthy và login lại.
Snapshot trước/sau **bằng nhau**. Đã query lại tất cả cases, vẫn pass. Metadata PostgreSQL
và volume tồn tại độc lập với app; chưa thử restart metadata DB hoặc backup/restore.

Thao tác UI thật trong in-app browser:

1. Mở localhost:3000, đăng nhập admin local từ .env; bỏ chọn Remember me, không lưu password.
2. Mở dashboard 2, đọc KPI mặc định 615063969.51 / 615063.60 / 50000 / 900.
3. Mở Asset dropdown, chọn ETH và Add filter; UI ra 16667 trades và 300 customers.
4. Dùng date pickers đổi From=2026-06-01, To=2026-06-30, giữ ETH;
   UI ra 2633325.78 / 2633.37 / 3100 / 300, khớp independent SQL.
5. Customer segment chart và top-fee table vẫn giữ fixed snapshot; nhãn đã ghi rõ.
6. Sau restart mở lại default URL, kiểm tra số mặc định và lưu ảnh thật, giữ tab mở.

Ảnh chụp JPG nguyên từ UI, không tạo mock hay ảnh ghép:

- docs/screenshots/metabase-default-kpis.jpg — volume/fees mặc định.
- docs/screenshots/metabase-default-counts.jpg — 50000 trades/900 customers.
- docs/screenshots/metabase-june-eth.jpg — UI June+ETH volume/fees và filter values.

Viewport in-app browser hẹp, ảnh chỉ chụp phần đang thấy, không phải toàn dashboard
ở desktop. Full-page screenshot ban đầu không bao hết nested scroll container;
đã cuộn thật tới KPI rồi chụp lại. Không resize hoặc dựng lại chart để giả screenshot.
Thử native Chrome qua UI automation bị timeout; không coi đó là Chrome UI đã pass.
Trên Mac user chỉ cần mở URL bằng Chrome/Safari, login từ .env để review bố cục rộng;
không cần tự tạo cards/dashboard hoặc nối filters.

Logs/report IDs/persistence snapshots nằm trong ignored data/metabase-*.json/log.
Local OpenAPI được đọc từ container để xác minh endpoint payload; ví dụ v0.64 yêu cầu
parameter ID trong card/query đã được xử lý, sau đó tất cả cases pass.

Rà soát cuối: market business checksum và full customer/trade snapshots vẫn bằng
các snapshot 2A; reader không có role memberships/table ownership hoặc elevated flags.
Candidate-file scan kiểm tra mọi local password/encryption key, không phát hiện secrets
hoặc local artifacts; staged area rỗng. Compose config và Python compile checks pass.
Screenshot KPI mặc định có SHA-256
`b1ffc16481c2245ed60468c1908e0cd4ec57b501edfbc42ff74b32f6cbd3c414`,
khớp nguyên bytes screenshot đã emit từ UI và file trong repo; cả ba JPG đã đọc lại
để xác nhận đúng nội dung KPI, không chỉ dựa vào tên file.

### Phần chưa chạy / giới hạn

- Chưa kiểm tra Chrome desktop/Safari, chia sẻ public, HTTPS/reverse proxy hoặc deploy ngoài Mac local.
- Chưa thử metadata PostgreSQL restart, backup/restore, volume migration hoặc mất encryption key.
- Chưa thử fresh full pipeline + Metabase từ một clone trống; đã tạo mới metadata DB,
  bootstrap app, rerun provisioning và kiểm chứng trên analytics hiện có.
- Không tự refresh dbt snapshot khi thay date filters; customer cards luôn cutoff cố định.
- Chưa có CI/Airflow/AI/RFM/retention. Không bật public sharing hoặc cấu hình SMTP.
- Chưa commit chặng 3 hoặc push. Dừng ở cuối chặng 3 để review.

### Rà soát trước commit chặng 3 — 2026-11-02

Theo yêu cầu commit sau review: đã xem diff và các file mới, kiểm tra 76 candidate
files với secrets local mà không in giá trị; không phát hiện secrets, private key
hoặc artifacts bị cấm. `docker compose config --quiet`, Python AST parse của các
scripts Metabase và `git diff --check` đều pass. Không chạy lại các integration
checks ở phần trên trong lần commit này. Hai ảnh PNG bổ sung trong screenshots
đã được xem: là dashboard desktop do user cung cấp, không phải kiểm thử Chrome
mới do agent thực hiện. Commit chặng 3 local; không push remote.

## Chặng 4A — Airflow local, 2026-11-05

### Môi trường và phạm vi

Đã đọc collector/config/schema/Compose trước khi triển khai; cập nhật plan ở
`docs/plans/stage-4a.md` để phân biệt backfill ngày thiếu và reprocess ngày đã có.
Working tree ban đầu chỉ có plan chưa tracked; HEAD là `78976b3`. Không commit,
push, xóa volume hoặc dữ liệu để triển khai 4A.

Host `uname -m=arm64`, Python 3.13.5, RAM 16 GiB; Docker daemon aarch64 được cấp
8.218.034.176 bytes, Compose v2.39.2. Docker Desktop ban đầu tắt, đã mở lại.
Image `apache/airflow:3.3.2-python3.12` có manifest linux/arm64 và đã chạy native.
Base image digest:
`sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1`.
Upstream constraints Python 3.12 được lưu trong orchestration; image riêng dùng
requests 2.34.2, psycopg 3.3.5, python-dotenv 1.2.3 theo constraints. Collector host
requirements và môi trường dbt không đổi.

LocalExecutor, API server, scheduler, DAG processor, metadata PostgreSQL riêng;
không Redis/Celery/Docker socket. UI bind loopback 8081; business PostgreSQL 55432,
Metabase 3000 và các volume cũ giữ nguyên. Airflow metadata/auth/logs dùng ba named
volumes riêng. Init mới gặp PermissionError ở auth volume; đã sửa init cấp đúng
ownership thư mục rồi chạy migration bằng airflow user. Không reset volume.
Trong một lần recreate, Docker API proxy chờ nhiều phút trước khi start containers;
sau đó services lên healthy, không xác định chắc nguyên nhân và không coi là lỗi DAG.

### Live ingestion, backfill và reprocessing

DAG `market_prices_daily` có `collect_prices → verify_price_interval`;
CronDataIntervalTimetable midnight UTC explicit, catchup=False, một active run và
pool coinbase_serial một slot. Scoped role `airflow_collector` dùng raw SELECT/INSERT,
market SELECT/INSERT/UPDATE; không dùng owner để chạy tasks. Secrets ở .env mode0600,
không mount .env/host virtualenv vào containers. Init env rerun báo 0 values mới.

Các runs live đã thực sự thành công, cả collection và verification:

| Run | Phạm vi UTC [start,end) | Kết quả |
| --- | --- | --- |
| `scheduled__2026-11-05T00:00:00+00:00` | 2026-11-04 → 2026-11-05 | 3 products, 3 rows |
| `manual__live__20261105T095634069118` | 2026-10-28 → 2026-10-29 | 3 products, 3 rows |
| `manual__reprocess__20261105T095722791910` | Cùng ngày 2026-10-28 | Vẫn 3 keys, full OHLCV business snapshot bằng nhau |
| `backfill__2026-10-30T00:00:00+00:00` | 2026-10-29 → 2026-10-30 | Thành công |
| `backfill__2026-10-31T00:00:00+00:00` | 2026-10-30 → 2026-10-31 | Thành công |
| `backfill__2026-11-01T00:00:00+00:00` | 2026-10-31 → 2026-11-01 | Thành công |

Backfill ID 1 có max_active_runs=1, reprocess_behavior=none. CLI nhận exclusive end
2026-11-01 và chuyển thành logical-date bounds 2026-10-29..2026-10-31 theo API.
Run ID của Airflow này biểu diễn interval end; đã đối chiếu actual data_interval,
không suy ngày cần lấy chỉ từ chuỗi run_id.

Sau sáu runs thành công: raw 20→38, normalized 540→555, mỗi product 185 ngày.
Các ngày mới là Oct28..31 và Nov4, không tuyên bố coverage liên tục tới Nov4.
`sql/verify_4a.sql` qua psql ON_ERROR_STOP: Oct28..31 đủ 4 ngày/product,
missing_keys=0, invalid_lineage_or_utc=0. Không dùng fixture để load database live.

Exact-response replay bằng `pipeline.verify_idempotency` cho BTC ngày Oct28:
fetch_id `62313586-2f61-4390-8aa7-b79c24ffbf08`, SHA-256
`4b81a836a602f39338fe64f28e0d2d1460626fd198aa0cabb540b5c9e78ec5fb`.
Hai transaction trả rows_after_first=1, rows_after_second=1,
business_values_equal=true; không thêm raw hoặc gọi API. Không nhầm live re-fetch
với exact-body replay: provider có thể sửa business values khi fetch lại.

### Fault injection và task retry

`scripts/verify_airflow.py` đã chạy trên verification image riêng. Test DAGs chỉ
được copy vào image kiểm thử; production DAG functions được gọi lại trong task
process thật. HTTP là mock, PostgreSQL là service `airflow_fixture_db`, database
`investment_4a_test`, tmpfs, không host port và không mount business volume.
Migrations 001/002 đã chạy trên server mới này; raw/normalized ban đầu đều rỗng.

| Case | Bằng chứng thực tế |
| --- | --- |
| Missing second product | `verify_4a_failure`, run `manual__isolated__20261105T121936241035`: collection failed try1, verify upstream_failed; raw 0→2, normalized 0→0 |
| Retryable HTTP | `verify_4a_retry`, run `manual__isolated__20261105T121942488333`: ba mocked HTTP503 ở attempt1, task attempt2 thành công và verify success; raw 2→8, normalized 0→3 |

Failure case cố ý đặt retries=0 để kiểm tra trạng thái fail/blocked. Retry case đặt
delay 2 giây để kiểm thử; production dùng 5 phút. Các payload giá test không phải
response Coinbase live, dù dùng cùng schema/collector. Không có test flag hoặc
endpoint fixture trong production DAG.

Đã thử manual run thiếu conf trên DAG production:
`manual__invalid_boundaries__20261105T122033`: collect failed try1 dù cấu hình retries=1,
verify upstream_failed, raw trước/sau đều 38. Không fallback sang dates .env,
không gọi API hoặc retry lỗi cấu hình.

`python -m pytest -q`: **45 passed** (29 cũ, 16 mới cho intervals/lineage và malformed
JSON). Kiểm tra explicit dates thắng .env sai, naive/non-midnight/future/empty/too-long
ranges, checksum/values/lineage thiếu hoặc sai, và bảo toàn exception validation.
Tests cũ đã bổ sung assert retryable classification cho HTTP transient/permanent/TLS.
Collector và dbt `pip check` đều không broken requirements.

### Bảo toàn demo và phạm vi UI

So full-row snapshots: 20 raw cũ và 540 normalized prices trước Oct28 không đổi;
synthetic.datasets/customers/trades checksums bằng trước triển khai.
Analytics vẫn 50000 trades, 615063969.51 USD notional, 615063.60 USD fees và
900 distinct active customers; segments active600/inactive300/never100.
`verify_metabase.py` đã chạy lại thành công trên cả bốn filter cases, read-only ACL,
customer snapshot và ranking. Không chạy dbt build hay sinh lại synthetic.

UI automation: in-app browser không có sẵn, browser inventory rỗng. Không tạo ảnh
Airflow giả và không tuyên bố đã thao tác browser UI. Backend health và đăng nhập
API bằng local credentials đã thành công. Trên Mac: mở localhost:8081, login từ
.env, chọn market_prices_daily, mở run và xem task attempts/logs. Các thao tác UI
này để user kiểm tra; DAG được pause sau buổi kiểm chứng.

Logs, run reports và snapshots nằm trong ignored data/airflow-* và data/stage4a-*.
Không đưa chúng hoặc credentials vào Git. Các phần lịch sử trước giữ đúng bằng
chứng tại thời điểm chạy, kể cả ghi chú về phạm vi chưa có Airflow lúc đó.

### Native completed reprocessing và persistence

Đã chạy thêm backfill ID 2 với reprocess_behavior=completed cho Oct29, kết thúc
thành công. Airflow 3.3.2 tái sử dụng run
`backfill__2026-10-30T00:00:00+00:00`, hai task tăng try_number 1→2; không tạo
run ID mới. Kiểm tra ban đầu chờ run ID mới không phù hợp hành vi này; đã chuyển
sang đối chiếu backfill completed_at và task attempts. Mỗi product có hai raw
responses Oct29 cùng SHA-256; normalized giữ nguyên keys/values. Raw tăng 38→41,
normalized vẫn 555. Đây là re-fetch live, tách khỏi exact-body replay ở trên.

Đã kiểm tra sau recreate từ verification image về normal image: toàn bộ 7 run
records/task states lúc đó bằng snapshot trước recreate. Logs task collection
của manual live run lấy được qua API, gồm 16 entries. Sau native reprocessing,
lưu snapshot mới để không nhầm các thay đổi task attempts có chủ đích với mất
persistence. Deployment cuối dùng normal image, test DAG files không có trong
image này; metadata lịch sử test vẫn được giữ.

Lần restart cuối (sau reprocessing) đã so snapshot đủ 7 production run IDs,
config/intervals/states và từng task attempt/start/end: bằng nhau trước/sau.
Login API sau restart thành công, DAG paused=true. Task log cũ giữ đủ 16 entries
với content bằng nhau; named volumes metadata/auth/logs vẫn còn. Airflow image
`pip check` không broken requirements, runtime Python 3.12.14 aarch64;
normal DAG import-errors list rỗng. Collector role không có elevated flags,
role memberships hoặc table ownership trong business DB.

Rà soát cuối: 45 Python tests pass; base/normal/test Compose config --quiet hợp lệ,
git diff --check không lỗi. Scan 93 candidate tracked/untracked files với local
passwords/Fernet/JWT keys không phát hiện secrets/private keys hoặc artifacts;
Python syntax parse pass. Staged area vẫn rỗng. Đã kiểm tra status/backfill-status
CLI; backfill ID 2 completed, production history có 6 successful runs và 1 failed
run do cố ý thiếu manual conf. Native reprocessing tái sử dụng một trong 6 run IDs.
Tổng cuối raw=41, prices=555, customers=1000, trades=50000. Cả 21 HTTP attempts mới
ở DB live đều HTTP200 tại attempt1; HTTP503/failure/retry chỉ được gây bằng mocks.
Business sources demo và totals analytics được so lại sau native reprocessing,
vẫn không đổi. Các services Airflow/business/Metabase đều healthy; test tmpfs
server giữ nguyên để review, không xóa data. Code/Compose/docs 4A chưa commit/push.

### Chưa chạy / giới hạn sau 4A

- Chưa thao tác Airflow browser UI hoặc có screenshot mới: browser connector không
  có sẵn. API login, task logs, run/task states đã kiểm tra; user kiểm tra UI trên Mac.
- Chưa xác minh live API429/outage, database outage, hard kill giữa commit, concurrent
  collectors, overlapping manual/backfill hoặc external writes. Không dùng pool
  để tuyên bố khóa được các Python processes bên ngoài Airflow.
- Chưa kiểm tra metadata PostgreSQL restart, backup/restore, mất auth/Fernet/JWT key,
  upgrade Airflow, host/architecture khác, production load hoặc deployment public.
- SimpleAuthManager lưu password dạng plaintext trong auth volume riêng và chỉ
  phù hợp development loopback. Chưa SSO/TLS/SMTP/alerting; không production-ready.
- Chưa chạy pipeline đầy đủ từ clone trống; mới metadata Airflow và fixture DB đã
  khởi tạo trong lần này trên business database có sẵn.
- Chưa thêm CI, scheduled dbt/synthetic builds, run-status table nghiệp vụ, RFM,
  retention, AI, SCD2, balances, holdings hoặc P&L. Dừng sau 4A để review.

## Chặng 4B — GitHub Actions, 2026-11-05

### Triển khai và isolation

Đã đối chiếu ghi chép 4A ở README/decisions/verification và learning-notes local
trước khi mở scope CI. Các thay đổi 4A vẫn ở working tree; không tự commit hoặc
push để kích hoạt workflow. Plan 4B ở docs/plans/stage-4b.md.

`.github/workflows/ci.yml` có pull_request, push main và workflow_dispatch:
unit matrix Python3.12/3.13 trên ubuntu-24.04, PostgreSQL16 + fixture/dbt, và
Airflow production DAG parse trong pinned image với network=none.
Không dùng pull_request_target hoặc repository secrets; contents:read,
checkout persist-credentials=false, job timeout, concurrency cancellation và
artifact whitelist. Actions checkout v7.0.1/setup-python v7.1.0/upload-artifact
v7.0.2 pin full SHAs đã đọc từ official repositories; action metadata dùng Node24.

`scripts/verify_ci.py` không load .env, không đọc snapshot hoặc kết nối database
demo. Chỉ nhận CI_POSTGRES_* tường minh, từ chối user/database/host không thuộc
CI và ports55432/55433. Shared apply_migrations(conn) được tách từ init_db, giữ
nguyên numbered migrations/transaction behavior; wrapper dbt có load_local_env=False.
Fixture loader, oracle assertions và SQL models dùng lại từ verify_dbt, không có
bản models CI riêng. Password ci_fixture_only_password chỉ là credentials public
của PostgreSQL test tạm, không phải secret trên Mac.

### Kết quả thực sự đã chạy local

| Check | Kết quả |
| --- | --- |
| Python host3.13.5/macOS arm64 | 52 passed; JUnit ở ignored data/stage4b-unit-host.xml |
| Python Linux3.12.16/aarch64 | 52 passed; chạy trong CI reproduction image |
| Fresh migrations001/002 và rerun | Pass trên ci_verify_ae0faff9e11a, schema_migrations=2 |
| Baseline cutoff2026-05-03 | 8 models +88 tests, 96 nodes pass; 4 customers/6 trades |
| Early cutoff2026-05-01 | 96 nodes pass; summary/cutoff assertions pass |
| Threshold cutoff2026-06-02 | 96 nodes pass; recency30/31 và segments pass |
| Inactive cutoff2026-07-02 | 96 nodes pass; recency60/61/never semantics pass |
| Source-schema guard | Compile với DBT_SCHEMA=market bị từ chối đúng thông báo guard |
| Airflow DAG import/contract | Pass với network=none, không runtime credentials |
| actionlint1.7.12 | Workflow không lỗi |

52 tests gồm 45 tests sau4A và 7 checks mới cho CI connection guards, credentials
bắt buộc và wrapper không load .env/không đưa password vào argv. Test wrapper dùng
temporary fake executable để unit job không cần cài dbt.

Baseline output dbt so với expected_totals.json khớp 475.94 USD notional và 0.48 USD
fees; mọi group customer/product/side/date, row-level amounts và daily distinct
[1,1,2] đều pass. Mọi cutoff assert các bảng raw/market/synthetic chuẩn trong CI DB
vẫn rỗng; fixture chỉ ở fixture_input. Các test có dataset/provider distractors
và future rows dùng cùng assertions đã có, không mô tả fixture là dữ liệu API live.

Local reproduction dùng compose.ci.yaml/project de_pj_ci. ci_postgres ở loopback55434,
database bootstrap investment_ci, server tmpfs. docker inspect xác nhận
HostConfig.Tmpfs=/var/lib/postgresql/data, Mounts=[]; runner chỉ mount named volume
de_pj_ci_ci_artifacts, không bind repo/.env hoặc business volume. Container
`de-pj-ci-verification` exited0, được giữ lại cùng test DB để review. Artifacts đã
copy ra ignored data/ci-linux-artifacts/: report.json, per-case targets/logs và JUnit.
Không xóa test DB hoặc volume để kiểm chứng.

DAG parser ban đầu dùng include_examples argument của API cũ và bị TypeError;
đã chuyển sang airflow.dag_processing.dagbag.DagBag theo actual3.3.2 signature,
tắt examples bằng env và chạy lại thành công. DAG có hai tasks đúng dependencies,
CronDataIntervalTimetable, catchup=False, max_active_runs=1, pool và retries đúng.
Có deprecation warning cho import AirflowFailException từ airflow.exceptions
trong DAG4A; pinned3.3.2 vẫn parse pass. Chưa nâng Airflow hoặc sửa runtime chỉ để
che warning; cần xem lại alias khi upgrade.

Full source snapshots raw/market/synthetic demo trước/sau CI bằng nhau.
Airflow/Metabase services vẫn healthy; CI không thu giá mới, không sinh lại trades
hoặc build analytics demo. Scan101 candidate files không có local secrets,
private keys hoặc generated artifacts; Python syntax, README local links,
Compose CI config --quiet và git diff --check pass. Staged area rỗng.

### Chưa chạy / giới hạn sau 4B

- **Chưa chạy workflow trên GitHub-hosted runners**: workflow chưa commit/push,
  không có run URL hoặc green badge để công bố. Local Linux arm64/Debian image
  không được coi là bằng chứng Ubuntu24.04/x64 hosted jobs đã pass.
- Chưa chạy LinuxPython3.13 trong lần này; Python3.13 mới chạy trên macOS.
- Chưa cấu hình branch protection/required status checks; chưa xác minh upload/
  download artifacts, cache hit, fork PR permissions hoặc cancellation trên GitHub.
- DAG parse không kiểm chứng scheduler/retry/HTTP live; các bằng chứng runtime
  thuộc4A. CI không chạy Airflow scheduler hoặc Metabase.
- Direct dependencies/actions được pin, nhưng chưa có transitive Python lockfile
  hoặc digest pin cho PostgreSQL/Python reproduction images.
- Chưa deploy, auto-refresh dbt/synthetic, AI/RFM/retention/SCD2 hay tính năng mới.
  Dừng sau4B để review; các thay đổi4A/4B chưa commit hoặc push.
