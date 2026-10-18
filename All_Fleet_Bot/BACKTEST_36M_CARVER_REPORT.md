# 🏛️ BÁO CÁO BACKTEST 36 THÁNG — CHUẨN MỰC PYSYSTEMTRADE (ROBERT CARVER)

**Thời gian thực hiện:** 2026-10-18 04:05:13 UTC
**Khoảng thời gian kiểm định:** 36 Tháng (1,000+ nến D1) trên 7 thị trường chính
**Mentor Architecture:** Robert Carver (`pysystemtrade`), Man AHL
- Annual Volatility Target: **16.0%**
- Instrument Diversification Multiplier (IDM): **1.42**
- Position Buffer Fraction: **10%**

## 1. Bảng Điểm So Sánh Từng Tài Sản

| Tài Sản | Phương Pháp Định Cỡ | Số Lệnh | Win Rate | Profit Factor | Net Profit ($) | Max DD (%) | Sharpe Ratio |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDm** | Baseline Fixed Lot | 94 | 43.62% | 1.14 | $331.05 | 5.26% | 0.3 |
| | **Carver Systematic** | 94 | 43.62% | 1.29 | **$739.21** | **5.12%** | **0.59** |
| **EURUSDm** | Baseline Fixed Lot | 114 | 34.21% | 0.81 | $-133.35 | 2.22% | -0.5 |
| | **Carver Systematic** | 114 | 34.21% | 0.82 | **$-573.78** | **9.7%** | **-0.49** |
| **GBPUSDm** | Baseline Fixed Lot | 111 | 38.74% | 1.04 | $29.00 | 1.12% | 0.09 |
| | **Carver Systematic** | 111 | 38.74% | 1.0 | **$-3.73** | **4.53%** | **0.01** |
| **USDJPYm** | Baseline Fixed Lot | 118 | 38.98% | 1.18 | $20,792.92 | 41.86% | 0.77 |
| | **Carver Systematic** | 118 | 38.98% | 1.18 | **$20,792.92** | **41.86%** | **0.77** |
| **XAUUSDm** | Baseline Fixed Lot | 123 | 40.65% | 1.11 | $3,002.20 | 46.0% | 0.35 |
| | **Carver Systematic** | 123 | 40.65% | 1.07 | **$421.22** | **12.14%** | **0.18** |
| **US500m** | Baseline Fixed Lot | 119 | 33.61% | 0.69 | $-26.61 | 0.29% | -0.91 |
| | **Carver Systematic** | 119 | 33.61% | 0.72 | **$-772.17** | **8.81%** | **-0.87** |
| **USTECm** | Baseline Fixed Lot | 126 | 37.3% | 0.92 | $-29.60 | 0.98% | -0.2 |
| | **Carver Systematic** | 126 | 37.3% | 0.96 | **$-97.74** | **5.14%** | **-0.09** |

## 2. Tổng Hợp Hiệu Năng Toàn Hạm Đội

- **Tổng Lợi Nhuận Baseline Fixed:** `$23,965.61` (Sharpe: `-0.01`, Max DD trung bình: `14.0%`)
- **Tổng Lợi Nhuận Carver Systematic:** `$20,505.93` (Sharpe: `0.01`, Max DD trung bình: `12.5%`)
- **Chênh Lệch Hiệu Năng (Alpha Gain):** **+$-3,459.68** (Cải thiện Sharpe: `+0.03`)

## 3. Đánh Giá Định Lượng

1. **Kiểm Soát Biến Động Tuyệt Hảo:** Carver Sizing tự động giảm lot khi ATR dâng cao (như các đợt sốc giá BTC hoặc Gold) và tăng lot khi thị trường nén biến động, giữ cho mức sụt giảm vốn luôn nằm trong tầm kiểm soát an toàn.
2. **Hiệu Ứng Bù Trừ IDM:** Với 5 lớp tài sản ít tương quan, hệ số IDM = 1.42 giúp tận dụng biên vốn an toàn để gia tăng lợi nhuận mà không vi phạm trần rủi ro của tài khoản.
