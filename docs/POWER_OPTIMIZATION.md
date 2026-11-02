# Tiết kiệm năng lượng – swarm 1.2.10

Ưu tiên: giữ tốc độ điều khiển RGB/UV và dữ liệu multi-tag tracking.

## Thay đổi

- Tắt nháy LED3 chẩn đoán mặc định trong bản swarm. Không đổi GPIO, màu,
  độ sáng hay lệnh điều khiển RGB/UV. LED2 do phần cứng sạc điều khiển.
- Worker BNO085 thức theo ngắt INT thay vì thức định kỳ mỗi 2 ms khi không
  có dữ liệu. Nếu INT vẫn thấp (còn gói chưa đọc) hoặc đọc GPIO lỗi, tiếp tục
  xử lý mỗi 2 ms. Timeout 20 ms dự phòng ngắt bị bỏ lỡ; semaphore giữ ngắt
  đến trước khi worker ngủ. Giữ nguyên cả năm báo cáo cảm biến ở 20 Hz.
- Khi dừng swarm, tắt TIMER2 và ngắt compare, xóa sự kiện chờ. Khi bắt đầu
  lại, khởi tạo lại timer như trước. Không đụng timer/RTC của BLE/Zephyr.

## Giữ nguyên

- Lịch TDMA, timestamp, PHY, công suất phát, cách tính khoảng cách và vị trí.
- Vòng chờ UWB 100 us và lịch đánh thức trước delayed TX.
- BLE điều khiển 7.5–15 ms, latency 0, kết nối được giữ cho lệnh LED nhanh.
- Tần số BNO085, bộ lọc tracking và cảm biến dự phòng LIS2DH12.
- UART sniffer 1 Mbaud, OTA và lệnh chẩn đoán.

Không đưa UWB vào deep sleep khi tracking: việc đó cần thiết kế lại lịch
thu và đo kiểm mất gói/đồng bộ. Bản này chưa chứng minh mức giảm dòng tổng
hoặc thời lượng pin. Cần đo dòng tại nguồn board với cùng tải RGB/UV,
cùng số node, cùng lịch tracking và BLE trước/sau; đo riêng khi dừng tracking.
Kiểm tra thêm tuổi dữ liệu IMU, số gói mất, restart tracking nhiều lần,
ACK LED p50/p95 và sai số chuyển động thực. ACK BLE không phải thời điểm
ánh sáng thực tế thay đổi.

Build: `.platformio/penv/Scripts/platformio.exe run -e swarm` (dùng đường dẫn
PlatformIO trong thư mục người dùng). Ký: `python tools/sign_image.py sign
--env swarm --output-dir build/images/power-1.2.10`.

## Kết quả ngày 01/11/2026

- Build thành công: flash 193988/200192 byte, RAM 63701/131072 byte.
- OTA xác nhận thành công trên 09EC3021, 76A10829, 7A99EB74, 642D2C6D.
  Báo cáo: `build/batch-ota/20261031T180443401171Z/results.json` (UTC).
- 100 lượt RGB OFF tới nhóm bốn board, kết nối warm: 400/400 ACK;
  thời gian cả nhóm median 46.55 ms, p95 82.76 ms, max 127.77 ms.
  Dữ liệu: `tmp/rgb-speed-power-20261101.json`. Đo lúc chưa chạy UWB.
  Lượt cũ có median 40.67 ms / p95 64.83 ms; chưa phải phép so sánh A/B
  cùng điều kiện nên không kết luận độ trễ được cải thiện hoặc tương đương.
- Hai chu kỳ start/stop mạng bốn node: tất cả sync=1, tx_rc=0,
  missed=0, rejected=0, drops=0 tại thời điểm lấy mẫu. BNO085 cả bốn
  fresh=1, comm_errors=0, invalid=0, missed=0. Reset IMU mỗi lần start
  là do quy trình khởi tạo hiện có. Log: `tmp/power-smoke-20261101.json`.
  Các bộ đếm này không chứng minh không mất gói RF giữa mọi cặp.
- Kết thúc kiểm tra đã dừng swarm trên bốn node. Control Center sẽ cấu hình
  lại vai trò/ID khi Start Tracking; ID trong phép thử không phải roster UI.
- Chưa thấy COM9/sniffer nên chưa kiểm chứng lại độ chính xác vị trí hoặc
  độ trễ LED khi chạy cùng tracking đầy đủ. Chưa đo dòng tiêu thụ.
