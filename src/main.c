#include "validation.h"
#include "uwb_app.h"
#include "uwb_network.h"
#include "uwb_reference.h"
#include "dstwr_tdma.h"
void main(void)
{
    int board_rc=board_init();
    if (console_init()) return;
    out("========================================");
    out("CUSTOM PCB HARDWARE TEST %s",FW_VERSION);
    out("========================================");
    board_info(); out("Board GPIO init rc=%d. Type help for commands.",board_rc);
    int rc=ota_init(); if (rc) out("OTA startup rc=%d",rc);
#ifdef VALIDATION_LED_BLINK
    uint32_t led_changed=k_uptime_get_32();
    bool led_on=false;
    bool blink_ok=board_rc==0;
#endif
    for (;;) {
        cli_service(); imu_service(); uwb_service(); network_service(); reference_service(); dstwr_service();
#ifdef VALIDATION_LED_BLINK
        uint32_t now=k_uptime_get_32();
        if (blink_ok && (uint32_t)(now-led_changed)>=100) {
            led_changed=now;
            led_on=((reference_running() && reference_anchor()) || (board_led_role_enabled() && uwb_is_anchor())) ? true : !led_on;
            int led_rc=board_led(led_on);
            if (led_rc) { blink_ok=false; out("LED heartbeat stopped rc=%d",led_rc); }
        }
#endif
        if(network_running() || dstwr_get_state()->sync_state) k_yield(); else k_msleep(1);
    }
}
