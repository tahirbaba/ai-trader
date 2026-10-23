# Phase 0 Feasibility Report
Generated: 2026-10-23 15:13 UTC (v2 — fixed MEXC contractSize, Bybit minNotionalValue,
MaxStop% at planned notional, Binance retry)

## Risk Constants Used
- `MAX_MARGIN_USD` = $2.0
- `MAX_LOSS_USD` = $1.0
- `SLIPPAGE_PCT` = 0.05% (0.0005 as fraction)
- `MAX_LEVERAGE` = 3x
- `LIQ_BUFFER_MULTIPLE` = 2.0
- `MAINT_MARGIN_PCT` = 0.5%
- `MAX_NOTIONAL_USD` = $6.00 (= margin × leverage)
- `PLANNED_NOTIONAL_USD` = $6.00 (what we size to; MaxStop% and LiqDist% computed here)

## Summary Table

| Exchange | Symbol | Price | MinNotional | ≤$6? | ReqLev | TakerFee% | MaxStop%† | TypStop%(ATR) | LiqDist%† | Tradable |
|---|---|---|---|---|---|---|---|---|---|---|
| Binance | BTC | $81,451.8000 | $81.4518 | NO | 40.7x | 0.040% | 16.54% | 0.382% | 32.83% | NO |
| Binance | ETH | $2,398.0200 | $20.0000 | NO | 10.0x | 0.040% | 16.54% | 0.510% | 32.83% | NO |
| Binance | SOL | $104.3600 | $5.0000 | YES | 2.5x | 0.040% | 16.54% | 0.534% | 32.83% | **YES** |
| Binance | XRP | $1.3075 | $5.0000 | YES | 2.5x | 0.040% | 16.54% | 0.511% | 32.83% | **YES** |
| Binance | DOGE | $0.0799 | $5.0000 | YES | 2.5x | 0.040% | 16.54% | 0.546% | 32.83% | **YES** |
| Bybit | BTC | $81,475.1000 | $81.4751 | NO | 40.7x | 0.055% | 16.51% | 0.388% | 32.83% | NO |
| Bybit | ETH | $2,398.4400 | $23.9844 | NO | 12.0x | 0.055% | 16.51% | 0.521% | 32.83% | NO |
| Bybit | SOL | $104.3800 | $10.4380 | NO | 5.2x | 0.055% | 16.51% | 0.535% | 32.83% | NO |
| Bybit | XRP | $1.3074 | $5.0000 | YES | 2.5x | 0.055% | 16.51% | 0.506% | 32.83% | **YES** |
| Bybit | DOGE | $0.0799 | $5.0000 | YES | 2.5x | 0.055% | 16.51% | 0.539% | 32.83% | **YES** |
| OKX | BTC | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR: HTTPSConnectionPool(host='www.okx.c Re-run from VPS (OKX geo-restricts residential IPs) |
| OKX | ETH | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR: HTTPSConnectionPool(host='www.okx.c Re-run from VPS (OKX geo-restricts residential IPs) |
| OKX | SOL | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR: HTTPSConnectionPool(host='www.okx.c Re-run from VPS (OKX geo-restricts residential IPs) |
| OKX | XRP | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR: HTTPSConnectionPool(host='www.okx.c Re-run from VPS (OKX geo-restricts residential IPs) |
| OKX | DOGE | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR: HTTPSConnectionPool(host='www.okx.c Re-run from VPS (OKX geo-restricts residential IPs) |
| MEXC | BTC | $81,483.5000 | $8.1484 | NO | 4.1x | 0.010% | 16.60% | 0.492% | 32.83% | NO |
| MEXC | ETH | $2,398.8800 | $23.9888 | NO | 12.0x | 0.010% | 16.60% | 0.705% | 32.83% | NO |
| MEXC | SOL | $104.4000 | $10.4400 | NO | 5.2x | 0.010% | 16.60% | 0.709% | 32.83% | NO |
| MEXC | XRP | $1.3074 | $1.3074 | YES | 0.7x | 0.010% | 16.60% | 0.667% | 32.83% | **YES** |
| MEXC | DOGE | $0.0799 | $7.9900 | NO | 4.0x | 0.010% | 16.60% | 0.717% | 32.83% | NO |

† MaxStop% and LiqDist% computed at **planned_notional = $6.00** (MAX_MARGIN × MAX_LEV), not at the exchange-minimum notional.

## Per-Row Math

### Binance — BTC (USDT-PERP)
- Price: $81,451.800000
- min_qty=0.001, contract_size=1.0, lot_size=0.001, tick_size=0.1
- Exchange min_notional (raw): $50.0000
- **min_notional_usd** = max($50.0000, 0.001 × 1.0 × $81451.8000) = **$81.4518**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $81.4518 / $2.0 = **40.73x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.040% = **$0.00480**
- net_loss_budget = $1.0 − $0.00480 = **$0.99520**
- **max_stop_pct** = ($0.99520 / $6.00 − 0.00050) × 100 = **16.5367%**
- ATR(15m) = 310.957143
- **typical_stop_pct** = 310.957143 / 81451.800000 × 100 = **0.3818%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.3818% vs 16.5367%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 0.7635%)
- **TRADABLE: NO**

### Binance — ETH (USDT-PERP)
- Price: $2,398.020000
- min_qty=0.001, contract_size=1.0, lot_size=0.001, tick_size=0.01
- Exchange min_notional (raw): $20.0000
- **min_notional_usd** = max($20.0000, 0.001 × 1.0 × $2398.0200) = **$20.0000**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $20.0000 / $2.0 = **10.00x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.040% = **$0.00480**
- net_loss_budget = $1.0 − $0.00480 = **$0.99520**
- **max_stop_pct** = ($0.99520 / $6.00 − 0.00050) × 100 = **16.5367%**
- ATR(15m) = 12.222857
- **typical_stop_pct** = 12.222857 / 2398.020000 × 100 = **0.5097%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5097% vs 16.5367%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0194%)
- **TRADABLE: NO**

### Binance — SOL (USDT-PERP)
- Price: $104.360000
- min_qty=0.01, contract_size=1.0, lot_size=0.01, tick_size=0.01
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.01 × 1.0 × $104.3600) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.040% = **$0.00480**
- net_loss_budget = $1.0 − $0.00480 = **$0.99520**
- **max_stop_pct** = ($0.99520 / $6.00 − 0.00050) × 100 = **16.5367%**
- ATR(15m) = 0.557143
- **typical_stop_pct** = 0.557143 / 104.360000 × 100 = **0.5339%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5339% vs 16.5367%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0677%)
- **TRADABLE: YES**

### Binance — XRP (USDT-PERP)
- Price: $1.307500
- min_qty=0.1, contract_size=1.0, lot_size=0.1, tick_size=0.0001
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.1 × 1.0 × $1.3075) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.040% = **$0.00480**
- net_loss_budget = $1.0 − $0.00480 = **$0.99520**
- **max_stop_pct** = ($0.99520 / $6.00 − 0.00050) × 100 = **16.5367%**
- ATR(15m) = 0.006686
- **typical_stop_pct** = 0.006686 / 1.307500 × 100 = **0.5113%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5113% vs 16.5367%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0227%)
- **TRADABLE: YES**

