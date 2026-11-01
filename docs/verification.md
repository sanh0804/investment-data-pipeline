# Verification — chặng 1 và 2A

Trạng thái hiện tại: chặng 1 có commit `4153807`; chặng 2A đã được kiểm chứng ở
phần cuối tài liệu. Phần chặng 1 giữ bằng chứng lịch sử trước commit; những dòng
“chưa commit/chưa ETH-SOL” trong phần đó không mô tả trạng thái sau 2A.

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
