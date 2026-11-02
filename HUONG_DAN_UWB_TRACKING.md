# Hướng dẫn UWB Tracking và điều khiển UV/RGB

## 1. Mở chương trình

Nhấp đúp **START_CONTROL_CENTER.cmd** trong thư mục dự án. Chỉ mở một Control Center tại một thời điểm.

Chương trình mở tab **UV / RGB Control**, quét BLE rồi dò các board nối UART. Đợi quét xong trước khi bấm Start.

## 2. Chuẩn bị phần cứng

- Cấp nguồn cho các board tham gia tracking.
- Nối board thu dữ liệu (sniffer/collector) với máy tính qua USB–UART. ST-Link chỉ nối SWD không cung cấp dữ liệu UART.
- Dùng ít nhất 4 board tổng cộng cho chế độ hiện tại. Đặt trên cùng mặt phẳng, giữ board collector cố định khi lấy nó làm gốc tọa độ.
- Đóng Serial Monitor hoặc ứng dụng khác đang giữ cổng COM của collector.

Các kết nối đã kiểm tra ngày 30/10/2026:

| Cổng | Board |
|---|---|
| COM9 | PCB_F42728EC |
| COM14 | PCB_09EC3021 |

Cổng và board có thể thay đổi sau khi cắm lại. Hãy dùng **Detect USB boards** để nhận diện lại, không cần nhớ ID.

## 3. Khởi chạy tracking

1. Bấm **Scan BLE** nếu danh sách chưa có các board đang bật. Đợi quét hoàn tất.
2. Chọn các board tham gia trong danh sách phía trên: giữ **Ctrl** để chọn rời rạc, hoặc **Shift** để chọn một dải. Chọn cả collector nếu nó có trong danh sách.
3. Bấm **Detect USB boards** nếu cần; chọn **COM9** cho bố trí đã kiểm tra ở trên. Nếu chỉ dò được một board UART, chương trình tự chọn cổng.
4. Bấm **Start UWB tracking** một lần. Chương trình đọc lại định danh qua UART, tìm board tương ứng trong BLE rồi cấu hình lần lượt các board. Không nhập ID thủ công.
5. Đợi cấu hình hoàn tất và các liên kết UWB xuất hiện. Việc này có thể mất vài chục giây do kết nối BLE ban đầu. Sau đó cần khoảng một giây có đủ vị trí để khởi tạo bộ lọc.
6. Khi bản đồ hiện các node, bấm **Fit map** nếu cần. Điểm **SNIFFER (0,0)** là gốc cố định; các node khác hiển thị tọa độ tương đối, đơn vị mét. Hướng bản đồ tương đối, không phải hướng Bắc địa lý.

Nút Start trong tab này dùng chế độ mặt bàn 2D và collector chủ động đo khoảng cách làm gốc. Đây không phải phép đo độ cao 3D. Không di chuyển collector trong phiên.

## 4. Điều khiển LED khi tracking đang chạy

- Bấm các node trên bản đồ để chọn một hoặc nhiều node; viền trắng đánh dấu lựa chọn. Bấm lại để bỏ chọn.
- Bấm **Prepare BLE** để kết nối sẵn với nhóm đã chọn.
- Dùng **UV left / UV right / UV both** cho bo UV.
- Với bo RGB, dùng **Red / Green / Blue**, hoặc chọn riêng R/G/B của LED 1 và LED 2 rồi bấm **Apply RGB**.
- **LED off** tắt các đầu ra của nhóm đang chọn. B1 là **P0.02** từ firmware 1.2.8 trở lên.
- UV và RGB dùng chung một số chân nên là hai chế độ thay thế nhau, không phải hai bộ đầu ra độc lập.
- Khi tracking chưa chạy, lựa chọn board cho LED lấy từ danh sách BLE. Khi tracking đã chạy, lựa chọn lấy từ các marker trên bản đồ.

## 5. Dừng và khởi động lại

