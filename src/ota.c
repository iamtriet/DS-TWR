/* Reuses the project's BLE mcumgr + MCUboot architecture and partition map.
 * No automatic confirmation: an unconfirmed trial must revert after reset. */
#include "validation.h"
#include "uwb_app.h"
#include "radio.h"
#include <mgmt/mgmt.h>
#include <bluetooth/bluetooth.h>
#include <bluetooth/conn.h>
#include <mgmt/mcumgr/smp_bt.h>
#include <img_mgmt/img_mgmt.h>
#include <os_mgmt/os_mgmt.h>
#include <dfu/mcuboot.h>
#include <storage/flash_map.h>
#include <sys/reboot.h>
#include <sys/atomic.h>
#include <hal/nrf_gpio.h>
#include <sys/printk.h>
#include <string.h>

static atomic_t connected,adv_error,upload_locked;
static bool ready;
static char name[20];
static struct k_work advertise_work;
static const struct bt_data advertising[]={
    BT_DATA_BYTES(BT_DATA_FLAGS,BT_LE_AD_GENERAL|BT_LE_AD_NO_BREDR),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL,0x84,0xaa,0x60,0x74,0x52,0x8a,0x8b,0x86,0xd3,0x4c,0xb7,0x1d,0x1d,0xdc,0x53,0x8d)
};
bool ota_connected(void) {
#ifdef SWARM_RANGING
    /* Discovery/status connections must not stop the TDMA network. */
    return atomic_get(&upload_locked)!=0;
#else
    return atomic_get(&connected)!=0 && !control_enabled();
#endif
}
bool ota_upload_locked(void) { return atomic_get(&upload_locked)!=0; }
static void advertise(struct k_work *work)
{
    ARG_UNUSED(work);
    int rc=bt_le_adv_start(BT_LE_ADV_CONN_NAME,advertising,ARRAY_SIZE(advertising),NULL,0);
    atomic_set(&adv_error,rc); out("[OTA] BLE advertising %s rc=%d",name,rc);
}
static void on_connected(struct bt_conn *conn,uint8_t err)
{
    ARG_UNUSED(conn);
    if (!err) { control_disconnect(); atomic_clear(&upload_locked); atomic_set(&connected,1); }
    out("[OTA] BLE connection err=%u",err);
}
static void on_disconnected(struct bt_conn *conn,uint8_t reason)
{
    ARG_UNUSED(conn); control_disconnect(); atomic_clear(&upload_locked); atomic_set(&connected,0);
    out("[OTA] BLE disconnected reason=%u",reason);
    k_work_submit(&advertise_work);
}
BT_CONN_CB_DEFINE(ota_callbacks)={.connected=on_connected,.disconnected=on_disconnected};
static void dfu_started(void) { out("[OTA] upload started"); }
static void dfu_stopped(void) { out("[OTA] upload failed/stopped; inspect host error"); }
static void dfu_pending(void) { out("[OTA] upload complete; request test explicitly, then reboot"); }
static void dfu_confirmed(void) { out("[OTA] image confirmed by management request"); }
static const img_mgmt_dfu_callbacks_t dfu_callbacks={dfu_started,dfu_stopped,dfu_pending,dfu_confirmed};
static int upload_guard(uint32_t offset,uint32_t size,void *arg)
{
    ARG_UNUSED(arg);
    /* Lock control out before any flash write; the main loop stops ranging. */
    atomic_set(&upload_locked,1); control_disconnect();
#ifdef SWARM_RANGING
    /* Yield the management workqueue until the application cancels TX/RX.
     * Never write flash while a radio operation still owns the SPI buffers. */
    for (unsigned wait=0;wait<100 && (uwb_running() || radio_busy());wait++)
        k_msleep(1);
#endif
    if (uwb_running()) return MGMT_ERR_EUNKNOWN;
    if (radio_busy()) { out("[OTA] upload deferred: radio operation finishing; retry"); return MGMT_ERR_EUNKNOWN; }
    static uint32_t last_bucket=UINT32_MAX;
    uint32_t bucket=size ? (uint32_t)((uint64_t)offset*10/size) : 0;
    if (!offset || bucket!=last_bucket) out("[OTA] upload accepted offset=%u total=%u",offset,size);
    last_bucket=bucket; return 0;
}
int ota_init(void)
{
    int rc=bt_enable(NULL); if (rc) return rc;
    snprintk(name,sizeof(name),"PCB_%08X",NRF_FICR->DEVICEID[0]);
    rc=bt_set_name(name); if (rc) return rc;
    img_mgmt_register_group(); os_mgmt_register_group();
    img_mgmt_set_upload_cb(upload_guard,NULL);
    img_mgmt_register_callbacks(&dfu_callbacks);
    rc=smp_bt_register(); if (rc) return rc;
    k_work_init(&advertise_work,advertise);
    ready=true; advertise(NULL); return (int)atomic_get(&adv_error);
}
static int bank(uint8_t id,const char *label)
{
    const struct flash_area *area;
    int rc=flash_area_open(id,&area); if (rc) { out("OTA %s open rc=%d",label,rc); return rc; }
    out("OTA %s offset=0x%08x size=0x%08x",label,(unsigned)area->fa_off,(unsigned)area->fa_size);
    struct mcuboot_img_header header;
    rc=boot_read_bank_header(id,&header,sizeof(header));
    if (!rc && header.mcuboot_version==1) {
        const struct mcuboot_img_sem_ver *v=&header.h.v1.sem_ver;
        out("OTA %s metadata version=%u.%u.%u+%u body_bytes=%u",label,v->major,v->minor,v->revision,v->build_num,header.h.v1.image_size);
        if (header.h.v1.image_size>area->fa_size-0x200 || !header.h.v1.image_size) rc=-EMSGSIZE;
    } else if (!rc) rc=-ENOTSUP;
    flash_area_close(area); return rc;
}
void ota_status(void)
{
    out("OTA expected boot chain=MCUboot + BLE SMP ready=%u name=%s connected=%u adv_error=%d",ready,name,ota_connected(),(int)atomic_get(&adv_error));
    out("OTA running FW=%s confirmed=%u swap_type=%d; no automatic confirmation",FW_VERSION,boot_is_img_confirmed(),mcuboot_swap_type());
    int primary=bank(FLASH_AREA_ID(image_0),"primary"),secondary=bank(FLASH_AREA_ID(image_1),"secondary");
    out("OTA metadata primary_rc=%d secondary_rc=%d (empty secondary is normal)",primary,secondary);
    out("OTA header metadata is not signature validation; MCUboot validates signature at boot. Installed bootloader identity requires SWD/provisioning evidence.");
}
/* mcumgr and these UART jobs run on the same system workqueue, so flash
 * mutations cannot overlap even if a BLE connection arrives after dispatch. */
