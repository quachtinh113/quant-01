# Quant 01 — Unified Quant Fleet

Hệ thống giao dịch định lượng đa chiến lược (Multi-Strategy Institutional Quant Fleet) vận hành đồng thời trên nền tảng MetaTrader 5:

- **Bot 1 (`BTC_8H`):** Giao dịch xu hướng Bitcoin khung 8H với bộ lọc ADX(14) và RSI(14) chống bẫy đảo chiều.
- **Bot 2 (`FX_D1`):** Bắt sóng xu hướng các cặp Forex Majors (EURUSD, USDJPY, USDCAD) tại thời điểm đóng nến ngày D1.
- **Bot 3 (`GOLD_SESS`):** Chiến lược Vàng (XAUUSD) quét phiên với cơ chế Breakeven Lock (+1.5x ATR) và Dynamic Trailing Stop (+2.0x ATR).
- **Bot 4 (`USIDX_SESS`):** Bắt nhịp bứt phá phiên mở cửa/đóng cửa chỉ số chứng khoán Mỹ (US500, USTEC).
- **Bot 5 (`NOWTRADING_MTF`):** Chiến lược đa khung thời gian M15-H1-H4 kết hợp RSI và ADX.

## Cấu Trúc Dự Án

```text
Quant_01/
├── All_Fleet_Bot/
│   ├── run_unified_bot.py         # Master Orchestrator điều phối toàn bộ hạm đội bot
│   ├── start_bot.bat              # Script khởi động tự động có Watchdog giám sát
│   ├── test_fleet.py              # Bộ kiểm thử tự động toàn diện (Test Suite)
│   ├── config/                    # Cấu hình tham số và Magic Registry
│   ├── core/                      # Quản lý kết nối MT5, Risk Guard và Logger
│   ├── strategies/                # 5 module chiến lược định lượng độc lập
│   └── logs/                      # Nhật ký giao dịch thời gian thực
├── .gitignore
└── README.md
```

## Bắt Đầu Nhanh

1. Cài đặt các thư viện cần thiết:
   ```bash
   pip install MetaTrader5 pandas numpy pyyaml python-dotenv
   ```
2. Cấu hình thông tin tài khoản MT5:
   Sao chép file `All_Fleet_Bot/.env.example` thành `All_Fleet_Bot/.env` và điền thông tin tài khoản MT5 của bạn.
3. Chạy kiểm tra hệ thống:
   ```bash
   python All_Fleet_Bot/test_fleet.py
   ```
4. Khởi động hệ thống:
   ```bash
   python All_Fleet_Bot/run_unified_bot.py
   ```
