# Verification — chặng 1

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
