#include <Wire.h>
#include <Adafruit_BNO08x.h>
#include <bluefruit.h>

// --- ĐỊNH NGHĨA CHÂN VẬT LÝ ---
#define BNO_RST_PIN 22 // P0.22
#define BNO_INT_PIN 4  // P0.04
#define BOOT_PIN    5  // P0.05
#define LED_PIN     6  // P0.06
#define UART_TX     20 // P0.20
#define UART_RX     21 // P0.21
#define I2C_SDA     24 // P0.24
#define I2C_SCL     36 // P1.04

// Truyền -1 để tự quản lý Reset và Ngắt
Adafruit_BNO08x bno08x(-1);
sh2_SensorValue_t sensorValue;
BLEDfu bledfu;
BLEDis bledis;

void setup() {
  // 1. CẤU HÌNH LED VÀ BOOT BẰNG HÀM CHUẨN
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // Tắt LED
  pinMode(BOOT_PIN, OUTPUT);
  digitalWrite(BOOT_PIN, HIGH); // Ép BOOT lên HIGH chống sập BNO085

  // 2. CẤU HÌNH UART CHUẨN (DÙNG Serial1 ĐỂ XUẤT RA CHÂN TX/RX VẬT LÝ)
  Serial1.setPins(UART_RX, UART_TX);
  Serial1.begin(115200);
  delay(1000);
  Serial1.println("\n\n=======================================");
  Serial1.println(">> KHOI DONG BAN CODE SO 1 (OTA TEST) <<");

  // 3. KHỞI TẠO BLUETOOTH
  Bluefruit.begin();
  Bluefruit.setTxPower(4);
  Bluefruit.setName("CYBORG_OTA_TEST"); 
  bledis.setManufacturer("CYBORG_OTA_TEST"); bledis.begin();
  bledfu.begin();
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bledfu);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.start(0);
  Serial1.println(">> BLUETOOTH OK! Dang phat song: CYBORG_OTA_TEST");

  // 4. KHỞI TẠO I2C BẰNG HÀM CHUẨN CỦA ADAFRUIT
  Wire.setPins(I2C_SDA, I2C_SCL);
  Wire.begin();
  Wire.setClock(100000);

  // 5. RESET BNO085 THỦ CÔNG
  Serial1.println(">> Dang Reset BNO085...");
  pinMode(BNO_RST_PIN, OUTPUT);
  digitalWrite(BNO_RST_PIN, LOW);
  delay(50);
  digitalWrite(BNO_RST_PIN, HIGH);
  delay(200); // Chờ BNO085 thức dậy

  // 6. KẾT NỐI BNO085
  Serial1.println(">> Dang tim kiem BNO085 (I2C)...");
  if (!bno08x.begin_I2C(0x4A, &Wire, -1)) {
    Serial1.println("!!! LOI: Khong the ket noi BNO085 !!!");
    while (1) {
      // Nháy đèn cực nhanh báo lỗi I2C
      digitalWrite(LED_PIN, LOW); delay(100);
      digitalWrite(LED_PIN, HIGH); delay(100);
    }
  }
  Serial1.println(">> THANH CONG! Da nhan BNO085.");
  digitalWrite(LED_PIN, LOW); // Đèn sáng đứng báo OK
  
  // Cấu hình chân INT làm lối vào để đọc ngầm
  pinMode(BNO_INT_PIN, INPUT_PULLUP);
  if (!bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, 50000)) {
    Serial1.println("!!! LOI: Khong the kich hoat Report !!!");
  } else {
    Serial1.println(">> DANG DOC DU LIEU GOC QUAY...");
  }
}

void loop() {
  // Đọc chân INT: Chỉ vào lấy dữ liệu khi BNO085 kéo chân này xuống LOW (0V)
  if (digitalRead(BNO_INT_PIN) == LOW) {
    if (bno08x.getSensorEvent(&sensorValue)) {
      if (sensorValue.sensorId == SH2_GAME_ROTATION_VECTOR) {
        Serial1.print("I: "); Serial1.print(sensorValue.un.gameRotationVector.i, 3);
        Serial1.print(" | J: "); Serial1.print(sensorValue.un.gameRotationVector.j, 3);
        Serial1.print(" | K: "); Serial1.println(sensorValue.un.gameRotationVector.k, 3);
      }
    }
  }
  if (bno08x.wasReset()) {
    Serial1.println(">> BNO085 Reset. Dang cau hinh lai...");
    bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, 50000);
  }

  // nháy led
  digitalWrite(LED_PIN, LOW);
  delay(200);
  digitalWrite(LED_PIN, HIGH);
  delay(200);
}