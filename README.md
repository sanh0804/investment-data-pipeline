# Investment Data Pipeline & Analytics

Personal portfolio project cho vị trí Data Engineering / Data Analytics.
Chặng 1: lấy daily candle BTC-USD từ Coinbase Exchange, lưu raw và giá đóng cửa theo ngày UTC vào PostgreSQL.

Stack toàn project đã chốt: Python, PostgreSQL, dbt Core, Airflow, Metabase,
Docker Compose và GitHub Actions. Các chặng sau mới có ETH/SOL, 1.000 khách hàng
và 50.000 giao dịch đã khớp giả lập, SQL transformations, data quality,
reconciliation, customer summary và một dashboard. Airflow và CI thêm cuối cùng.

## Yêu cầu và setup trên macOS

Kiểm tra thực tế trước khi cài, không chọn architecture cố định cho Docker:

```bash
uname -m
python3 --version
docker --version
docker compose version
docker info --format '{{.OSType}} {{.Architecture}}'
git --version
```

Python đã kiểm chứng: 3.13.5; cần Python >= 3.11 và Docker daemon hoạt động.
Chạy các lệnh tại thư mục gốc repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp -n .env.example .env
chmod 600 .env
```

Sửa `POSTGRES_PASSWORD` trong `.env` thành mật khẩu local ngẫu nhiên trước khi
khởi tạo database. Có thể tạo bằng `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Nếu `.env` đã được tạo trong lần triển khai đầu thì giữ nguyên mật khẩu đang dùng.
Không commit file này. Không cần API key cho endpoint đã chọn.

```bash
docker compose up -d --wait
docker compose ps
.venv/bin/python -m pipeline.init_db
.venv/bin/python -m pipeline.collector
```

PostgreSQL chỉ bind `127.0.0.1:55432`, dùng volume `postgres_data` của Compose project này.
Host đã có database ở 5432; không sửa service đó. Nếu 55432 bận, đổi `POSTGRES_PORT`
trong `.env`. Schema được tạo bằng lệnh Python riêng, có thể chạy lại; không phụ
thuộc script chỉ chạy lần đầu của PostgreSQL image.

## Cấu hình và dữ liệu

`.env.example` minh họa `START_DATE=2026-10-25`, `END_DATE=2026-10-28`:
ba ngày 25, 26, 27/10 UTC. `start` bao gồm, `end` loại trừ. Nếu bỏ cả hai biến,
collector lấy 7 ngày UTC đã hoàn tất gần nhất. Chỉ đặt một biến sẽ báo lỗi.
Shell environment ưu tiên hơn `.env`.

```bash
START_DATE=2026-10-25 END_DATE=2026-10-28 .venv/bin/python -m pipeline.collector
```

Tối đa 299 ngày/request; vượt giới hạn sẽ báo lỗi, chưa có pagination/backfill dài.
`END_DATE` không được vượt ngày hiện tại UTC. Không thu thập nến ngày đang mở.
Source cố định `coinbase_exchange`, product `BTC-USD` trong chặng 1.
Giá là daily close bằng USD tại Coinbase, không phải giá trung bình toàn thị trường.

Các biến timeout/retry và kết nối nằm trong `.env.example`.
Mỗi request có connect timeout và read timeout riêng (mặc định 5s/20s).
Read timeout là thời gian chờ đọc socket, không phải deadline cho toàn bộ run.
Mặc định tối đa 3 attempts kể cả lần đầu, hard cap 5; exponential backoff.
Chỉ retry timeout, connection error và HTTP 408/429/500/502/503/504.
Không retry lỗi chứng chỉ TLS, 400/401/403/404 hoặc JSON/data validation.
`Retry-After` được đọc dưới dạng số giây hoặc HTTP date; nếu thời gian chờ vượt
`HTTP_MAX_RETRY_DELAY`, dừng để chạy lại sau, không gọi API sớm hơn yêu cầu.

Luồng dữ liệu:

```text
Coinbase public candles API (BTC-USD, 86400s)
  → raw.price_fetches (commit từng attempt, giữ body bytes + metadata)
  → parse JSON bằng Decimal, kiểm tra UTC/OHLC/completeness
  → market.daily_prices (một transaction upsert cho cả khoảng ngày)
```

`raw.price_fetches` giữ response body nguyên bytes đã được HTTP client giải nén,
SHA-256, thời điểm bắt đầu/kết thúc UTC, endpoint, params, HTTP status, run ID,
attempt number và một số response headers. Không lưu cookie/authorization.
Transport error không có HTTP response thì body/status là NULL.
JSON lỗi vẫn được lưu raw. Nếu database không kết nối/ghi được thì dừng; chưa có
fallback spool trên disk. `raw` là quy ước append-only của ứng dụng, chưa cưỡng chế
bằng role riêng; tài khoản database hiện chỉ phục vụ local development.

