#include "validation.h"
#include <device.h>
#include <drivers/gpio.h>
#include <hal/nrf_gpio.h>
static const struct device *gpio;
static uint32_t reset_reason;
static bool role_led=true;
void board_led_role_mode(bool enable) { role_led=enable; }
bool board_led_role_enabled(void) { return role_led; }
int board_init(void)
{
    reset_reason=NRF_POWER->RESETREAS;
    gpio=DEVICE_DT_GET(DT_NODELABEL(gpio0));
    if (!device_is_ready(gpio)) return -ENODEV;
    return gpio_pin_configure(gpio,BOARD_LED,GPIO_OUTPUT_HIGH);
}
int board_led(bool on) { return gpio ? gpio_pin_set_raw(gpio,BOARD_LED,!on) : -ENODEV; }
const char *status_name(int rc)
{
    if (!rc) return "PASS";
    if (rc==-EAGAIN || rc==-ETIMEDOUT) return "WAITING_FOR_PEER/NO_DATA";
    if (rc==-ENOTSUP) return "NOT_SUPPORTED";
    if (rc==-EACCES) return "CONFIG_REQUIRED";
    return "FAIL";
}
void board_info(void)
{
#ifdef VALIDATION_LED_BLINK
    out("LED3 P0.06 active low: mode=%s; blink 100ms on/off. LED2 is charger-controlled.",role_led?"solid anchor / blinking tag":"blink");
#endif
    out("%s | %s %s | custom PCB TEST","PCB_VERSION=" FW_VERSION,__DATE__,__TIME__);
    out("nRF52833 Cortex-M4F configured 64MHz | reset reason raw=0x%08x",reset_reason);
    out("FICR ID=%08x%08x | flash=%u KiB RAM=%u KiB",NRF_FICR->DEVICEID[1],NRF_FICR->DEVICEID[0],NRF_FICR->INFO.FLASH,NRF_FICR->INFO.RAM);
    out("Clock HFCLKSTAT=0x%08x LFCLKSTAT=0x%08x | watchdog running=%u",NRF_CLOCK->HFCLKSTAT,NRF_CLOCK->LFCLKSTAT,NRF_WDT->RUNSTATUS);
    out("UART0 P0.20 TX P0.21 RX 115200 8N1 no flow control; dropped logs=%u",console_drops());
    out("BNO08x I2C0 SDA=P0.24 SCL=P1.04 100kHz addr=0x4A INT=P0.04 RESET=P0.22 BOOT=P0.05");
    out("UWB SPI3 2MHz SCK=P0.03 MOSI=P0.08 MISO=P0.29 CS=P1.06 IRQ=P1.02 RESET=P0.25");
    out("Documented IMU=BNO085; requested BNO088 fitted identity requires verification; SWD unchanged");
}

