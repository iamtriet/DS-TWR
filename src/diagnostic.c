#include "validation.h"
#include "radio.h"
#include "uwb_app.h"
#include <drivers/uart.h>
#include <device.h>
#include <dfu/mcuboot.h>
#include <storage/flash_map.h>

void diagnostic(void)
{
    uwb_stop_all();
    out("========== BOARD DIAGNOSTIC ==========");
    board_info();
    bool failed=false;
    const struct device *uart=DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    bool uart_ready=device_is_ready(uart);
    out("UART peripheral ........ %s (host loopback not tested)",uart_ready?"PASS":"FAIL");
    if (!uart_ready) failed=true;
    out("Clock accuracy .......... SKIPPED (external measurement required)");
    int rc=board_led(true); if (!rc) { k_msleep(100); rc=board_led(false); }
    out("LED GPIO drive .......... %s rc=%d (visual result not inferred)",rc?"FAIL":"PASS",rc);
    if (rc) failed=true;
    rc=imu_init(); out("IMU product/config ...... %s rc=%d",rc?"FAIL":"PASS",rc);
    if (rc) failed=true;
    else {
        int64_t end=k_uptime_get()+2000;
        while (!imu_fresh() && k_uptime_get()<end) { imu_service(); k_msleep(1); }
        bool fresh=imu_fresh(); out("IMU required reports .... %s",fresh?"PASS":"FAIL (no fresh data)");
        if (!fresh) failed=true;
    }
    if (ota_connected()) out("UWB communication ....... SKIPPED (BLE connection active)");
    else {
        rc=uwb_init(); out("UWB communication/config  %s rc=%d",rc?"FAIL":"PASS",rc); if (rc) failed=true;
        if (!rc && radio_stats()->calibrated && uwb_configured()) {
            uint32_t irq_before=radio_irq_count();
            char *tx[]={"tx"}; rc=uwb_command(1,tx);
            out("UWB TX complete ......... %s rc=%d",rc?"FAIL":"PASS",rc); if (rc) failed=true;
            out("UWB IRQ edges ........... %s",radio_irq_count()!=irq_before?"OBSERVED":"NOT_OBSERVED");
        } else out("UWB TX .................. SKIPPED (ID/peer/calibration required)");
    }
    out("UWB RX/range/multitag ... WAITING_FOR_PEER (run explicit peer tests)");
    struct mcuboot_img_header header;
    rc=boot_read_bank_header(FLASH_AREA_ID(image_0),&header,sizeof(header));
    out("OTA primary metadata .... %s rc=%d",rc?"FAIL":"PASS",rc); if (rc) failed=true;
    out("OTA upload/rollback ..... SKIPPED (explicit A/B test required)");
    out("Overall: %s",failed?"FAIL":"PARTIAL");
}
