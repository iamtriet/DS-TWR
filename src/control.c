/* Bench command/telemetry transport; commands execute on the main thread. */
#include "validation.h"
#include <bluetooth/gatt.h>
#include <bluetooth/conn.h>
#include <sys/atomic.h>
#include <string.h>

#define CONTROL_UUID(n) BT_UUID_128_ENCODE(0x8d53dc20 + (n),0x1db7,0x4cd3,0x868b,0x8a527460aa84)
static atomic_t enabled;
bool control_enabled(void) { return atomic_get(&enabled)!=0; }
void control_disconnect(void) { atomic_clear(&enabled); console_remote_clear(); }
static ssize_t command_write(struct bt_conn *conn,const struct bt_gatt_attr *attr,
                            const void *buf,uint16_t len,uint16_t offset,uint8_t flags)
{
    ARG_UNUSED(conn); ARG_UNUSED(attr); ARG_UNUSED(flags);
    if (offset) return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    if (ota_upload_locked()) return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    if (len==12 && !memcmp(buf,"MODE CONTROL",12)) {
        atomic_set(&enabled,1); return len;
    }
    if (!control_enabled()) return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    int rc=console_remote(buf,len);
    return rc ? BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES) : len;
}
BT_GATT_SERVICE_DEFINE(control_service,
    BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(CONTROL_UUID(0))),
    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(CONTROL_UUID(1)),BT_GATT_CHRC_WRITE,
                           BT_GATT_PERM_WRITE,NULL,command_write,NULL),
    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(CONTROL_UUID(2)),BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE,NULL,NULL,NULL),
    BT_GATT_CCC(NULL,BT_GATT_PERM_READ|BT_GATT_PERM_WRITE)
);
static void notify_connection(struct bt_conn *conn,void *data)
{
    if (!control_enabled() || !bt_gatt_is_subscribed(conn,&control_service.attrs[4],BT_GATT_CCC_NOTIFY)) return;
    const char *line=data;
    size_t len=strlen(line), limit=bt_gatt_get_mtu(conn)-3;
    while (len) {
        size_t n=MIN(len,limit);
        if (bt_gatt_notify(conn,&control_service.attrs[4],line,n)) return;
        line+=n; len-=n;
    }
    bt_gatt_notify(conn,&control_service.attrs[4],"\n",1);
}
void control_notify(const char *line)
{
    if (control_enabled()) bt_conn_foreach(BT_CONN_TYPE_LE,notify_connection,(void *)line);
}
