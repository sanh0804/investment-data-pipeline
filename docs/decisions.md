# Quyết định chặng 1

## 2026-11-01 — Phạm vi

Triển khai Python + PostgreSQL + Docker Compose cho daily BTC-USD. Chưa dựng
orchestration, BI hay business datasets. Quy tắc bền vững ở `AGENTS.md`, hướng dẫn
sản phẩm/vận hành ở README. Không tạo framework agent riêng.

## Provider và kiểm chứng API trước khi normalize

Chọn Coinbase Exchange endpoint public:
`GET https://api.exchange.coinbase.com/products/BTC-USD/candles`.
Request thực tế không kèm API key đã trả HTTP 200 từ máy này ngày 01/11/2026.
Đây là kiểm chứng quyền truy cập tại thời điểm chạy, không bảo đảm truy cập mọi
quốc gia/mạng hoặc mọi thời điểm. Không xác minh bằng cách cố tình flood API.

Tài liệu provider: [Get product candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles),
[REST rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits).
Giới hạn tài liệu: 300 candles/request, public 10 requests/second/IP, burst 15;
429 khi vượt giới hạn. Không poll liên tục, chỉ gọi từng lần theo khoảng ngày.
Không có API key/paid plan được dùng trong chặng này; chưa kiểm chứng quota thực
nghiệm hay các hạn chế ngoài tài liệu.

## Timestamp, ngày và giá

Probe thực tế cho khoảng 2026-10-25 đến 2026-10-28 nhận 4 arrays gồm 6 trường:
`[time, low, high, open, close, volume]`, thứ tự ngày giảm dần.
Timestamp `1792886400` chuyển ra `2026-10-25T00:00:00+00:00`, không phải milliseconds.
Các timestamp cách nhau 86400 giây, chia hết cho 86400.
Response có nến ở mốc end; không suy luận rằng API dùng end-exclusive.
Tài liệu định nghĩa time là bucket start, granularity 86400 là một ngày;
tài liệu cũng cảnh báo có thể thiếu dữ liệu hoặc có bucket trước start.
Từ bằng chứng này chọn daily close cho ngày UTC, lọc lại `[start, end)` tại client
và chỉ nhận ngày đã hoàn tất. Timestamp/shape thay đổi sẽ bị từ chối, không đoán.

BTC-USD là sản phẩm tại Coinbase, quote USD, volume theo BTC. Chưa mô hình hóa
global reference price hay multi-provider reconciliation. Tối đa 299 ngày để chừa
một nến ở end, chưa chia request tự động.

## Storage và giao dịch

Raw grain: một HTTP attempt, key `fetch_id`, thêm unique `(run_id, attempt_number)`.
Lưu bytes HTTP body đã giải nén dưới dạng BYTEA và checksum SHA-256; giữ numeric
lexemes, không tái serialize qua float. Headers lưu allowlist, bỏ cookie.
Raw commit trước validation; normalized batch commit nguyên khối sau khi tất cả
checks qua. Không có normalized nửa chừng nếu thiếu một ngày. Raw không chứng minh
run đã normalize thành công; cần đối chiếu output và `last_fetch_id`.

Normalized grain/key: `(source, product_id, price_date)`.
UPSERT cập nhật OHLCV và last fetch lineage, không thêm duplicate key.
NUMERIC không fixed scale để tránh làm tròn precision source. Không phải SCD2.
Rerun hợp lệ có thể cập nhật dữ liệu provider đã sửa. Chạy tuần tự;
nếu tương lai chạy đồng thời cần quy tắc freshness/locking rõ hơn.

## Local development

Máy thực tế macOS arm64, Docker Linux aarch64. Dùng image multi-architecture
`postgres:16-bookworm`, không ép `platform: linux/amd64`.
PostgreSQL khác đã có tại 5432; project bind loopback 55432 và volume riêng.
Schema init dùng lệnh có thể chạy lại, không dùng volume reset. Local app dùng
database owner cho đơn giản, chưa thiết kế production least-privilege roles.

Retry có budget attempts/timeouts và chờ hữu hạn; không retry permanent errors.
Nếu Retry-After dài hơn budget thì dừng. Không có disk spool khi database down,
không có scheduler tự chạy, chưa có run-status table hoặc alerting.

Các quyết định trên phục vụ learning scope chặng 1; chưa tuyên bố production-ready.
