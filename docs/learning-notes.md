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

## Chặng 2A — luồng và file mới

Collector lặp product → date_windows → raw → validate tất cả → một upsert transaction.
`sql/002_multi_asset_synthetic.sql` nâng cấp schema đã có bằng migration, không reset.
`pipeline/synthetic.py` đọc daily prices, tạo dataset registry, customers trước trades.
`pipeline/verify_stage2a.py` chỉ đọc để kiểm tra constraints, profiles, tiền và checksum.
Fixture CSV + expected_totals.json chỉ được unit tests đọc, chưa phải dbt models.

### Ba kiến thức cần hiểu

1. **Pagination theo thời gian và completeness theo từng asset.** Product tồn tại
   không đồng nghĩa có đủ lịch sử. Chia windows dưới trần API; dùng `[start,end)`
   để biên chung không bị nhân đôi, kiểm tra từng ngày trước khi commit toàn batch.
2. **Deterministic synthetic data và lineage.** Seed + version + fixed date range
   giữ values tái lập, UUID5 giữ entity IDs ổn định. Dataset registry đóng băng
   config và price-input hash. Source live cho reference price không biến synthetic
   execution thành trade thật; phải ghi rõ daily-close look-ahead assumption.
3. **Decimal, rounding per trade và reconciliation oracle.** Làm tròn price trước,
   rồi quantity×price, rồi fee theo notional đã round. SUM các row đã round có thể
   khác round một lần ở tổng. Fixture nhỏ phải có expected totals tính tay độc lập,
   không chỉ so hai hàm thực hiện cùng một công thức.

### Ba câu hỏi phỏng vấn

1. Nếu một window của SOL thiếu ngày trong khi BTC đã lấy xong thì pipeline làm gì?
   Giữ raw, báo product/window bị thiếu, không commit normalized của cả run; không
   tự điền giá. Có thể rerun sau khi điều tra, upsert không nhân đôi các key đã có.
2. Cùng seed có đủ để bảo đảm 50k trades giống nhau khi chạy lại không?
   Còn phụ thuộc fixed dates, input-price snapshot, generator version, traversal
   order, rounding và runtime. Registry/checksum phát hiện drift, IDs/FK bảo vệ grain.
3. Tại sao tổng phí không nên tính bằng SUM(notional)×fee_rate rồi round một lần?
   Fee thực hiện per trade; rounding error tích lũy làm khác. Đối chiếu từng row,
   rồi tổng theo customer/product/side/date với fixture độc lập để bắt lỗi join/rounding.

Không cộng quantity của BTC/ETH/SOL thành một tổng vì khác đơn vị. Notional USD và
fee USD có thể cộng; đó là tổng hoạt động giao dịch, không phải vốn, balance hay P&L.

## Chặng 2B — dbt analytics

Flow: existing synthetic/market sources → stg_customers/stg_trades/stg_asset_prices
→ dim_assets/dim_date + fact_trades → mart_daily_trading/mart_customer_summary.
Đọc analytics/models/schema.yml và docs/models.md để hiểu grain/columns trước,
sau đó xem fact joins, marts và analytics/tests. scripts/run_dbt.py là env wrapper;
scripts/verify_dbt.py chạy migrations/fixture models trên test server riêng.

### Ba kiến thức cần hiểu

1. **Grain và join cardinality.** Customer key có dataset; asset/price key có source,
   product và price còn có ngày. Join thiếu một thành phần có thể nhân đôi tiền mà
   SQL vẫn chạy thành công. LEFT JOIN giữ trade, tests bắt dimension thiếu; unique
   và reconciliation phát hiện fan-out, không dùng DISTINCT fact để che lỗi.
2. **Additive và non-additive metrics.** Volume/phí/count có thể SUM theo nhóm không
   chồng lấn; distinct customers không được cộng từ asset/side hoặc từ nhiều ngày.
   Fee phải SUM số đã round per trade. Customer zero-trade giữ totals zero nhưng
   recency NULL để phân biệt chưa giao dịch với vừa giao dịch hôm nay.
