# Quyết định thiết kế

Các quyết định dưới đây giải thích source contracts, cách lưu dữ liệu, mô hình
analytics và phạm vi vận hành local. Bằng chứng chạy thực tế nằm trong verification.md.

## Phạm vi

Python collector thu thập daily BTC/ETH/SOL vào PostgreSQL; dbt xây analytics và
Metabase hiển thị dashboard. Docker Compose phục vụ vận hành local. Orchestration
và CI là hướng mở rộng. Quy tắc làm việc ở `AGENTS.md`, hướng dẫn vận hành ở README.

## Provider và kiểm chứng API trước khi normalize

Coinbase Exchange cung cấp OHLCV theo sản phẩm qua endpoint public, đủ cho
bài toán giá ngày mà không cần quản lý API key:

`GET https://api.exchange.coinbase.com/products/BTC-USD/candles`.
Request thực tế không kèm API key đã trả HTTP 200 trong lần kiểm chứng.
Đây là kiểm chứng quyền truy cập tại thời điểm chạy, không bảo đảm truy cập mọi
quốc gia/mạng hoặc mọi thời điểm. Không xác minh bằng cách cố tình flood API.

Tài liệu provider: [Get product candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles),
[REST rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits).
Giới hạn tài liệu: 300 candles/request, public 10 requests/second/IP, burst 15;
429 khi vượt giới hạn. Không poll liên tục, chỉ gọi từng lần theo khoảng ngày.
Không có API key/paid plan được dùng; chưa kiểm chứng quota thực
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
một nến ở end; collector chia khoảng dài thành các windows.

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

Thiết kế phục vụ portfolio local, chưa tuyên bố production-ready.

## Multi-asset và synthetic

### Coinbase và lịch sử theo product

Giữ provider. Probe `/products/BTC-USD`, `/products/ETH-USD`, `/products/SOL-USD`
đều HTTP 200, base đúng BTC/ETH/SOL, quote USD, status online, trading_disabled=false.
Probe daily candles vượt 300 ngày thực tế trả HTTP 400 với thông báo count of
aggregations exceeds 300. Không thử tải sát quota theo IP hoặc cố tình tạo 429.
Tài liệu candles và rate limit ở trên là nguồn tham chiếu.

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
volume_btc→volume_base, giữ values/keys/FK/raw đã có và thêm schema synthetic.
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

### Reconciliation fixture

Fixture tách ở tests/fixtures/reconciliation: source=fixture, 4 customers/6 trades,
giá giả rõ ràng, expected totals viết độc lập. Không có command nạp fixture vào live;
source CHECK cũng ngăn ghi fixture trực tiếp. Mẫu gồm zero-trade customer, cả BUY/SELL,
fee half-cent và notional half-cent làm oracle kiểm thử dbt/reconciliation.

## dbt analytics

Giữ nguyên migrations đã áp dụng và dữ liệu nguồn. Đọc schema thực tế trước khi
quyết định joins; dbt chỉ ghi vào analytics schema riêng.

### Runtime và credentials

