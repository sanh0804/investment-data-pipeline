# Quyết định theo chặng

Phần chặng 1 bên dưới là lịch sử; quyết định chặng 2A ở cuối tài liệu thay thế
các giới hạn cũ về BTC-only, window đơn và tên volume.

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

## 2026-11-01 — Chặng 2A: multi-asset và synthetic

Chặng 1 đã có commit `4153807` trước khi sửa chặng 2A; working tree/staged trống.
Đã đọc code/docs và xác nhận tracked files không chứa .env, venv, data local hay
mật khẩu local. Không tạo commit rỗng và không push. Cập nhật AGENTS theo scope mới.

### Coinbase và lịch sử theo product

Giữ provider. Probe `/products/BTC-USD`, `/products/ETH-USD`, `/products/SOL-USD`
đều HTTP 200, base đúng BTC/ETH/SOL, quote USD, status online, trading_disabled=false.
Probe daily candles vượt 300 ngày thực tế trả HTTP 400 với thông báo count of
aggregations exceeds 300. Không thử tải sát quota theo IP hoặc cố tình tạo 429.
Tài liệu candles và rate limit dẫn ở phần chặng 1 vẫn là nguồn tham chiếu.

Chọn range cố định `[2026-05-01,2026-10-28)` = 180 completed UTC days. Chia 3
windows 60 ngày: May 1→Jun 30, Jun 30→Aug 29, Aug 29→Oct 28, cho mỗi product.
Trần window 299 ngày chừa một inclusive-end candle; total range không còn bị
giới hạn bởi một request. Request tuần tự có pacing 1s mặc định.
Lọc `[start,end)` trên từng response, check completeness từng product/window.
Chỉ commit normalized toàn run khi mọi window đạt; raw attempts commit riêng.
Không fill missing hoặc suy đoán lịch sử product. Coverage chỉ được xác nhận cho
range thực tế đã lấy, không tuyên bố lịch sử đầy đủ trước đó.

### Migrations và preservation

Giữ nguyên `001_schema.sql` đã commit. Migration `002` mở product CHECK, rename
volume_btc→volume_base, giữ values/keys/FK/raw của chặng 1 và thêm schema synthetic.
`init_db` ghi migration filenames, dùng transaction/advisory lock và skip migration
đã chạy. Không drop bảng, xóa row hay reset volume.

### Synthetic contracts

Không sinh CSV 50k rồi gọi là dữ liệu thị trường. Load vào `synthetic.datasets`,
`synthetic.customers`, `synthetic.trades`; origin synthetic, status MATCHED.
Grain customer là một người giả, PK UUID5. Grain trade là một matched execution,
PK UUID5. Thêm unique (dataset_id,entity_number) và FK dataset/customer/price/raw.

Generator version `synthetic-v1`, dataset `portfolio-v1`, seed `20261101`, cùng
range 180 ngày. IDs dùng namespace UUID5 từ version/dataset_id và entity number;
seed không nằm trong ID để giữ logical ID khi rerun, nhưng dataset registry từ
chối seed/config/price inputs khác dưới cùng dataset_id.
RNG Python random.Random, không dùng cho bảo mật. Reproducibility đã kiểm chứng
trên Python 3.13.5; chưa xác minh mọi phiên bản Python tương lai.

Created_at của tất cả khách trước start 1..30 ngày. 100 never, 600 regular,
300 ceased. 50k trades chia vòng qua 900 khách; mỗi khách 55/56 executions.
Regular trải đều toàn range, ceased chỉ trong 120 ngày đầu và im lặng 60 ngày cuối.
BUY/SELL luân phiên theo trade ordinal của khách. Product chia vòng BTC/ETH/SOL;
vì 900 chia hết cho 3, ở version này mỗi khách hoạt động gắn với một product.
Đây là kịch bản để phân tích, chưa mô phỏng danh mục đa tài sản cho từng khách.

Quantity 6 decimal places, random integer 1000..1000000 / 1000000. Execution
dựa daily close × (1+slippage bps/10000), slippage integer [-25,25], round 2 decimals
HALF_UP. Notional = quantity × execution rồi round 2; fee = notional × 0.001
rồi round 2 mỗi execution. SQL constraints dùng round NUMERIC tương ứng cho số dương.

Intraday timestamps với close cùng ngày là look-ahead: chỉ để tạo synthetic,
không dùng cho backtesting hoặc mô tả lệnh thị trường thật. Không có funds/holdings,
bán không cần sở hữu asset; không tính balance hoặc P&L.

Giữ reference_close và reference_fetch_id của lần sinh; lưu checksum business
prices, không gồm fetch metadata. Giá live thay đổi thì reject rerun cùng dataset,
không tự reprice lịch sử synthetic. Input price không đổi nhưng fetch ID mới thì
cho rerun, giữ raw lineage ban đầu. Synthetic INSERT ON CONFLICT DO NOTHING,
đối chiếu full business checksum/counts trước commit để phát hiện dữ liệu bị sửa.

### Fixture nhỏ và scope tiếp theo

Fixture tách ở tests/fixtures/reconciliation: source=fixture, 4 customers/6 trades,
giá giả rõ ràng, expected totals viết độc lập. Không có command nạp fixture vào live;
source CHECK cũng ngăn ghi fixture trực tiếp. Mẫu gồm zero-trade customer, cả BUY/SELL,
fee half-cent và notional half-cent để chặng 2B có oracle kiểm thử.
Chưa thêm dbt, Airflow, Metabase, CI, analytics models, balances, holdings hoặc P&L.
