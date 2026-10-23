# Phase 0 Feasibility Report
Generated: 2026-10-23 14uman:17 UTC

## Risk Constants Used
- `MAX_MARGIN_USD` = $2.0
- `MAX_LOSS_USD` = $1.0
- `SLIPPAGE_PCT` = 0.05%
- `MAX_LEVERAGE` = 3x
- `LIQ_BUFFER_MULTIPLE` = 2.0
- `MAINT_MARGIN_PCT` = 0.5%
- `MAX_NOTIONAL_USD` = $6.00 (= margin × leverage)

## Summary Table
| Exchange | Symbol | Type | Price | MinNotional | MinQty | LotSize | MaxNot≤$6? | ReqLev | TakerFee% | MaxStop% | TypStop%(ATR) | LiqDist% | Tradable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Binance | BTC | USDT-PERP | $81,538.9000 | $81.5389 | 0.001 | 0.001 | NO | 40.8x | 0.040% | 1.096% | 0.282% | 1.95% | NO |
| Binance | ETH | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='fapi.binance.c |
| Binance | SOL | USDT-PERP | $104.2100 | $5.0000 | 0.01 | 0.01 | YES | 2.5x | 0.040% | 19.870% | 0.407% | 39.50% | **YES** |
| Binance | XRP | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='fapi.binance.c |
| Binance | DOGE | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='fapi.binance.c |
| Bybit | BTC | USDT-PERP | $81,225.1000 | $81.2251 | 0.001 | 0.001 | NO | 40.6x | 0.055% | 1.071% | 0.313% | 1.96% | NO |
| Bybit | ETH | USDT-PERP | $2,386.6800 | $23.8668 | 0.01 | 0.01 | NO | 11.9x | 0.055% | 4.030% | 0.412% | 7.88% | NO |
| Bybit | SOL | USDT-PERP | $103.8100 | $10.3810 | 0.1 | 0.1 | NO | 5.2x | 0.055% | 9.473% | 0.434% | 18.77% | NO |
| Bybit | XRP | USDT-PERP | $1.3062 | $0.1306 | 0.1 | 0.1 | YES | 0.1x | 0.055% | 765.420% | 0.406% | 99.50% | **YES** |
| Bybit | DOGE | USDT-PERP | $0.0792 | $0.0792 | 1.0 | 1.0 | YES | 0.0x | 0.055% | 1261.829% | 0.492% | 99.50% | **YES** |
| OKX | BTC | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='www.okx.com',  |
| OKX | ETH | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='www.okx.com',  |
| OKX | SOL | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='www.okx.com',  |
| OKX | XRP | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='www.okx.com',  |
| OKX | DOGE | USDT-PERP | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: HTTPSConnectionPool(host='www.okx.com',  |
| MEXC | BTC | USDT-PERP | $81,195.8000 | $81195.8000 | 1.0 | 1.0 | NO | 40597.9x | 0.010% | 0.000% | 0.382% | -0.50% | NO |
| MEXC | ETH | USDT-PERP | $2,385.2100 | $2385.2100 | 1.0 | 1.0 | NO | 1192.6x | 0.010% | -0.028% | 0.536% | -0.42% | NO |
| MEXC | SOL | USDT-PERP | $103.6800 | $103.6800 | 1.0 | 1.0 | NO | 51.8x | 0.010% | 0.895% | 0.539% | 1.43% | NO |
| MEXC | XRP | USDT-PERP | $1.3052 | $1.3052 | 1.0 | 1.0 | YES | 0.7x | 0.010% | 76.547% | 0.514% | 99.50% | **YES** |
| MEXC | DOGE | USDT-PERP | $0.0792 | $7.9230 | 1.0 | 1.0 | NO | 4.0x | 0.010% | 12.551% | 0.550% | 24.74% | NO |

## Per-Row Math

### Binance — BTC (USDT-PERP)
- Price: $81,538.900000
- min_qty: 0.001, lot_size: 0.001, tick_size: 0.1
- Exchange min_notional: $50.0000
- **min_notional_usd** = max($50.0000, 0.001 × $81538.9000) = **$81.5389**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $81.5389 / $2.0 = **40.77x** (cap: 3x)
- round_trip_fee_usd = $81.5389 × 2 × 0.040% = **$0.06523**
- net_loss_budget = $1.0 - $0.06523 = **$0.93477**
- **max_stop_pct** = ($0.93477 / $81.5389 - 0.00050) × 100 = **1.0964%**
- ATR(15m): 230.178571
- **typical_stop_pct** = 230.178571 / 81538.900000 × 100 = **0.2823%**
- **liq_distance_pct** = (1/40.77 - 0.0050) × 100 = **1.9528%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.2823% vs 1.0964%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (1.9528% vs 0.5646%)
- **TRADABLE: NO**

### Binance — ETH (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti

### Binance — SOL (USDT-PERP)
- Price: $104.210000
- min_qty: 0.01, lot_size: 0.01, tick_size: 0.01
- Exchange min_notional: $5.0000
- **min_notional_usd** = max($5.0000, 0.01 × $104.2100) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- round_trip_fee_usd = $5.0000 × 2 × 0.040% = **$0.00400**
- net_loss_budget = $1.0 - $0.00400 = **$0.99600**
- **max_stop_pct** = ($0.99600 / $5.0000 - 0.00050) × 100 = **19.8700%**
- ATR(15m): 0.424286
- **typical_stop_pct** = 0.424286 / 104.210000 × 100 = **0.4071%**
- **liq_distance_pct** = (1/2.50 - 0.0050) × 100 = **39.5000%**
- Condition 1 (min_notional ≤ max_notional): PASS
- Condition 2 (typical_stop ≤ max_stop): PASS (0.4071% vs 19.8700%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (39.5000% vs 0.8143%)
- **TRADABLE: YES**

### Binance — XRP (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti

### Binance — DOGE (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti

### Bybit — BTC (USDT-PERP)
- Price: $81,225.100000
- min_qty: 0.001, lot_size: 0.001, tick_size: 0.1
- Exchange min_notional: $0.0000
- **min_notional_usd** = max($0.0000, 0.001 × $81225.1000) = **$81.2251**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $81.2251 / $2.0 = **40.61x** (cap: 3x)
- round_trip_fee_usd = $81.2251 × 2 × 0.055% = **$0.08935**
- net_loss_budget = $1.0 - $0.08935 = **$0.91065**
- **max_stop_pct** = ($0.91065 / $81.2251 - 0.00050) × 100 = **1.0711%**
- ATR(15m): 254.350000
- **typical_stop_pct** = 254.350000 / 81225.100000 × 100 = **0.3131%**
- **liq_distance_pct** = (1/40.61 - 0.0050) × 100 = **1.9623%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.3131% vs 1.0711%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (1.9623% vs 0.6263%)
- **TRADABLE: NO**

### Bybit — ETH (USDT-PERP)
- Price: $2,386.680000
- min_qty: 0.01, lot_size: 0.01, tick_size: 0.01
- Exchange min_notional: $0.0000
- **min_notional_usd** = max($0.0000, 0.01 × $2386.6800) = **$23.8668**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $23.8668 / $2.0 = **11.93x** (cap: 3x)
- round_trip_fee_usd = $23.8668 × 2 × 0.055% = **$0.02625**
- net_loss_budget = $1.0 - $0.02625 = **$0.97375**
- **max_stop_pct** = ($0.97375 / $23.8668 - 0.00050) × 100 = **4.0299%**
- ATR(15m): 9.832857
- **typical_stop_pct** = 9.832857 / 2386.680000 × 100 = **0.4120%**
- **liq_distance_pct** = (1/11.93 - 0.0050) × 100 = **7.8798%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.4120% vs 4.0299%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (7.8798% vs 0.8240%)
- **TRADABLE: NO**

### Bybit — SOL (USDT-PERP)
- Price: $103.810000
- min_qty: 0.1, lot_size: 0.1, tick_size: 0.01
- Exchange min_notional: $0.0000
- **min_notional_usd** = max($0.0000, 0.1 × $103.8100) = **$10.3810**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $10.3810 / $2.0 = **5.19x** (cap: 3x)
- round_trip_fee_usd = $10.3810 × 2 × 0.055% = **$0.01142**
- net_loss_budget = $1.0 - $0.01142 = **$0.98858**
- **max_stop_pct** = ($0.98858 / $10.3810 - 0.00050) × 100 = **9.4730%**
- ATR(15m): 0.450714
- **typical_stop_pct** = 0.450714 / 103.810000 × 100 = **0.4342%**
- **liq_distance_pct** = (1/5.19 - 0.0050) × 100 = **18.7660%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.4342% vs 9.4730%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (18.7660% vs 0.8683%)
- **TRADABLE: NO**

### Bybit — XRP (USDT-PERP)
- Price: $1.306200
- min_qty: 0.1, lot_size: 0.1, tick_size: 0.0001
- Exchange min_notional: $0.0000
- **min_notional_usd** = max($0.0000, 0.1 × $1.3062) = **$0.1306**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $0.1306 / $2.0 = **0.07x** (cap: 3x)
- round_trip_fee_usd = $0.1306 × 2 × 0.055% = **$0.00014**
- net_loss_budget = $1.0 - $0.00014 = **$0.99986**
- **max_stop_pct** = ($0.99986 / $0.1306 - 0.00050) × 100 = **765.4195%**
- ATR(15m): 0.005307
- **typical_stop_pct** = 0.005307 / 1.306200 × 100 = **0.4063%**
- **liq_distance_pct** = (1/1.00 - 0.0050) × 100 = **99.5000%**
- Condition 1 (min_notional ≤ max_notional): PASS
- Condition 2 (typical_stop ≤ max_stop): PASS (0.4063% vs 765.4195%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (99.5000% vs 0.8126%)
- **TRADABLE: YES**

### Bybit — DOGE (USDT-PERP)
- Price: $0.079240
- min_qty: 1.0, lot_size: 1.0, tick_size: 1e-05
- Exchange min_notional: $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × $0.0792) = **$0.0792**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $0.0792 / $2.0 = **0.04x** (cap: 3x)
- round_trip_fee_usd = $0.0792 × 2 × 0.055% = **$0.00009**
- net_loss_budget = $1.0 - $0.00009 = **$0.99991**
- **max_stop_pct** = ($0.99991 / $0.0792 - 0.00050) × 100 = **1261.8289%**
- ATR(15m): 0.000390
- **typical_stop_pct** = 0.000390 / 0.079240 × 100 = **0.4922%**
- **liq_distance_pct** = (1/1.00 - 0.0050) × 100 = **99.5000%**
- Condition 1 (min_notional ≤ max_notional): PASS
- Condition 2 (typical_stop ≤ max_stop): PASS (0.4922% vs 1261.8289%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (99.5000% vs 0.9844%)
- **TRADABLE: YES**

### OKX — BTC (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

### OKX — ETH (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

### OKX — SOL (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

### OKX — XRP (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

### OKX — DOGE (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

### MEXC — BTC (USDT-PERP)
- Price: $81,195.800000
- min_qty: 1.0, lot_size: 1.0, tick_size: 0.1
- Exchange min_notional: $8.1196
- **min_notional_usd** = max($8.1196, 1.0 × $81195.8000) = **$81195.8000**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $81195.8000 / $2.0 = **40597.90x** (cap: 3x)
- round_trip_fee_usd = $81195.8000 × 2 × 0.010% = **$16.23916**
- net_loss_budget = $1.0 - $16.23916 = **$-15.23916**
- max_stop_pct: N/A (net_loss_budget ≤ 0)
- ATR(15m): 309.971429
- **typical_stop_pct** = 309.971429 / 81195.800000 × 100 = **0.3818%**
- **liq_distance_pct** = (1/40597.90 - 0.0050) × 100 = **-0.4975%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): FAIL (0.3818% vs 0.0000%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): FAIL (-0.4975% vs 0.7635%)
- **TRADABLE: NO**

### MEXC — ETH (USDT-PERP)
- Price: $2,385.210000
- min_qty: 1.0, lot_size: 1.0, tick_size: 0.01
- Exchange min_notional: $23.8521
- **min_notional_usd** = max($23.8521, 1.0 × $2385.2100) = **$2385.2100**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $2385.2100 / $2.0 = **1192.61x** (cap: 3x)
- round_trip_fee_usd = $2385.2100 × 2 × 0.010% = **$0.47704**
- net_loss_budget = $1.0 - $0.47704 = **$0.52296**
- **max_stop_pct** = ($0.52296 / $2385.2100 - 0.00050) × 100 = **-0.0281%**
- ATR(15m): 12.781429
- **typical_stop_pct** = 12.781429 / 2385.210000 × 100 = **0.5359%**
- **liq_distance_pct** = (1/1192.61 - 0.0050) × 100 = **-0.4161%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): FAIL (0.5359% vs -0.0281%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): FAIL (-0.4161% vs 1.0717%)
- **TRADABLE: NO**

### MEXC — SOL (USDT-PERP)
- Price: $103.680000
- min_qty: 1.0, lot_size: 1.0, tick_size: 0.01
- Exchange min_notional: $10.3680
- **min_notional_usd** = max($10.3680, 1.0 × $103.6800) = **$103.6800**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $103.6800 / $2.0 = **51.84x** (cap: 3x)
- round_trip_fee_usd = $103.6800 × 2 × 0.010% = **$0.02074**
- net_loss_budget = $1.0 - $0.02074 = **$0.97926**
- **max_stop_pct** = ($0.97926 / $103.6800 - 0.00050) × 100 = **0.8945%**
- ATR(15m): 0.558571
- **typical_stop_pct** = 0.558571 / 103.680000 × 100 = **0.5387%**
- **liq_distance_pct** = (1/51.84 - 0.0050) × 100 = **1.4290%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.5387% vs 0.8945%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (1.4290% vs 1.0775%)
- **TRADABLE: NO**

### MEXC — XRP (USDT-PERP)
- Price: $1.305200
- min_qty: 1.0, lot_size: 1.0, tick_size: 0.0001
- Exchange min_notional: $1.3052
- **min_notional_usd** = max($1.3052, 1.0 × $1.3052) = **$1.3052**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $1.3052 / $2.0 = **0.65x** (cap: 3x)
- round_trip_fee_usd = $1.3052 × 2 × 0.010% = **$0.00026**
- net_loss_budget = $1.0 - $0.00026 = **$0.99974**
- **max_stop_pct** = ($0.99974 / $1.3052 - 0.00050) × 100 = **76.5466%**
- ATR(15m): 0.006714
- **typical_stop_pct** = 0.006714 / 1.305200 × 100 = **0.5144%**
- **liq_distance_pct** = (1/1.00 - 0.0050) × 100 = **99.5000%**
- Condition 1 (min_notional ≤ max_notional): PASS
- Condition 2 (typical_stop ≤ max_stop): PASS (0.5144% vs 76.5466%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (99.5000% vs 1.0289%)
- **TRADABLE: YES**

### MEXC — DOGE (USDT-PERP)
- Price: $0.079230
- min_qty: 1.0, lot_size: 1.0, tick_size: 1e-05
- Exchange min_notional: $7.9230
- **min_notional_usd** = max($7.9230, 1.0 × $0.0792) = **$7.9230**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $7.9230 / $2.0 = **3.96x** (cap: 3x)
- round_trip_fee_usd = $7.9230 × 2 × 0.010% = **$0.00158**
- net_loss_budget = $1.0 - $0.00158 = **$0.99842**
- **max_stop_pct** = ($0.99842 / $7.9230 - 0.00050) × 100 = **12.5515%**
- ATR(15m): 0.000436
- **typical_stop_pct** = 0.000436 / 0.079230 × 100 = **0.5499%**
- **liq_distance_pct** = (1/3.96 - 0.0050) × 100 = **24.7430%**
- Condition 1 (min_notional ≤ max_notional): FAIL
- Condition 2 (typical_stop ≤ max_stop): PASS (0.5499% vs 12.5515%)
- Condition 3 (liq_dist ≥ 2.0× typical_stop): PASS (24.7430% vs 1.0999%)
- **TRADABLE: NO**

## Tradable Summary

**Tradable (4):**
- Binance SOL
- Bybit XRP
- Bybit DOGE
- MEXC XRP

**Not tradable (8):**
- Binance BTC: conditions failed
- Bybit BTC: conditions failed
- Bybit ETH: conditions failed
- Bybit SOL: conditions failed
- MEXC BTC: conditions failed
- MEXC ETH: conditions failed
- MEXC SOL: conditions failed
- MEXC DOGE: conditions failed

**Errors (8):**
- Binance ETH: HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti
- Binance XRP: HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti
- Binance DOGE: HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out. (read ti
- OKX BTC: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url
- OKX ETH: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url
- OKX SOL: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url
- OKX XRP: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url
- OKX DOGE: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url

## Owner Action Required
1. Review the tradable/not-tradable table above.
2. If no symbols are tradable, consider raising `max_margin_per_trade_usd` or `max_leverage` in `config/risk.yaml`.
3. Choose exchange + symbols based on this report.
4. See ADR-002 for cap semantics options.
5. Confirm exchange availability in your country (see ADR-001).