### Binance — DOGE (USDT-PERP)
- Price: $0.079880
- min_qty=1.0, contract_size=1.0, lot_size=1.0, tick_size=1e-05
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 1.0 × 1.0 × $0.0799) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.040% = **$0.00480**
- net_loss_budget = $1.0 − $0.00480 = **$0.99520**
- **max_stop_pct** = ($0.99520 / $6.00 − 0.00050) × 100 = **16.5367%**
- ATR(15m) = 0.000436
- **typical_stop_pct** = 0.000436 / 0.079880 × 100 = **0.5464%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5464% vs 16.5367%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0927%)
- **TRADABLE: YES**

### Bybit — BTC (USDT-PERP)
- Price: $81,475.100000
- min_qty=0.001, contract_size=1.0, lot_size=0.001, tick_size=0.1
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.001 × 1.0 × $81475.1000) = **$81.4751**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $81.4751 / $2.0 = **40.74x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.055% = **$0.00660**
- net_loss_budget = $1.0 − $0.00660 = **$0.99340**
- **max_stop_pct** = ($0.99340 / $6.00 − 0.00050) × 100 = **16.5067%**
- ATR(15m) = 316.057143
- **typical_stop_pct** = 316.057143 / 81475.100000 × 100 = **0.3879%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.3879% vs 16.5067%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 0.7758%)
- **TRADABLE: NO**

### Bybit — ETH (USDT-PERP)
- Price: $2,398.440000
- min_qty=0.01, contract_size=1.0, lot_size=0.01, tick_size=0.01
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.01 × 1.0 × $2398.4400) = **$23.9844**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $23.9844 / $2.0 = **11.99x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.055% = **$0.00660**
- net_loss_budget = $1.0 − $0.00660 = **$0.99340**
- **max_stop_pct** = ($0.99340 / $6.00 − 0.00050) × 100 = **16.5067%**
- ATR(15m) = 12.501429
- **typical_stop_pct** = 12.501429 / 2398.440000 × 100 = **0.5212%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5212% vs 16.5067%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0425%)
- **TRADABLE: NO**

