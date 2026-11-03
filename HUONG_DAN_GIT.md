# Hướng Dẫn Cập Nhật GitHub Repository

> Dự án: **DS-TWR UWB Swarm Firmware & Control System**
> Repository: https://github.com/iamtriet/DS-TWR

---

## Mục Lục

1. [Thiết lập lần đầu](#1-thiết-lập-lần-đầu)
2. [Quy trình cập nhật hàng ngày](#2-quy-trình-cập-nhật-hàng-ngày)
3. [Các lệnh Git thường dùng](#3-các-lệnh-git-thường-dùng)
4. [Quy tắc viết commit message](#4-quy-tắc-viết-commit-message)
5. [Làm việc với nhánh (Branch)](#5-làm-việc-với-nhánh-branch)
6. [Xử lý xung đột (Conflict)](#6-xử-lý-xung-đột-conflict)
7. [Khôi phục lỗi](#7-khôi-phục-lỗi)
8. [Cấu trúc dự án](#8-cấu-trúc-dự-án)

---

## 1. Thiết Lập Lần Đầu

### 1.1 Cài đặt Git
Tải Git tại: https://git-scm.com/download/win

### 1.2 Cấu hình tên và email
```bash
git config --global user.name "Tên của bạn"
git config --global user.email "email@example.com"
```

### 1.3 Clone repository về máy
```bash
git clone https://github.com/iamtriet/DS-TWR.git
cd DS-TWR
```

### 1.4 Kiểm tra kết nối
```bash
git remote -v
# Output mong đợi:
# origin  https://github.com/iamtriet/DS-TWR.git (fetch)
# origin  https://github.com/iamtriet/DS-TWR.git (push)
```

---

## 2. Quy Trình Cập Nhật Hàng Ngày

### Bước 1 — Kiểm tra trạng thái
```bash
git status
```

### Bước 2 — Kéo code mới nhất từ server
```bash
git pull origin main
```

### Bước 3 — Stage các thay đổi
```bash
git add -A
```

### Bước 4 — Commit với message có nghĩa
```bash
git commit -m "feat: mô tả ngắn gọn thay đổi"
```

### Bước 5 — Push lên GitHub
```bash
git push origin main
```

### Sơ đồ tóm tắt
```
[Làm việc] → git add -A → git commit -m "..." → git push origin main → [GitHub]
```

---

## 3. Các Lệnh Git Thường Dùng

### Xem trạng thái
```bash
git status                    # Xem file nào thay đổi
git diff                      # Xem chi tiết nội dung thay đổi
git diff --stat               # Xem tóm tắt số dòng thay đổi
git log --oneline -10         # Xem 10 commit gần nhất
git log --oneline --graph     # Xem lịch sử dạng đồ thị
```

### Stage & Unstage
```bash
git add -A                    # Stage tất cả thay đổi
git add .                     # Stage file trong thư mục hiện tại
git add <file>                # Stage 1 file cụ thể
git restore --staged <file>   # Unstage 1 file
git restore <file>            # Hoàn tác thay đổi trong file (cẩn thận!)
```

### Commit
```bash
git commit -m "message"       # Commit với message
git commit --amend            # Sửa commit message của commit cuối
```

### Push & Pull
```bash
git push origin main          # Push lên nhánh main
git pull origin main          # Pull từ nhánh main
git fetch origin              # Fetch về nhưng chưa merge
```

### Stash - lưu tạm thay đổi
```bash
git stash                     # Lưu tạm thay đổi hiện tại
git stash list                # Xem danh sách stash
git stash pop                 # Lấy lại thay đổi gần nhất
git stash drop                # Xóa stash gần nhất
```

---

## 4. Quy Tắc Viết Commit Message

Dự án này theo chuẩn **Conventional Commits**:

```
<type>: <mô tả ngắn gọn bằng tiếng Anh>
```

| Type       | Ý nghĩa                                      | Ví dụ |
|------------|----------------------------------------------|-------|
| `feat`     | Thêm tính năng mới                           | `feat: add UART discovery tool` |
| `fix`      | Sửa lỗi                                      | `fix: correct CRC calculation in uwb_app.c` |
| `docs`     | Cập nhật tài liệu                            | `docs: update hardware wiring diagram` |
| `refactor` | Tái cấu trúc code                            | `refactor: simplify swarm timer logic` |
| `test`     | Thêm hoặc sửa test                           | `test: add swarm membership test cases` |
| `chore`    | Thay đổi build, config                       | `chore: update platformio.ini board list` |
| `perf`     | Tối ưu hiệu năng                             | `perf: reduce IMU polling interval` |

### Ví dụ tốt
```bash
git commit -m "feat: add swarm cooperative tracker with 30-node support"
git commit -m "fix: resolve TDMA slot collision in dense swarm"
git commit -m "docs: add power optimization guide for field deployment"
```

### Ví dụ xấu (tránh)
```bash
git commit -m "update"        # Quá mơ hồ
git commit -m "fix bug"       # Không rõ bug nào
git commit -m "asdfgh"        # Vô nghĩa
```

---

## 5. Làm Việc Với Nhánh (Branch)

### Tạo nhánh mới
```bash
git checkout -b feature/ten-tinh-nang
# Ví dụ:
git checkout -b feature/uwb-3d-visualization
git checkout -b fix/led-blink-issue
```

### Xem danh sách nhánh
```bash
git branch              # Nhánh local
git branch -r           # Nhánh remote
git branch -a           # Tất cả nhánh
```

### Merge nhánh vào main
```bash
git checkout main
git pull origin main
git merge feature/ten-tinh-nang
git push origin main
```

### Xóa nhánh sau khi merge
```bash
git branch -d feature/ten-tinh-nang
git push origin --delete feature/ten-tinh-nang
```

---

## 6. Xử Lý Xung Đột (Conflict)

Conflict xảy ra khi 2 người cùng sửa 1 đoạn code.

### Nhận biết conflict
```
CONFLICT (content): Merge conflict in src/uwb_app.c
```

### Mở file conflict, tìm marker
```
<<<<<<< HEAD
// Code của bạn
int timeout = 500;
=======
// Code từ remote
int timeout = 1000;
>>>>>>> origin/main
```

### Giải quyết
1. Xóa các marker (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Giữ lại code đúng
3. Save file, rồi:

```bash
git add src/uwb_app.c
git commit
```

---

## 7. Khôi Phục Lỗi

### Hoàn tác thay đổi chưa commit
```bash
git restore src/uwb_app.c    # Hoàn tác 1 file
git restore .                # Hoàn tác tất cả file
```

### Hoàn tác commit cuối (giữ thay đổi)
```bash
git reset --soft HEAD~1      # Uncommit, giữ code trong staging
git reset HEAD~1             # Uncommit, bỏ staging nhưng giữ file
```

### Hoàn tác commit cuối (XÓA thay đổi - rất nguy hiểm!)
```bash
git reset --hard HEAD~1      # XÓA hoàn toàn commit và thay đổi
```

### Lấy lại file từ commit cụ thể
```bash
git checkout <commit-hash> -- src/uwb_app.c
```

---

## 8. Cấu Trúc Dự Án

```
purpleC_2/
├── src/                    # Firmware C source files (Zephyr RTOS)
│   ├── main.c              # Entry point
│   ├── uwb_app.c           # UWB ranging application
│   ├── uwb_swarm.c         # Swarm networking
│   ├── control.c           # Motor/actuator control
│   ├── imu.c               # IMU sensor interface
│   ├── aux_led.c           # LED control
│   ├── console.c           # UART console
│   └── swarm_timer.c       # TDMA timing
├── include/                # Header files
│   ├── dstwr_tdma.h        # DS-TWR TDMA protocol
│   └── uwb_swarm.h         # Swarm types & API
├── tools/                  # Python desktop tools
│   ├── control_app.py      # Main control application
│   ├── control_backend.py  # Backend server
│   ├── swarm_gateway.py    # Swarm network gateway
│   ├── swarm_cooperative_tracker.py
│   ├── led_tracking_view.py
│   ├── uwb3d_view.py       # 3D position viewer
│   ├── position_gate.py    # Virtual geofencing
│   ├── swarm_membership.py # Node membership manager
│   └── uart_discovery.py   # Auto UART port discovery
├── tests/                  # Python test suite
├── docs/                   # Documentation
├── boards/                 # Board configuration files
├── platformio.ini          # PlatformIO build config
├── README.md               # Tài liệu chính
├── HARDWARE.md             # Sơ đồ phần cứng
├── HUONG_DAN_UWB_TRACKING.md  # Hướng dẫn sử dụng
└── HUONG_DAN_GIT.md        # File này - hướng dẫn cập nhật GitHub
```

---

## Tips Nhanh

```bash
# Xem tóm tắt thay đổi trước khi commit
git diff --stat

# Commit tất cả file đã track (không cần git add)
git commit -am "fix: quick message"

# Push và set upstream cùng lúc (lần đầu với nhánh mới)
git push -u origin feature/my-branch

# Xem commit của 1 người cụ thể
git log --author="iamtriet" --oneline

# Tìm kiếm trong lịch sử commit
git log --grep="LED" --oneline
```

---

*Cập nhật lần cuối: 2026-11-02 | Repository: [iamtriet/DS-TWR](https://github.com/iamtriet/DS-TWR)*
