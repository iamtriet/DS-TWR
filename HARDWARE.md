# Cyborg greenC — Hardware Reference

**Board:** Cyborg greenC v001 (36.5 × 45.5 mm)  
**Revision:** Tổng hợp từ schematic Altium + kết quả bring-up thực tế  
**Cập nhật:** 2026-10-01

---

## 1. Tổng quan hệ thống

```
┌───────────────────────────────────────────────────────┐
│              Bo Main — Cyborg greenC v001              │
│                                                       │
│  ┌──────────────────────┐    ┌──────────────────────┐ │
│  │  DWM3001C (U7)       │    │  BNO085 IMU (U?)     │ │
│  │  ┌────────┐ ┌──────┐ │    │  I2C0 (SDA/SCL)      │ │
│  │  │nRF52833│ │DW3110│ │    │  INT=P0.04           │ │
│  │  │Cortex-M4│ │ UWB  │ │    │  RST=P0.22           │ │
│  │  │512K/128K│ │      │ │    └──────────────────────┘ │
│  │  └────────┘ └──────┘ │                             │
│  └──────────────────────┘                             │
│                                                       │
│  [JP1 8-pin] ─── Bo con LED UV / LED RGB / Debug      │
│  [JP4 6-pin] ─── USB hoặc UART (chọn bằng R jumper)  │
│  [SWD]       ─── Nạp code (ST-Link V2)               │
│                                                       │
│  Nguồn: LiPo → TP4056 → LDO → 3V3                   │
└───────────────────────────────────────────────────────┘
```

---

## 2. MCU — nRF52833 (trong module DWM3001C)

| Thông số | Giá trị |
|----------|---------|
| Core | ARM Cortex-M4F, 64 MHz |
| Flash | 512 KB on-chip (không flash ngoài) |
| RAM | 128 KB SRAM |
| Radio | BLE 5.2 (tích hợp) |
| UWB | DW3110 (trong module, giao tiếp qua SPI3) |
| Package | Module Qorvo DWM3001C (U7) |

### Device ID (FICR)
Dùng để tự động phân vai trò Anchor/Tag:

| Board | Device ID | Vai trò |
|-------|-----------|---------|
| Board 1 | `DC6EBB5F76A10829` | ANCHOR |
| Board 2 | `C992073E09EC3021` | TAG |

---

## 3. GPIO Pin Map — Tổng hợp

### 3.1 DW3110 UWB Transceiver (nội bộ module)

| Chức năng | Pin nRF52833 | Port | Hướng | Ghi chú |
|-----------|-------------|------|-------|---------|
| SPI3 SCK | P0.03 | GPIO0 | Output | Không expose ra ngoài module |
| SPI3 MOSI | P0.08 | GPIO0 | Output | Không expose ra ngoài module |
| SPI3 MISO | P0.29 | GPIO0 | Input | ⚠️ **KHÔNG PHẢI P0.06** (đã xác nhận) |
| SPI3 CS | P1.06 | GPIO1 | Output | Active LOW |
| DW IRQ | P1.02 | GPIO1 | Input | Active HIGH, có pull-down |
| DW RESET | P0.25 | GPIO0 | Output | Active LOW |
| DW WAKEUP | P0.15 | GPIO0 | Output | Active HIGH |

> [!WARNING]
> **SPI MISO = P0.29**, không phải P0.06 như một số tài liệu cũ ghi.
> P0.06 là LED status trên PCB. Nếu cấu hình sai sẽ gây xung đột SPI ↔ LED.

### 3.2 UART Debug Console

| Chức năng | Pin nRF52833 | Ghi chú |
|-----------|-------------|---------|
| UART TX (MCU phát) | P0.20 | ⚠️ **SWAP** so với label trên schematic |
| UART RX (MCU nhận) | P0.21 | ⚠️ **SWAP** so với label trên schematic |
| Baudrate | 115200 | 8N1 |

> [!IMPORTANT]
> TX/RX đã được xác nhận qua raw register diagnostic — swap so với label trên schematic.
> Đã cấu hình trong DTS: `tx-pin = <20>; rx-pin = <21>;`

