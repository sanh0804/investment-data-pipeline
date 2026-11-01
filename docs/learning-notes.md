# Learning notes — chặng 1

## Luồng và các file nên đọc

API public → `collector.fetch()` lưu từng HTTP attempt vào `raw.price_fetches`
→ `normalize()` đọc JSON và kiểm tra dữ liệu → `db.upsert()` ghi
`market.daily_prices`. Bắt đầu đọc `sql/001_schema.sql`, sau đó
`pipeline/normalize.py`, `pipeline/collector.py` và `pipeline/verify_idempotency.py`.
`compose.yaml` cấp PostgreSQL persistent; `.env` cung cấp cấu hình local.

## Ba kiến thức cần hiểu

1. **Grain, unique key và idempotency.** Grain mô tả một dòng đại diện điều gì.
   Primary key ngăn duplicate; upsert quy định cập nhật khi gặp lại key. Hai lần
   xử lý cùng raw body phải cho cùng OHLCV và số dòng. Raw tăng theo attempt là
   có chủ đích; metadata load có thể khác giữa các lần xử lý.
2. **Event time, ingestion time và UTC.** Timestamp source ở đây là đầu bucket,
   không phải thời điểm collector lấy dữ liệu. Daily close thuộc ngày UTC của
   bucket đó, không đổi sang ngày Việt Nam bằng cách cộng 7 giờ. Chỉ lấy ngày đã
   hoàn tất và tự lọc `[start, end)` để tránh biên thời gian của API.
3. **Raw lineage, transaction và data quality.** Lưu response trước giúp điều tra,
   replay và phân biệt lỗi API với lỗi biến đổi. Decimal/NUMERIC giữ độ chính xác;
   checks phát hiện thiếu ngày/giá lỗi. Raw vẫn tồn tại khi validation thất bại,
   còn normalized batch phải rollback toàn bộ nếu load lỗi.

## Ba câu hỏi phỏng vấn

1. **Làm sao pipeline không tạo duplicate khi scheduler chạy lại?**
   Nêu grain và primary key, `ON CONFLICT`, batch transaction và bài kiểm tra replay
   hai lần cùng payload. Phân biệt duplicate HTTP fetch với duplicate business key.
2. **API trả timestamp và nến ngày thế nào, tại sao không dùng ngày local?**
   Dẫn chứng response thật, epoch seconds, UTC midnight, day close đã hoàn tất,
   inclusive/exclusive boundaries. Không tự diễn giải `time` thành thời điểm close.
3. **Nếu API trả 429, thiếu một ngày hoặc giá lịch sử bị sửa thì sao?**
   429 retry hữu hạn theo Retry-After; thiếu ngày giữ raw và fail normalized batch;
   dữ liệu sửa được upsert, raw giữ lineage. Thừa nhận chưa có scheduler/backfill
   tự động hay lịch sử phiên bản normalized.

## Ghi chú thực hành

- HTTP 200 chỉ xác nhận request thành công; payload còn phải qua kiểm tra dữ liệu.
- Không có giao dịch trong bucket có thể dẫn tới thiếu candle theo tài liệu API;
  không tự fill forward và gọi đó là dữ liệu gốc.
- Fixture/mocks trong `tests/` là giá tổng hợp để kiểm tra logic, không được đưa
  vào bảng live hay dùng làm bằng chứng lấy dữ liệu thật.
- Volume PostgreSQL và `.env` là trạng thái local cần giữ khi rerun.
- Xem `verification.md` để biết kết quả nào là thực nghiệm và giới hạn nào còn mở.