### Bybit — SOL (USDT-PERP)
- Price: $104.380000
- min_qty=0.1, contract_size=1.0, lot_size=0.1, tick_size=0.01
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.1 × 1.0 × $104.3800) = **$10.4380**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $10.4380 / $2.0 = **5.22x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.055% = **$0.00660**
- net_loss_budget = $1.0 − $0.00660 = **$0.99340**
- **max_stop_pct** = ($0.99340 / $6.00 − 0.00050) × 100 = **16.5067%**
- ATR(15m) = 0.558571
- **typical_stop_pct** = 0.558571 / 104.380000 × 100 = **0.5351%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5351% vs 16.5067%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0703%)
- **TRADABLE: NO**

### Bybit — XRP (USDT-PERP)
- Price: $1.307400
- min_qty=0.1, contract_size=1.0, lot_size=0.1, tick_size=0.0001
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 0.1 × 1.0 × $1.3074) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.055% = **$0.00660**
- net_loss_budget = $1.0 − $0.00660 = **$0.99340**
- **max_stop_pct** = ($0.99340 / $6.00 − 0.00050) × 100 = **16.5067%**
- ATR(15m) = 0.006614
- **typical_stop_pct** = 0.006614 / 1.307400 × 100 = **0.5059%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5059% vs 16.5067%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0118%)
- **TRADABLE: YES**

### Bybit — DOGE (USDT-PERP)
- Price: $0.079870
- min_qty=1.0, contract_size=1.0, lot_size=1.0, tick_size=1e-05
- Exchange min_notional (raw): $5.0000
- **min_notional_usd** = max($5.0000, 1.0 × 1.0 × $0.0799) = **$5.0000**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $5.0000 / $2.0 = **2.50x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.055% = **$0.00660**
- net_loss_budget = $1.0 − $0.00660 = **$0.99340**
- **max_stop_pct** = ($0.99340 / $6.00 − 0.00050) × 100 = **16.5067%**
- ATR(15m) = 0.000431
- **typical_stop_pct** = 0.000431 / 0.079870 × 100 = **0.5393%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.5393% vs 16.5067%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.0785%)
- **TRADABLE: YES**

### OKX — BTC (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url  Note: Re-run from VPS (OKX geo-restricts residential IPs)

### OKX — ETH (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url  Note: Re-run from VPS (OKX geo-restricts residential IPs)

### OKX — SOL (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url  Note: Re-run from VPS (OKX geo-restricts residential IPs)

### OKX — XRP (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url  Note: Re-run from VPS (OKX geo-restricts residential IPs)

### OKX — DOGE (USDT-PERP)
**ERROR:** HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url  Note: Re-run from VPS (OKX geo-restricts residential IPs)

### MEXC — BTC (USDT-PERP)
- Price: $81,483.500000
- min_qty=1.0, contract_size=0.0001, lot_size=1.0, tick_size=0.1
- Exchange min_notional (raw): $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × 0.0001 × $81483.5000) = **$8.1484**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $8.1484 / $2.0 = **4.07x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.010% = **$0.00120**
- net_loss_budget = $1.0 − $0.00120 = **$0.99880**
- **max_stop_pct** = ($0.99880 / $6.00 − 0.00050) × 100 = **16.5967%**
- ATR(15m) = 401.028571
- **typical_stop_pct** = 401.028571 / 81483.500000 × 100 = **0.4922%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.4922% vs 16.5967%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 0.9843%)
- **TRADABLE: NO**

### MEXC — ETH (USDT-PERP)
- Price: $2,398.880000
- min_qty=1.0, contract_size=0.01, lot_size=1.0, tick_size=0.01
- Exchange min_notional (raw): $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × 0.01 × $2398.8800) = **$23.9888**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $23.9888 / $2.0 = **11.99x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.010% = **$0.00120**
- net_loss_budget = $1.0 − $0.00120 = **$0.99880**
- **max_stop_pct** = ($0.99880 / $6.00 − 0.00050) × 100 = **16.5967%**
- ATR(15m) = 16.921429
- **typical_stop_pct** = 16.921429 / 2398.880000 × 100 = **0.7054%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.7054% vs 16.5967%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.4108%)
- **TRADABLE: NO**

