#include "validation.h"
#include "uwb_network.h"
#include "uwb_reference.h"
#include <device.h>
#include <drivers/uart.h>
#include <stdarg.h>
#include <sys/printk.h>
#include <string.h>
#include <sys/atomic.h>
#include <sys/reboot.h>

#define LINE_SIZE 224
typedef struct { uint16_t length; bool binary; uint8_t data[LINE_SIZE]; } log_packet_t;
/* A 30-node sparse superframe can burst 110 range records faster than UART
 * drains them. Keep a bounded 64-record queue; drops remain observable. */
K_MSGQ_DEFINE(logq, sizeof(log_packet_t), 64, 4);
K_MSGQ_DEFINE(rxq, 1, 256, 1);
K_MSGQ_DEFINE(remoteq, 160, 1, 4);
void console_remote_clear(void) { k_msgq_purge(&remoteq); }
int console_remote(const void *data, size_t len)
{
    char command[160]={0};
    if (!len || len>=sizeof(command)) return -EINVAL;
    const uint8_t *bytes=data;
    for (size_t i=0;i<len;++i) if (bytes[i]<32 || bytes[i]>126) return -EINVAL;
    memcpy(command,data,len);
    return k_msgq_put(&remoteq,command,K_NO_WAIT);
}
static const struct device *uart;
static atomic_t drops, rx_overflow;
static K_THREAD_STACK_DEFINE(log_stack, 1536);
static struct k_thread log_thread;
static void writer(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    log_packet_t packet;
    for (;;) {
        k_msgq_get(&logq, &packet, K_FOREVER);
        for (size_t i=0; i<packet.length; ++i) uart_poll_out(uart, packet.data[i]);
        if (!packet.binary) {
            uart_poll_out(uart, '\r'); uart_poll_out(uart, '\n');
            control_notify((const char *)packet.data);
        }
    }
}
void out(const char *fmt, ...)
{
    log_packet_t packet={0};
    char *line=(char *)packet.data;
    va_list ap; va_start(ap,fmt);
    int n=vsnprintk(line,LINE_SIZE,fmt,ap); va_end(ap);
    if (n<0 || n>=LINE_SIZE) atomic_inc(&drops);
    packet.length=strlen(line);
    if (k_msgq_put(&logq,&packet,K_NO_WAIT)) atomic_inc(&drops);
}
int console_binary(const uint8_t *data,size_t length)
{
    if (!data || !length || length>LINE_SIZE) return -EINVAL;
    log_packet_t packet={.length=length,.binary=true};memcpy(packet.data,data,length);
    int rc=k_msgq_put(&logq,&packet,K_NO_WAIT);if(rc) atomic_inc(&drops);return rc;
}
uint32_t console_drops(void) { return (uint32_t)atomic_get(&drops); }
static void rx_irq(const struct device *dev, void *context)
{
    ARG_UNUSED(context);
    while (uart_irq_update(dev) && uart_irq_rx_ready(dev)) {
        uint8_t byte;
        if (uart_fifo_read(dev,&byte,1)!=1) break;
        if (k_msgq_put(&rxq,&byte,K_NO_WAIT)) atomic_set(&rx_overflow,1);
    }
}
int console_init(void)
{
    uart=DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    if (!device_is_ready(uart)) return -ENODEV;
    uart_irq_callback_user_data_set(uart,rx_irq,NULL);
    uart_irq_rx_enable(uart);
    k_thread_create(&log_thread,log_stack,K_THREAD_STACK_SIZEOF(log_stack),
                    writer,NULL,NULL,NULL,10,0,K_NO_WAIT);
    return 0;
}
static int dispatch(char *line)
{
    char *args[16]; int count=0;
    for (char *p=strtok(line," \t"); p; p=strtok(NULL," \t")) {
        if (count==ARRAY_SIZE(args)) { out("ERROR too many arguments"); return -EINVAL; }
        args[count++]=p;
    }
    if (!count) return -EINVAL;
    int rc=-EINVAL;
    if (!strcmp(args[0],"help") && count==1) {
        out("help | info | diag | led on/off | reset");
        out("led mode blink/role (role: solid LED3 for anchor, blinking for tags; LED2 is charger-controlled)");
        out("imu init/status/read/stream/stop/reset");
        out("uwb init/status/tx/rx/range/continuous/stop | uwb role anchor/tag");
        out("uwb id HEX | uwb peer HEX | uwb calibration TX_DELAY RX_DELAY TX_POWER_HEX PG_DELAY_HEX");
        out("uwb anchors clear | uwb anchors HEX [HEX ...] (peer allowlist, max 12)");
        out("uwb cooperate (host-scheduled peer ranging; keeps anchor/tag LED role)");
        out("uwb capabilities | uwb output binary (U3D UART ranges; BP TDMA not qualified)");
        out("net configure ID COUNT SLOT_MS PERIOD_MS NET_HEX | net neighbors ID [ID ...] | net start experimental | net status/stop");
        out("multitag add HEX | clear/start/stop/status/stats | uwb output text/csv");
        out("ota status/test/confirm/reboot (test requests a trial of the uploaded image)");
        return 0;
    }
    if (!strcmp(args[0],"info") && count==1) { board_info(); imu_status(); uwb_status(); ota_status(); return 0; }
    if (!strcmp(args[0],"diag") && count==1) { diagnostic(); return 0; }
    if (!strcmp(args[0],"reset") && count==1) { out("Rebooting"); k_msleep(100); sys_reboot(SYS_REBOOT_COLD); return 0; }
    if (!strcmp(args[0],"led") && count==2) {
        if (!strcmp(args[1],"on")) rc=board_led(true);
        else if (!strcmp(args[1],"off")) rc=board_led(false);
    }
    if (!strcmp(args[0],"led") && count==3 && !strcmp(args[1],"mode")) {
        if (!strcmp(args[2],"role")) { board_led_role_mode(true); rc=0; }
        else if (!strcmp(args[2],"blink")) { board_led_role_mode(false); rc=0; }
    }
    if (!strcmp(args[0],"imu") && count==2) {
        if (!strcmp(args[1],"init") || !strcmp(args[1],"reset")) rc=imu_init();
        else if (!strcmp(args[1],"status")) { imu_status(); return 0; }
        else if (!strcmp(args[1],"read")) rc=imu_read();
        else if (!strcmp(args[1],"stream")) { imu_stream(true); return 0; }
        else if (!strcmp(args[1],"stop")) { imu_stream(false); return 0; }
    }
    if (!strcmp(args[0],"uwb")) rc=uwb_command(count-1,args+1);
    if (!strcmp(args[0],"net")) rc=network_command(count-1,args+1);
    if (!strcmp(args[0],"ref")) rc=reference_command(count-1,args+1);
    if (!strcmp(args[0],"multitag")) rc=multitag_command(count-1,args+1);
    if (!strcmp(args[0],"ota")) rc=ota_command(count-1,args+1);
    out("[%s] %s rc=%d",args[0],status_name(rc),rc);
    return rc;
}
void cli_service(void)
{
    char remote[160];
    if (!k_msgq_get(&remoteq,remote,K_NO_WAIT)) {
        if (control_enabled()) {
            /* Flash/reboot management belongs to the signed OTA transport. */
            int rc=(!strncmp(remote,"ota ",4) || !strcmp(remote,"reset")) ? -EACCES : dispatch(remote);
            out("@DONE %d",rc);
        }
        return;
    }
    static char line[160]; static size_t used; static bool discard;
    if (atomic_set(&rx_overflow,0)) { discard=true; used=0; out("CLI RX overflow: discarding through newline"); }
    uint8_t c;
    /* One command per service call; radio/IMU get time between commands. */
    for (int i=0;i<256 && !k_msgq_get(&rxq,&c,K_NO_WAIT);++i) {
        if (c=='\r' || c=='\n') {
            if (!discard && used) { line[used]=0; dispatch(line); }
            used=0; discard=false; return;
        }
        if (discard) continue;
        if (c==8 || c==127) { if (used) --used; continue; }
        if (c<32 || c>126) continue;
        if (used==sizeof(line)-1) { discard=true; used=0; out("CLI line too long"); }
        else line[used++]=(char)c;
    }
}
