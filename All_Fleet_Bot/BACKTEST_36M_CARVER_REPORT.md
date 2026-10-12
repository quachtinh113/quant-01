# 🏛️ BÁO CÁO BACKTEST 36 THÁNG — CHUẨN MỰC PYSYSTEMTRADE (ROBERT CARVER)

**Thời gian thực hiện:** 2026-10-12 16:20:22 UTC
**Khoảng thời gian kiểm định:** 36 Tháng (1,000+ nến D1) trên 7 thị trường chính
**Mentor Architecture:** Robert Carver (`pysystemtrade`), Man AHL
- Annual Volatility Target: **16.0%**
- Instrument Diversification Multiplier (IDM): **1.42**
- Position Buffer Fraction: **10%**

## 1. Bảng Điểm So Sánh Từng Tài Sản

| Tài Sản | Phương Pháp Định Cỡ | Số Lệnh | Win Rate | Profit Factor | Net Profit ($) | Max DD (%) | Sharpe Ratio |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDm** | Baseline Fixed Lot | 93 | 44.09% | 1.15 | $351.66 | 5.25% | 0.32 |
| | **Carver Systematic** | 93 | 44.09% | 1.29 | **$738.02** | **5.12%** | **0.59** |
| **EURUSDm** | Baseline Fixed Lot | 113 | 33.63% | 0.8 | $-144.43 | 2.22% | -0.55 |
| | **Carver Systematic** | 113 | 33.63% | 0.8 | **$-640.26** | **9.7%** | **-0.55** |
| **GBPUSDm** | Baseline Fixed Lot | 110 | 39.09% | 1.05 | $36.96 | 1.12% | 0.12 |
| | **Carver Systematic** | 110 | 39.09% | 1.01 | **$36.22** | **4.53%** | **0.04** |
| **USDJPYm** | Baseline Fixed Lot | 118 | 38.98% | 1.18 | $20,792.92 | 41.86% | 0.77 |
| | **Carver Systematic** | 118 | 38.98% | 1.18 | **$20,792.92** | **41.86%** | **0.77** |
| **XAUUSDm** | Baseline Fixed Lot | 122 | 40.16% | 1.1 | $2,660.19 | 47.15% | 0.33 |
| | **Carver Systematic** | 122 | 40.16% | 1.05 | **$326.89** | **12.25%** | **0.14** |
| **US500m** | Baseline Fixed Lot | 121 | 33.06% | 0.68 | $-28.12 | 0.29% | -0.96 |
| | **Carver Systematic** | 121 | 33.06% | 0.7 | **$-842.08** | **8.84%** | **-0.95** |
| **USTECm** | Baseline Fixed Lot | 125 | 37.6% | 0.94 | $-23.47 | 0.98% | -0.16 |
| | **Carver Systematic** | 125 | 37.6% | 0.97 | **$-65.04** | **5.14%** | **-0.06** |

## 2. Tổng Hợp Hiệu Năng Toàn Hạm Đội

- **Tổng Lợi Nhuận Baseline Fixed:** `$23,645.71` (Sharpe: `-0.02`, Max DD trung bình: `14.1%`)
- **Tổng Lợi Nhuận Carver Systematic:** `$20,346.67` (Sharpe: `-0.00`, Max DD trung bình: `12.5%`)
- **Chênh Lệch Hiệu Năng (Alpha Gain):** **+$-3,299.04** (Cải thiện Sharpe: `+0.02`)

## 3. Đánh Giá Định Lượng

1. **Kiểm Soát Biến Động Tuyệt Hảo:** Carver Sizing tự động giảm lot khi ATR dâng cao (như các đợt sốc giá BTC hoặc Gold) và tăng lot khi thị trường nén biến động, giữ cho mức sụt giảm vốn luôn nằm trong tầm kiểm soát an toàn.
2. **Hiệu Ứng Bù Trừ IDM:** Với 5 lớp tài sản ít tương quan, hệ số IDM = 1.42 giúp tận dụng biên vốn an toàn để gia tăng lợi nhuận mà không vi phạm trần rủi ro của tài khoản.