### MEXC — SOL (USDT-PERP)
- Price: $104.400000
- min_qty=1.0, contract_size=0.1, lot_size=1.0, tick_size=0.01
- Exchange min_notional (raw): $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × 0.1 × $104.4000) = **$10.4400**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $10.4400 / $2.0 = **5.22x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.010% = **$0.00120**
- net_loss_budget = $1.0 − $0.00120 = **$0.99880**
- **max_stop_pct** = ($0.99880 / $6.00 − 0.00050) × 100 = **16.5967%**
- ATR(15m) = 0.740000
- **typical_stop_pct** = 0.740000 / 104.400000 × 100 = **0.7088%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.7088% vs 16.5967%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.4176%)
- **TRADABLE: NO**

### MEXC — XRP (USDT-PERP)
- Price: $1.307400
- min_qty=1.0, contract_size=1.0, lot_size=1.0, tick_size=0.0001
- Exchange min_notional (raw): $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × 1.0 × $1.3074) = **$1.3074**
- max_notional_allowed = $6.00 → fits? **YES**
- **required_leverage** = $1.3074 / $2.0 = **0.65x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.010% = **$0.00120**
- net_loss_budget = $1.0 − $0.00120 = **$0.99880**
- **max_stop_pct** = ($0.99880 / $6.00 − 0.00050) × 100 = **16.5967%**
- ATR(15m) = 0.008714
- **typical_stop_pct** = 0.008714 / 1.307400 × 100 = **0.6665%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): PASS
- Cond 2 (typical_stop ≤ max_stop): PASS (0.6665% vs 16.5967%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.3331%)
- **TRADABLE: YES**

### MEXC — DOGE (USDT-PERP)
- Price: $0.079900
- min_qty=1.0, contract_size=100.0, lot_size=1.0, tick_size=1e-05
- Exchange min_notional (raw): $0.0000
- **min_notional_usd** = max($0.0000, 1.0 × 100.0 × $0.0799) = **$7.9900**
- max_notional_allowed = $6.00 → fits? **NO**
- **required_leverage** = $7.9900 / $2.0 = **4.00x** (cap: 3x)
- **planned_notional** = $6.00 (MAX_MARGIN × MAX_LEV — stop budget computed here)
- round_trip_fee = $6.00 × 2 × 0.010% = **$0.00120**
- net_loss_budget = $1.0 − $0.00120 = **$0.99880**
- **max_stop_pct** = ($0.99880 / $6.00 − 0.00050) × 100 = **16.5967%**
- ATR(15m) = 0.000573
- **typical_stop_pct** = 0.000573 / 0.079900 × 100 = **0.7170%**
- planned_leverage = $6.00 / $2.0 = 3.0x
- **liq_distance_pct** = (1/3.0 − 0.0050) × 100 = **32.8333%**
- Cond 1 (min_notional ≤ $6.00): FAIL
- Cond 2 (typical_stop ≤ max_stop): PASS (0.7170% vs 16.5967%)
- Cond 3 (liq_dist ≥ 2.0× typical_stop): PASS (32.8333% vs 1.4339%)
- **TRADABLE: NO**

## Tradable Summary

**Tradable (6):**
- Binance SOL
- Binance XRP
- Binance DOGE
- Bybit XRP
- Bybit DOGE
- MEXC XRP

**Not tradable (9):**
- Binance BTC: conditions failed
- Binance ETH: conditions failed
- Bybit BTC: conditions failed
- Bybit ETH: conditions failed
- Bybit SOL: conditions failed
- MEXC BTC: conditions failed
- MEXC ETH: conditions failed
- MEXC SOL: conditions failed
- MEXC DOGE: conditions failed

**Errors / re-run needed (5):**
- OKX BTC: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url — Re-run from VPS (OKX geo-restricts residential IPs)
- OKX ETH: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url — Re-run from VPS (OKX geo-restricts residential IPs)
- OKX SOL: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url — Re-run from VPS (OKX geo-restricts residential IPs)
- OKX XRP: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url — Re-run from VPS (OKX geo-restricts residential IPs)
- OKX DOGE: HTTPSConnectionPool(host='www.okx.com', port=443): Max retries exceeded with url — Re-run from VPS (OKX geo-restricts residential IPs)

## Owner Action Required
1. Review the tradable/not-tradable table above.
2. If no symbols are tradable, consider raising `max_margin_per_trade_usd` or `max_leverage` in `config/risk.yaml` (owner-only edit).
3. Re-run this script from a VPS for OKX results.
4. Choose exchange + symbols based on this report.
5. See ADR-002 for cap semantics options.
6. Confirm exchange availability in your country (see ADR-001).