### 3.3 LED

| Tên | Pin | Cực tính | Mạch | Ghi chú |
|-----|-----|----------|------|---------|
| **LED3** | P0.06 | **ACTIVE LOW** | 3V3 → R12 → anode, cathode → P0.06 | LED duy nhất MCU điều khiển được |
| LED4 | — | — | Nối cứng 3V3 | Đèn nguồn, không điều khiển được |
| LED1 | — | — | TP4056 charge | Báo đang sạc, MCU không chạm |
| LED2 | — | — | TP4056 charge | Báo sạc đầy, MCU không chạm |

### 3.4 PWM / Stimulation Outputs (Header JP1)

| Nhãn | Pin nRF52833 | JP1 Pin | Trạng thái | Ghi chú |
|------|-------------|---------|------------|---------|
| PWM1 | P0.23 | 8 | ✅ Khả dụng | |
| PWM2 | P0.07 | 7 | ✅ Khả dụng | |
| PWM3 | P0.27 | 6 | ✅ Khả dụng | |
| PWM4 | P0.31 | 5 | ✅ Khả dụng | |
| PWM5 | P0.30 | 4 | ✅ Khả dụng | **→ UV LED trái** |
| PWM6 | DW_GP3 (pin 44) | 3 | ❌ **KHÔNG DÙNG** | GPIO của DW3110, chỉ qua SPI |

> [!CAUTION]
> **PWM6 KHÔNG tồn tại với firmware!** Chân 3 của JP1 = DW_GP3 = GPIO của chip DW3000.
> Chỉ điều khiển qua SPI register, không phải GPIO nRF52833.
> **Tuyệt đối không khai báo PWM6 trong devicetree.**

### 3.5 UV LED (Bo con LED UV v003)

Kết quả đo thực tế (2026-09-17):

| LED | Pin nRF52833 | Nhãn | Xác nhận |
|-----|-------------|------|----------|
| UV trái | P0.30 | PWM5 | ✅ Đã test (sweep) |
| UV phải | P0.02 | — | ✅ Đã test (sweep) |

> [!NOTE]
> P0.02 không nằm trong danh sách PWM1-PWM5 gốc. Đây là kênh bổ sung
> chỉ phát hiện qua test thực tế. Chưa rõ mạch lái (trực tiếp hay qua MOSFET).

### 3.6 BNO085 IMU (I2C)

| Chức năng | Pin nRF52833 | Ghi chú |
|-----------|-------------|---------|
| I2C0 SDA | Module pin 14 | Có pull-up ngoài R17 |
| I2C0 SCL | Module pin 15 | Có pull-up ngoài R19 |
| BNO INT | P0.04 | Interrupt |
| BNO RST | P0.22 | Reset |
| BNO BOOT | P0.05 | Bootloader mode |

> [!NOTE]
> Không bật pull-up nội trên I2C — bo đã có R17/R19 ngoài.
> IMU chưa được test trong firmware hiện tại.

### 3.7 SWD (Nạp code)

| Chức năng | Module Pin |
|-----------|-----------|
| SWCLK | Pin 2 |
| SWDIO | Pin 3 |

Nạp qua ST-Link V2 + OpenOCD. Dùng bo Debug v002 làm jig SWD.

---

## 4. Connector JP4 — USB / UART (chia sẻ)

JP4 là header 6 chân dùng chung cho USB và UART qua cầu điện trở 0402:

| Chế độ | Điện trở | Chân |
|--------|----------|------|
| USB | R20/R21 (hàn) | USB_DP / USB_DN |
| UART | R22/R23 (hàn) | TXD / RXD → UART0 |

> [!IMPORTANT]
> **Để dùng UART debug:** phải hàn R22/R23 và **bỏ** R20/R21.
> **Để dùng USB:** phải hàn R20/R21 và bỏ R22/R23.
> 
> **Lưu ý USB:** `CONFIG_USB_DEVICE_STACK=n` bắt buộc.
> Net `TP25 = VUSB` là net cụt (1 pad U7, không via) → không có VBUS
> → USBD không bao giờ enumerate, bật chỉ treo firmware.

