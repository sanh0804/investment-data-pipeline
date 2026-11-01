# Fixture riêng cho reconciliation ở chặng 2B

Dữ liệu này hoàn toàn giả, không phải giao dịch/giá live. Chỉ đọc từ test, không
có loader vào PostgreSQL. `source=fixture` cố ý không thỏa constraint source của
bảng market/synthetic production. Không thay thế hoặc điền thiếu dữ liệu API.

4 customers, 6 matched trades, 6 price references trong 3 ngày. Customer có đuôi
003 chưa giao dịch; 002 dừng từ sau 02/05; 001 và 004 có giao dịch ngày cuối mẫu.
Profile chỉ mô tả kịch bản fixture; bộ mẫu ba ngày không dùng quy tắc ceased 60
ngày của generator lớn. Không tính balances, holdings hay P&L.

Quy tắc: execution_price = ROUND_HALF_UP(reference_close × (1 + slippage_bps/10000), 2);
notional = ROUND_HALF_UP(quantity × execution_price, 2);
fee = ROUND_HALF_UP(notional × 0.001, 2). Tất cả bằng Decimal.

| Trade suffix | Phép tính notional | Notional USD | Fee USD |
| --- | --- | ---: | ---: |
| 001 | 2 × 100 | 200.00 | 0.20 |
| 002 | 3 × 20 | 60.00 | 0.06 |
| 003 | 10 × 10 | 100.00 | 0.10 |
| 004 | 0.5 × 110 | 55.00 | **0.06** (0.055 làm tròn lên) |
| 005 | 1.234567 × 25 = 30.864175 | 30.86 | 0.03 |
| 006 | price = 12 × 1.0025 = 12.03; 2.5 × 12.03 = 30.075 | **30.08** | 0.03 |

Tổng notional: 200 + 60 + 100 + 55 + 30.86 + 30.08 = **475.94 USD**.
Tổng phí theo từng trade: 0.20 + 0.06 + 0.10 + 0.06 + 0.03 + 0.03 = **0.48 USD**.
Không tính phí bằng cách làm tròn một lần ở cấp tổng: 475.94 × 0.001 → 0.48 chỉ
tình cờ bằng nhau ở bộ này; quy tắc vẫn là round per trade rồi SUM.

`expected_totals.json` viết độc lập các tổng theo customer, product, side, date;
test đọc CSV, kiểm tra arithmetic/keys/timestamps và so sánh với các số này.
Đơn vị quantity là base asset; không cộng quantity BTC/ETH/SOL thành một tổng.
Chưa dựng dbt/reconciliation models: đây chỉ là hợp đồng dữ liệu đầu vào/expected.