Bấm **Stop tracking** trước khi đổi nhóm board, đổi COM hoặc cắm lại collector. Sau đó quét/dò lại và bấm Start. Đóng Control Center bằng nút X để ứng dụng giải phóng UART và BLE đúng cách.

## 6. Khi không thấy node

| Hiện tượng | Cách xử lý |
|---|---|
| Danh sách BLE trống/thiếu board | Kiểm tra nguồn, bấm Scan BLE; không khởi chạy với board đã tắt nguồn. |
| Không đọc được định danh trên COM | Kiểm tra TX/RX/GND, cáp USB–UART và đóng Serial Monitor đang giữ cổng. |
| Board UART không có trong BLE | Quét BLE lại trước khi Start. |
| Đang chờ đủ vị trí | Xem dòng trạng thái; giữ các board cùng mặt phẳng, đủ nguồn và trong tầm đo. |
| Có liên kết nhưng chưa có tọa độ | Cần đủ hình học đo để tính vị trí; tránh xếp tất cả board thẳng hàng. |
| Bấm Start khi phiên đã chạy | Bấm Stop tracking trước, sau đó Start lại. |

**Lỗi đã sửa:** trước đây phần hiển thị chờ đủ 30 node mặc định dù nhóm chỉ có vài board. Phiên bản này lấy số node thực từ nhóm đã cấu hình; không cần chỉnh số node mô phỏng thành 4 hay 5.

Để xem chi tiết cấu hình hoặc lỗi, mở **UWB 3D Tracking** và **Console & Logs**. Kết nối LED thành công không tự chứng minh UWB đã có vị trí.

## Cập nhật phản hồi chuyển động (31/10/2026)

- Tab UV/RGB vẽ theo nhịp mục tiêu 25 Hz (trước đây 5 Hz), giữ tối đa 500 điểm quỹ đạo.
- Không vẽ bảng Tracking ẩn để giảm tải giao diện.
- Trong chế độ cooperative, khóa Quiet cần cả IMU và quyết định đứng yên từ khoảng cách UWB. IMU Quiet đơn lẻ không đủ để khóa một node đang trượt/chuyển động đều.
- Phát hiện chuyển động bằng khoảng cách dùng thêm cửa sổ ngắn 0,24 giây, cần ít nhất hai liên kết đồng thuận và vẫn kiểm tra hình học ổn định; bỏ điều kiện chờ toàn nhóm hết chuyển động 2 giây.
- Giữ nguyên giới hạn loại bước nhảy 0,6 m/s. Nếu di chuyển tay nhanh hơn giới hạn này, mẫu vẫn bị giữ; không tự nới giới hạn để làm đường vẽ trông mượt hơn.
- Dòng trạng thái báo nhịp tính vị trí, tuổi khoảng cách và số node bị giữ/outlier. 25 Hz là mục tiêu, không phải cam kết độ trễ hay độ chính xác tuyệt đối.

Kiểm tra phần mềm: đường tròn với IMU Quiet nhưng UWB xác nhận chuyển động giữ được độ cong; nhóm đứng yên vẫn khóa, bước nhảy vẫn bị loại. Kiểm tra thực tế ban đầu nhận đủ 4 node/6 liên kết. Độ chính xác đường tròn thực tế cần đối chiếu thao tác của người dùng hoặc quỹ đạo chuẩn.

### Giữ các node đứng yên khi một node chuyển động

Sửa ngày 31/10/2026: khi đã có collector cố định làm gốc, mỗi node được
UWB/IMU xác nhận đứng yên tiếp tục được bảo vệ riêng. Một node chuyển động
hoặc mất IMU không còn làm toàn bộ nhóm tham chiếu mất trạng thái đứng yên.
Collector được đưa vào nhóm tham chiếu theo cấu hình cố định, không phụ thuộc
vào cờ Quiet của IMU collector. Các node di động vẫn được giải phóng khi có
bằng chứng chuyển động; không cố định cứng ID 0001/0003.