3. **As-of analytics và kiểm thử trên model thật.** Cutoff UTC cố định ngăn future
   trades/customer leakage, segment dựa dữ liệu tới cutoff. Fixture nhỏ với oracle
   tính tay phải chạy chính SQL models; thử dbt trên dữ liệu thật/synthetic và chạy
   Python tính fixture riêng không thay thế được việc đó. Migrations test trên DB
   mới, không reset DB đang dùng.

### Ba câu hỏi phỏng vấn

1. Tại sao count/volume tăng sau join dim_assets hoặc daily prices dù trade_id không đổi?
   Kiểm tra grain từng bảng, đủ source/product/date chưa, uniqueness và per-key
   cardinality; đối chiếu count/tiền trước-sau, không chữa bằng DISTINCT toàn fact.
2. Một khách BUY BTC và SELL ETH cùng ngày được tính active customers thế nào?
   Một khách tại daily grain; COUNT DISTINCT từ executions của toàn ngày. Toàn kỳ
   cũng DISTINCT trực tiếp từ fact, không SUM daily active customers.
3. Làm sao báo cáo customer tái lập và vẫn giữ khách chưa từng giao dịch?
   Chốt dataset/as_of_date UTC, lọc cả customer creation và trades, LEFT JOIN một
   aggregate theo dataset/customer; totals zero, dates/recency NULL. Chứng minh bằng
   fixture qua cùng dbt models với cutoffs sớm/muộn và expected totals độc lập.

## Chặng 3 — Metabase

Flow: analytics tables/views → metabase_reader SELECT → saved native SQL cards →
Metabase dashboard. Cấu hình/cards/layout lưu trong PostgreSQL metadata riêng và
volume riêng, không ở business DB. SQL ở metabase/cards; provisioning/grants ở
scripts/setup_metabase*.py; SQL độc lập ở sql/verify_3.sql và verifier API.

### Ba kiến thức cần hiểu

1. **Grain và calendar padding.** Daily mart hiện một dataset/ngày, không có asset/side.
   Breakdown phải đọc fact. 116 ngày trade là union các lịch synthetic, không phải
   thiếu giá. LEFT JOIN dim_date lên trades đã filter để thể hiện 64 ngày zero-trade;
   đặt asset filter sai trong WHERE sau LEFT JOIN sẽ làm biến mất zero days.
2. **Filter scope và distinct customers.** Volume/fees/count cộng được, distinct không
   cộng qua ngày/asset/side. June+ETH tính trực tiếp từ filtered fact ra 300 customers.
   Customer summary là snapshot cutoff, không được thay nhãn/tổng theo filters vốn
   chỉ áp cho trading cards. Labels và parameter mappings là một phần metric contract.
3. **App state và least privilege.** Metabase administrator không đồng nghĩa query
   bằng database owner. Reader phải bị ACL từ chối ghi ngay cả khi bỏ read-only default.
   Questions/dashboard/filter definitions ở metadata DB bền vững; restart app phải
   giữ chúng. Metadata volume và encryption key cần giữ cùng nhau ngoài Git.

### Ba câu hỏi phỏng vấn

1. Vì sao có 180 ngày giá nhưng daily mart chỉ có 116 rows, và chart làm sao hiện đủ?
   Mart chỉ có ngày trade; generator đồng bộ lịch tạo 116 ngày. Calendar dimension
   LEFT JOIN fact đã lọc, COALESCE totals=0; không chèn thêm dữ liệu nguồn để làm đẹp.
2. Vì sao chọn ETH/tháng 6 không làm customer segments giảm còn 300 người?
   Trading filters chọn scope fact, nhưng segment chart là snapshot 1000 khách tại
   cutoff 2026-10-27; không có filter mapping. Muốn segmentation theo cohort khác
   cần model/contract khác, không ngầm tái diễn giải snapshot hiện tại.
3. Làm sao chứng minh dashboard không dùng owner và không mất khi restart?
   Kiểm tra connection user, SELECT và thử UPDATE WHERE false với read_only=off để
   thấy ACL deny; lưu IDs/SQL/filters/settings trước-sau restart, đối soát API cards
   với SQL độc lập và kiểm tra metadata dùng PostgreSQL named volume riêng.