static void flash_job(struct k_work *work);
K_WORK_DEFINE(flash_work,flash_job);
K_SEM_DEFINE(flash_done,0,1);
static atomic_t flash_pending;
static int flash_action,flash_result;
static void flash_job(struct k_work *work)
{
    ARG_UNUSED(work);
    int rc=-EBUSY;
    if (!atomic_get(&connected) && !radio_busy()) {
        if (flash_action==1) {
            rc=bank(FLASH_AREA_ID(image_1),"secondary");
            if (!rc) rc=boot_request_upgrade(BOOT_UPGRADE_TEST);
        } else rc=boot_write_img_confirmed();
    }
    flash_result=rc;
    out("OTA %s rc=%d",flash_action==1?"request trial (signature checked at boot)":"confirm current image",rc);
    k_sem_give(&flash_done);
}
int ota_command(int argc,char **argv)
{
    if (argc!=1) return -EINVAL;
    if (!strcmp(argv[0],"status")) { ota_status(); return 0; }
    if (atomic_get(&connected)) return -EBUSY;
    if (!strcmp(argv[0],"reboot")) { uwb_stop_all(); out("OTA reboot; unconfirmed trial may roll back"); k_msleep(100); sys_reboot(SYS_REBOOT_COLD); return 0; }
    int action=!strcmp(argv[0],"test")?1:!strcmp(argv[0],"confirm")?2:0;
    if (!action) return -EINVAL;
    /* A timed-out job keeps ownership until it completes; never reuse its args. */
    if (atomic_get(&flash_pending)) {
        if (k_sem_take(&flash_done,K_NO_WAIT)) return -EBUSY;
        atomic_clear(&flash_pending);
    }
    uwb_stop_all(); flash_action=action; atomic_set(&flash_pending,1);
    k_work_submit(&flash_work);
    if (k_sem_take(&flash_done,K_SECONDS(5))) return -ETIMEDOUT;
    atomic_clear(&flash_pending); return flash_result;
}