---

## 5. Connector JP1 — Header 8 chân (Bo con)

Chuẩn header 1.27mm cho 3 loại bo con (dùng chung pinout):

```
Pin:  1     2     3      4     5     6     7     8
      3V3   GND   PWM6   PWM5  PWM4  PWM3  PWM2  PWM1
                   ↑
                   DW_GP3 — KHÔNG dùng firmware!
```

### Bo con đã test

| Bo con | Phiên bản | Trạng thái |
|--------|-----------|------------|
| **LED UV** | v003 (16.5 × 7 mm) | ✅ Đã test: UV trái=P0.30, UV phải=P0.02 |
| LED RGB | — | Chưa test |
| Debug | v002 | ✅ Dùng làm jig SWD |

---

## 6. Nguồn điện

| Thành phần | Chi tiết |
|------------|---------|
| Pin | LiPo (qua TP4056 charge IC) |
| LDO | Ổn áp ra 3V3 |
| Đo pin | ❌ **Không có** — không GPIO ADC nào nối cầu chia từ BAT+ |
| LED sạc | LED1 (đang sạc), LED2 (sạc đầy) — do TP4056 điều khiển |

---

## 7. Flash Layout (MCUboot OTA)

```
Địa chỉ       Kích thước   Nhãn          Mục đích
───────────────────────────────────────────────────
0x00000000     48 KB        mcuboot       Bootloader
0x0000C000     200 KB       image-0       App slot chính
0x0003E000     200 KB       image-1       OTA staging
0x00070000     40 KB        image-scratch Swap buffer
0x0007A000     24 KB        storage       NVS settings
───────────────────────────────────────────────────
               512 KB       TOTAL
```

---

## 8. Firmware Environments

| Env | File nguồn | Mục đích | RAM | Flash |
|-----|-----------|---------|-----|-------|
| `ranging` | `app_ranging.c` + driver/ + protocol/ | SS-TWR ranging (Anchor/Tag) | 5.7% | 5.2% |
| `ranging_ota` | + ota/ | Ranging + BLE OTA DFU | 41.1% | 26.2% |
| `hat_test` | `main_hat.c` | Hardware Acceptance Test | 5.9% | 5.8% |
| `uv_test` | `main_uv_test.c` | UV LED test (bo con v003) | 5.7% | 4.7% |

---

## 9. Bẫy & Lưu ý quan trọng

### ❌ Tuyệt đối KHÔNG

1. **Không khai báo PWM6** trong devicetree — DW_GP3 là GPIO của chip DW3000, chỉ điều khiển qua SPI
2. **Không bật `CONFIG_USB_DEVICE_STACK=y`** — VUSB là net cụt, firmware sẽ treo
3. **Không bật pull-up nội I2C** — bo có pull-up ngoài R17/R19
4. **Không nhầm SPI MISO = P0.06** — MISO thực tế là P0.29

### ⚠️ Cẩn thận

5. **UART TX/RX swap** so với label trên schematic — đã xác nhận P0.20=TX, P0.21=RX
6. **JP4 cần đúng resistor config** — R22/R23 cho UART, R20/R21 cho USB
7. **P0.06 chia sẻ** — dùng làm LED3 (active low), **KHÔNG** phải SPI MISO
8. **ST-Link không thể rescue APPROTECT** — cần J-Link dự phòng nếu vô tình lock

### ✅ Đã xác nhận hoạt động

9. SPI3 tới DW3110 @ 2MHz — đọc DEV_ID = `0xDECA0302` ✓
10. UART0 @ 115200 — log ra serial monitor ✓
11. MCUboot bootloader — build OK, boot app thành công ✓
12. BLE advertising — "DWM3001C" visible trên nRF Connect ✓
13. UV LED trái (P0.30) + phải (P0.02) — software PWM hoạt động ✓
14. LED3 (P0.06) — active low, nháy + toggle OK ✓

---

*File này là nguồn sự thật duy nhất cho phần cứng. Mọi firmware mới phải tham chiếu file này trước khi khai báo GPIO.*
