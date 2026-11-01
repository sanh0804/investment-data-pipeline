# Verification — chặng 1, 2A, 2B và 3

Trạng thái hiện tại: chặng 1 có commit `4153807`, 2A có `933071b`, 2B có `5993a26`.
Kết quả chặng 3 ở cuối tài liệu. Các phần trước giữ bằng chứng lịch sử tại thời điểm
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