Chọn dbt Core/dbt-postgres 1.11.0, pin trực tiếp ở requirements-dbt.txt. Package
metadata yêu cầu Python >=3.10, adapter công bố classifier Python 3.13; đã cài wheel
psycopg2-binary 2.9.13 cho Python 3.13.5 macOS arm64 và build thực tế thành công.
Nguồn tham khảo: [dbt-postgres 1.11.0](https://pypi.org/project/dbt-postgres/1.11.0/),
[Postgres setup](https://docs.getdbt.com/docs/local/connect-data-platform/postgres-setup),
[env_var](https://docs.getdbt.com/reference/dbt-jinja-functions/env_var).

Dùng .venv-dbt tách collector .venv. Profile chỉ đọc env; wrapper dùng dotenv từ
collector env rồi truyền password qua DBT_ENV_SECRET_PASSWORD để dbt redact secrets.
Không có password trong YAML hoặc command arguments. Chưa khóa transitive dependencies;
version thực tế đã ghi trong verification. Không thêm dbt packages ngoài.

### Scope, models và metrics

Cấu hình cố định dataset_id=portfolio-v1, as_of_date=2026-10-27, price_source=coinbase_exchange.
Cutoff là trước nửa đêm UTC của ngày kế tiếp, không dùng CURRENT_DATE. Source schemas
synthetic/market, output analytics; macro luôn dùng target schema analytics/analytics_*,
kể cả dbt internal audit-schema request. Không để custom schema config redirect source.
Guard compile với DBT_SCHEMA=market đã được thử và bị từ chối.

Staging là views; dimensions/fact/marts là tables. Không incremental/SCD2/snapshot history.
Một schema biểu diễn một dataset/cutoff build; dùng schema khác để giữ cutoff khác.
Staging views phản ánh source hiện tại, cần build lại tables khi source đổi để các tầng
nhất quán. Chưa có scheduler tự refresh hoặc DB role riêng chỉ có SELECT trên source.

8 models, grain/columns/lineage mô tả ở docs/models.md và models/schema.yml.
Fact giữ một dataset/trade, LEFT JOIN customer theo dataset+customer, asset theo
source+product, price theo source+product+day, date theo UTC trade_date. Không DISTINCT
fact để che join fan-out. Surrogate MD5 từ JSONB arrays phục vụ tests/analytics;
SQL joins vẫn dùng full natural keys. Price context không ghi đè snapshot reference_close
hay execution_price. Không copy synthetic.activity_profile vào staging/customer segment.

Trade volume là SUM(notional_usd), không gọi revenue. Fees là SUM fee_usd đã round
per trade, không áp fee rate lên aggregate. Daily mart có một dataset/ngày có trade,
không thêm asset/side subtotals; active customers DISTINCT trực tiếp ở grain ngày,
không cộng distinct của slices hoặc ngày. Không fill zero-trade days trong mart này.

Customer summary LEFT JOIN aggregate theo dataset/customer, giữ mọi customer đủ
điều kiện created_at tại cutoff. Zero-trade có totals=0 và dates/recency=NULL.
Demo behavior rules: active <=30 ngày, at_risk 31..59, inactive >=60, never_traded
nếu chưa có trade tới cutoff. Không RFM/churn inference; lifetime chỉ trong dataset.

### Isolated verification

compose.test.yaml tạo server PostgreSQL riêng trên 55433, tmpfs, không mount volume
hiện tại. Script tạo verify_2b_<UUID> mới mỗi lần, chạy migrations hai lần, rồi load
fixture vào fixture_input (LIKE source types/defaults, không copy production constraints).
Market/synthetic chuẩn của test DB vẫn rỗng. Không drop DB hoặc xóa data sau test;
server/DB test được giữ để review và tmpfs không dùng làm persistence.

Fixture gồm 4 customers/6 trades, expected_totals.json giữ nguyên. Chạy chính
models bằng source-schema variables trên test DB, không có SQL models riêng cho test.
Thêm adversarial rows của dataset khác dùng cùng UUID, provider khác cùng pair/day
và future customer/trade. Baseline phải vẫn khớp JSON theo global/customer/product/side/date.
Bốn cutoffs kiểm tra dates/recency/segments và chống future-data leakage; daily distinct
[1,1,2] kiểm tra trường hợp một khách giao dịch nhiều assets trong ngày.

88 data tests cùng Python assertions trên output dbt bổ sung cho nhau; không dùng
Python fixture-only tests thay dbt. Artifacts mỗi test case ở target path riêng để
không đè manifest/run_results của demo. So checksum các demo source tables trước/sau.
Không thêm incremental models hoặc historical snapshots trong analytics hiện có.

## Metabase dashboard

Daily mart lưu ngày có giao dịch ở grain dataset/day. Dashboard cần cả ngày
không giao dịch nên bổ sung calendar bằng dim_date khi truy vấn; mart vẫn giữ
116 rows. Breakdown asset/side đọc fact vì hai chiều này không có trong mart.

Chọn official image metabase/metabase:v0.64.1, Docker manifest có arm64, native
arm64 đã chạy. Metadata tách sang service metabase_db và named volume riêng;
không publish metadata port, không dùng H2. Giữ mọi service/port/volume cũ.
Metabase bind localhost:3000. Password/encryption key sinh local vào ignored .env,
helper chỉ fill missing/placeholders, không rotate existing secrets.

Metabase dùng metabase_reader cho data source, SELECT analytics, không có table
writes hoặc CREATE. Default privileges của owner giữ SELECT sau dbt rebuild.
Metadata app user metabase_app chỉ thuộc server metadata; admin UI khác query user.

Native SQL cards versioned trong repo, script API upsert theo collection/card/dashboard
names để tái tạo không cần thao tác UI thủ công. API không versioned; code đã đối chiếu
OpenAPI chạy trên chính container. Tài liệu nguồn:
[Docker setup](https://www.metabase.com/docs/latest/installation-and-operation/running-metabase-on-docker),
[API setup](https://www.metabase.com/articles/set-up-metabase-from-code-with-the-api).

7 trading cards map From/To UTC/Asset, 2 snapshot cards không map. Trade volume ≠
revenue; active customers DISTINCT trực tiếp. Customer snapshot cutoff cố định,
không giả đổi segment theo trading filters. Banner/title ghi synthetic và Coinbase
reference prices. Không dùng AI, public sharing, SMTP hoặc scheduled subscriptions.

SQL và API checks đối chiếu dữ liệu cards; kiểm tra UI bổ sung việc chọn filters
và đọc kết quả hiển thị. Ảnh lưu viewport thật trong in-app browser. Phạm vi kiểm
chứng layout không bao gồm desktop Chrome/Safari; chi tiết ở verification.md.

## Airflow orchestration

LocalExecutor phù hợp workload tuần tự và tránh thêm Redis/Celery. Dùng Compose
overlay để base services không phụ thuộc cấu hình Airflow. API, scheduler,
DAG processor và PostgreSQL metadata riêng chạy bằng image arm64; pin Airflow
3.3.2/Python 3.12 và lưu upstream constraints trong repo. Dependencies của
collector trên host giữ nguyên; trong image dùng versions khớp constraints.

DAG dùng CronDataIntervalTimetable explicit UTC, không dựa mặc định thay đổi theo
version. Scheduled/backfill xử lý đúng một ngày; manual phải truyền start/end và
chỉ cho phép tối đa bảy ngày. Collector nhận dates bằng arguments, không sửa
process environment hoặc lấy ngày từ .env. Ngày thu thập là data interval,
không phải ngày task thực thi. Catchup tắt, DAG tạo ở trạng thái paused.

Giữ cả ba product trong một task để bảo toàn transaction boundary. Bước verify
đọc raw bodies theo fetch IDs trả về sau commit, kiểm tra checksum và đối chiếu
đủ values/keys/lineage. Không truyền raw vào XCom; không thêm status table nghiệp vụ.
Một slot pool và run limits phục vụ vận hành tuần tự; không coi đó là distributed
lock cho CLI hoặc các nguồn ghi khác. Không chạy backfill chồng manual reprocessing.

Backfill lấp run/day thiếu; reprocessing chủ động chạy lại ngày đã có và có thể
nhận price corrections. Retry HTTP và retry task có budget riêng; permanent
errors fail ngay, missing candle/transient failures được retry hữu hạn. Demo
portfolio-v1 và dbt/customer snapshot giữ nguyên, không tự sinh trades hay build
analytics khi market cập nhật.

SimpleAuthManager chỉ dành local development; tạo trước file auth trên volume
để tránh in generated passwords. Init chạy root chỉ cho ownership của thư mục
mới, còn runtime chạy airflow user. Scoped collector role chỉ ghi raw/market.
Các DAG fault-injection nằm trong verification image riêng, HTTP mocks và
PostgreSQL tmpfs riêng; không gắn test flag vào production DAG.

Nguồn: [Airflow Docker](https://airflow.apache.org/docs/apache-airflow/3.3.2/howto/docker-compose/),
[timetables](https://airflow.apache.org/docs/apache-airflow/3.3.2/authoring-and-scheduling/timetable.html),
[SimpleAuthManager](https://airflow.apache.org/docs/apache-airflow/3.3.2/core-concepts/auth-manager/simple/index.html).

## CI — tests và fixture reconciliation

Workflow tách unit matrix Python3.12/3.13, dbt fixture PostgreSQL16, và Airflow DAG
parse. Không dựng toàn bộ Airflow/Metabase trong CI: orchestration runtime đã có
bằng chứng local, còn CI cần phát hiện lỗi code/model và DAG import nhanh, không
phụ thuộc API public hay dataset live.

Tạo entry point verify_ci riêng để không đưa Compose/demo snapshot dependencies
của verify_dbt vào hosted runner. Chia sẻ loader và các assertions hiện có; không
fork SQL models hoặc expected totals. Migration runner tách apply_migrations(conn)
để dùng cùng logic trên connection test tường minh. Wrapper dbt có chế độ không
load .env; CI truyền đủ credentials qua env của database riêng.

Dùng PostgreSQL service user ci_runner/database investment_ci và tạo ci_verify_UUID
mới; password public chỉ dùng cho service tạm, không phải secret nghiệp vụ.
Không dùng secret trên Mac hoặc GitHub repository secrets. Reproduction Compose
có project/network/tmpfs riêng, port55434; runner chỉ mount artifact volume riêng.
Docker context allowlist không chứa .env, .git, host virtualenv hoặc dbt targets.

Mỗi cutoff chạy cùng 8 models/88 tests rồi so SQL output với oracle JSON và fixture
rows. Compile schema market phải fail với thông báo guard, không chỉ do connection
lỗi. Airflow check dùng DagBag API của pinned3.3.2 và network=none; không tuyên bố
parse check kiểm chứng scheduler, retry hay live HTTP.

Actions pin full commit SHAs, checkout không lưu Git credentials, token contents:read,
PR dùng pull_request thay vì pull_request_target. Artifacts whitelist và timeout/
concurrency limits giúp xem lỗi mà không upload workspace/local secrets. Chưa bật
branch protection hoặc publish workflow chỉ để có badge xanh.

Tham khảo: [PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers),
[Python workflows](https://docs.github.com/en/actions/tutorials/build-and-test-code/python),
[dbt secret env vars](https://docs.getdbt.com/reference/dbt-jinja-functions/env_var).