`market.daily_prices` có grain **một source × product × ngày UTC đã hoàn tất**.
Primary/unique key: `(source, product_id, price_date)`.
`bucket_start` phải bằng 00:00:00 UTC của `price_date`.
Giá dùng `Decimal` → PostgreSQL `NUMERIC`, không qua binary float.
`ON CONFLICT DO UPDATE` cập nhật OHLCV và lineage nếu fetch sau sửa dữ liệu lịch sử.
Chính sách hiện tại là lần xử lý được commit sau cùng thắng; chạy collector tuần tự.
Không giữ lịch sử phiên bản normalized; raw giữ được từng response.
`loaded_at`/`last_fetch_id` là metadata xử lý, có thể đổi khi chạy lại; idempotency
được định nghĩa theo key và business values, không phải toàn bộ metadata.

Collector không giả định API sắp xếp tăng dần hay tuân thủ end-exclusive.
Timestamp phải là integer epoch seconds tại nửa đêm UTC. Lọc `[start, end)`,
sắp xếp tăng dần, từ chối duplicate day, thiếu ngày, giá không hợp lệ hoặc OHLC
không nhất quán. Thiếu ngày khiến toàn bộ normalized batch không được ghi;
không nội suy, không tự thêm fixture. Raw attempt vẫn còn để điều tra.

## Kiểm tra kết quả và idempotency

```bash
.venv/bin/python -m pytest -q
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify.sql
```

SQL xem giá và kiểm tra duplicate:

```sql
SELECT price_date, bucket_start, close_price, last_fetch_id
FROM market.daily_prices ORDER BY price_date;

SELECT source, product_id, price_date, count(*)
FROM market.daily_prices
GROUP BY source, product_id, price_date HAVING count(*) > 1;
```

Collector in `fetch_id` cho mỗi attempt. Dùng ID của response HTTP 200 để phát lại
**đúng cùng body đã lưu** hai lần, trong hai transaction đã commit:

```bash
.venv/bin/python -m pipeline.verify_idempotency --fetch-id <UUID-cua-response-200>
```

Lệnh xác minh SHA-256, so sánh toàn bộ OHLCV theo key giữa hai lần, kiểm tra số
dòng bằng số ngày kỳ vọng và báo JSON. Script ghi lại normalized từ response đã
chọn, vì vậy chọn response mới nhất; đây là lệnh kiểm chứng thủ công, không phải
scheduler. Raw không được tạo thêm khi replay. Chạy collector live hai lần là
kiểm tra bổ sung; hai HTTP responses có thể khác nhau nên không thay thế replay.

Đọc raw JSON và params:

```sql
SELECT fetch_id, request_params, convert_from(response_body, 'UTF8') AS body
FROM raw.price_fetches WHERE http_status = 200 ORDER BY fetched_at DESC LIMIT 1;
```

Trạng thái đã chạy/chưa chạy, ID và kết quả thực tế: [docs/verification.md](docs/verification.md).
Unit tests dùng giá giả và HTTP mocks; không đại diện cho API live.

## File chính và vận hành

| File | Vai trò |
| --- | --- |
| `AGENTS.md` | Quy tắc cho agent và ranh giới chặng 1 |
| `compose.yaml` | PostgreSQL, healthcheck, cổng local và persistent volume |
| `pipeline/config.py` | Đọc env, kiểm tra khoảng ngày/giới hạn |
| `pipeline/collector.py` | Request live, retry, raw metadata và điều phối load |
| `pipeline/normalize.py` | Parse Decimal, UTC và quality checks |
| `pipeline/db.py`, `pipeline/init_db.py` | Kết nối, upsert và khởi tạo schema |
| `pipeline/verify_idempotency.py` | Replay cùng raw response hai lần |
| `sql/001_schema.sql`, `sql/verify.sql` | Grain/constraints và SQL kiểm chứng |
| `docs/` | Kiến thức, quyết định và bằng chứng kiểm chứng |

Xem lỗi bằng `docker compose logs --tail=100 postgres` và `docker compose ps`.
Dừng tạm bằng `docker compose stop`; khởi động lại bằng `docker compose up -d --wait`.
Không xóa volume/data để sửa lỗi. Đổi `POSTGRES_PASSWORD` trong `.env` không tự đổi
mật khẩu của database đã khởi tạo; cần thay đổi password trong PostgreSQL có chủ đích.
Image `postgres:16-bookworm` khóa major version, chưa khóa digest;
dependency Python khóa direct versions, chưa có transitive lockfile.

Chưa triển khai dbt, Airflow, Metabase, CI, ETH/SOL, simulated customers/trades,
AI agent, SCD2 hay retention. Dừng ở chặng 1 để review.
