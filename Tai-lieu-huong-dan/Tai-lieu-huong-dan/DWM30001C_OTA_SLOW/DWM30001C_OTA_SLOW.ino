/* DWM3001C — OTA test firmware: SLOW blink 2000 ms
 * Trigger DFU: send 'r' via UART @115200 on P0.20(TX) P0.21(RX)
 */
#include <bluefruit.h>
#include <nrf_power.h>

BLEDfu bledfu;

#define LED_PIN         6      // P0.06
#define UART_TX         20     // P0.20
#define UART_RX         21     // P0.21
#define BLINK_MS        2000   // slow

#define DFU_MAGIC_OTA_APPLESS  0xA8

#define DBG(...) do { Serial1.printf(__VA_ARGS__); Serial1.flush(); } while (0)

static void enter_dfu_mode(void)
{
    DBG("[DFU] writing GPREGRET=0x%02X and resetting...\r\n", DFU_MAGIC_OTA_APPLESS);
    delay(100);
    sd_power_gpregret_set(0, DFU_MAGIC_OTA_APPLESS);
    delay(100);
    NVIC_SystemReset();
}

void connect_cb(uint16_t conn_handle)
{
    BLEConnection* c = Bluefruit.Connection(conn_handle);
    char name[32] = {0};
    c->getPeerName(name, sizeof(name));
    DBG("[BLE] connected handle=%u peer=%s\r\n", conn_handle, name);
}

void disconnect_cb(uint16_t conn_handle, uint8_t reason)
{
    DBG("[BLE] disconnected handle=%u reason=0x%02X\r\n", conn_handle, reason);
}

void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);

    Serial1.setPins(UART_RX, UART_TX);
    Serial1.begin(115200);
    delay(50);

    DBG("\r\n\r\n===== DWM3001C boot — SLOW blink %d ms =====\r\n", BLINK_MS);
    DBG("[setup] send 'r' via UART to enter DFU mode\r\n");

    if (!Bluefruit.begin()) {
        DBG("[setup] Bluefruit.begin FAILED\r\n");
        while (1) { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(50); }
    }

    Bluefruit.setTxPower(4);
    Bluefruit.setName("DWM3001C-SLOW");
    Bluefruit.Periph.setConnectCallback(connect_cb);
    Bluefruit.Periph.setDisconnectCallback(disconnect_cb);

    bledfu.begin();

    Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
    Bluefruit.Advertising.addTxPower();
    Bluefruit.Advertising.addService(bledfu);
    Bluefruit.Advertising.addName();
    Bluefruit.Advertising.restartOnDisconnect(true);
    Bluefruit.Advertising.setInterval(32, 244);
    Bluefruit.Advertising.setFastTimeout(30);
    Bluefruit.Advertising.start(0);

    DBG("[setup] advertising as DWM3001C-SLOW\r\n");
}

void loop()
{
    static uint32_t last = 0;

    if (Serial1.available()) {
        char c = Serial1.read();
        DBG("[uart] rx='%c' (0x%02X)\r\n", c, (unsigned)c);
        if (c == 'r' || c == 'R') {
            enter_dfu_mode();
        }
    }

    if (millis() - last >= BLINK_MS) {
        last = millis();
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
}