Kiểm tra hồi quy: một node chạy cung tròn, collector thiếu IMU, các node còn lại
không bị kéo theo trong mô phỏng. Đã khởi chạy lại thực tế với 4 node/6 liên kết;
đang đối chiếu lượt di chuyển node 0002 của người dùng. Không xem kết quả mô
phỏng là chứng nhận sai số thực tế.

Bổ sung xác nhận chuyển động: trước khi mở khóa vì thay đổi khoảng cách, xu
hướng ở cửa sổ ngắn 0,24 s và cửa sổ dài 0,6 s phải cùng chiều trên ít nhất
hai liên kết. Một xung IMU ở node đã đứng yên có thể bị xem là rung nếu hai
liên kết UWB độc lập, không gần song song, vẫn ổn định. Khi hình học gần thẳng
hàng hoặc mất dữ liệu, hệ thống không giả vờ có đủ bằng chứng để cố định node.
Bộ kiểm tra cooperative hiện có 9 tình huống, bao gồm trường hợp rung IMU ở
một node đứng yên trong khi node khác chạy cung tròn. Cần thử lại trên mạch
sau khi nối lại COM9 để xác nhận hiệu quả thực tế.
# Nạp firmware cho board vừa bật nguồn

## Tự phát hiện khi đang tracking (02/11/2026)

Control Center quét BLE nền 3 giây mỗi 10 giây khi swarm đã cấu hình xong.
Board mới có firmware swarm được kiểm tra khả năng trước khi thêm vào nhóm.
Chương trình báo thay đổi, dừng và cấu hình lại TDMA rồi tiếp tục tracking;
quá trình này cần vài chục giây, không phải thêm node tức thì không gián đoạn.
Các node đang có dữ liệu UWB vẫn được giữ dù không quảng bá BLE khi kết nối.
Khi thêm board, thành viên cũ không có UWB trong 10 giây và không được quét
thấy sẽ được bỏ khỏi danh sách phiên mới. Danh sách và ID được cập nhật,
trajectory/selection cũ được xóa để tránh nhầm ID sau cấu hình lại.
Board chưa có firmware swarm cần cập nhật trong Boards & OTA trước.

Sửa phản hồi chuyển động: giới hạn bước hiệu chỉnh phù hợp bộ lọc hiển thị
0,6 m/s; khôi phục độ bất định vận tốc khi có bằng chứng rời trạng thái nghỉ.
Độ bất định được tính trong hệ tọa độ tương đối, loại bậc tự do hướng quay
không quan sát được. Đây không phải chứng nhận sai số tuyệt đối.

1. Dừng tracking, vào **Boards & OTA**, bấm **Scan BLE boards**.
2. Chọn đúng board mới; Ctrl-click nếu muốn nạp nhiều board.
3. **Choose signed .bin**: chọn bản `swarm_v1.2.10_2026-11-02_11-29-58_UTC+07.signed.bin`
   trong `build/images/power-1.2.10`. Control Center chọn sẵn bản swarm có
   tên ngày giờ được tạo gần nhất khi mở chương trình.
4. Bấm **Update selected boards**, xác nhận và giữ nguồn cho tới khi hoàn tất
   kiểm tra, khởi động lại và xác nhận ảnh firmware. `ALREADY_CURRENT` nghĩa
   là board đã chạy đúng hash firmware và đã xác nhận, không cần ghi flash lại.
5. Quét lại, chọn tất cả board đang dùng cùng sniffer. Sang **UV / RGB Control**,
   chọn COM của sniffer và bấm **Start UWB tracking** để cấu hình lại số node.

Board chưa từng có MCUboot/BLE OTA cần nạp lần đầu qua **New board / ST-Link**.
Tên file chứa giờ Việt Nam UTC+07 và là thời điểm ký/đóng gói, không phải giờ
biên dịch. Phiên bản firmware vẫn là 1.2.10; đổi tên không thay đổi thuật toán.

