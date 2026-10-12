/* Custom-PCB adapter for albifache/DWM3001CDK; see vendor/albifache/LICENSE. */
#ifdef REFERENCE_RANGING
#include "validation.h"
#include "radio.h"
#include "uwb_app.h"
#include "uwb_reference.h"
#include "uwb3d_wire.h"
#include "../vendor/albifache/app/app.h"
#include <deca_device_api.h>
#include <hal/nrf_gpio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
extern int app_current_role(void);
static bool active,configured,keyed;
static unsigned id=1,count=2,period=200,pan=0x4359,fixed_tag,rotating_tag;
static uint32_t wire_seq,rounds,failures,mask;
static uint64_t uids[10];
static int64_t deadline,next_round;
static app_init_obj_t setup;
bool reference_running(void) { return active; }
bool reference_anchor(void) { return app_current_role()==2; }
bool reference_abort(void) { return !active || ota_upload_locked() || ota_connected() || radio_port_error() || k_uptime_get()>=deadline; }
void reference_stop(void)
{
    active=false;keyed=false;memset(&setup,0,sizeof(setup));
    /* PHY remains until the next explicit uwb init; no RX restarts here. */
    if(radio_stats()->ready)radio_stop();
}
static void put(uint8_t *p,uint64_t v,unsigned n) { for(unsigned i=0;i<n;++i)p[i]=(uint8_t)(v>>(8*i)); }
static void hello(unsigned node)
{
    if(!uids[node])return;
    uint8_t wire[64];
    /* Reference image uses version 1.1.0. */
    uint8_t payload[20];put(payload,node,2);put(payload+2,uids[node],8);
    put(payload+10,1,2);put(payload+12,1,2);put(payload+14,0,2);put(payload+16,0,4);
    size_t n=u3d_encode(1,wire_seq++,(uint64_t)k_uptime_get()*1000,payload,sizeof(payload),wire,sizeof(wire));
    if(n)console_binary(wire,n);
}
void reference_service(void)
{
    if(!active)return;
    if(ota_connected() || ota_upload_locked()) { reference_stop();return; }
    int64_t now=k_uptime_get();if(id==1 && now<next_round)return;
    app_ctrl_obj_t control={0};
    if(id==1) {
        unsigned tag=fixed_tag?fixed_tag:1+(rotating_tag++%count);
        control.tag_mac_addr=tag-1;
        for(unsigned peer=1;peer<=count;++peer)
            if(peer!=tag && (!mask || (mask&(1U<<(peer-1)))))control.anchor_mac_addr[control.num_anchors++]=peer-1;
        if(!control.num_anchors || app_set_ctrl_params(&control)) { ++failures;next_round=now+period;return; }
    }
    if(radio_reference_acquire())return;
    deadline=now+200;int rc=app_run_ieee_802_15_4z_schedule();
    dwt_forcetrxoff();radio_reference_release();
    if(reference_abort()) { if(ota_connected() || ota_upload_locked())reference_stop(); }
    if(rc)++failures;else ++rounds;
    if(id!=1)return;
    next_round=MAX(now+period,k_uptime_get()+2);
    app_log_info_t log={0};app_read_log_info(&log);
    uint64_t stamp=(uint64_t)k_uptime_get()*1000;
    uint8_t network[10],wire[64];put(network,log.superframe_id,4);network[4]=rc==0;network[5]=3;put(network+6,period*1000,4);
    size_t n=u3d_encode(6,wire_seq++,stamp,network,10,wire,sizeof(wire));if(n)console_binary(wire,n);
    if(rc)return;
    unsigned tag=log.tag_mac_addr+1;if(tag>count)return;
    uint64_t effective=(uint64_t)(now+(3+log.num_anchors)*3)*1000;
    if(effective>stamp)effective=stamp;
    hello(tag);
    for(unsigned i=0;i<log.num_anchors && i<8;++i) {
        unsigned peer=log.anchor_mac_addr[i]+1;
        double meters=log.dist[i]*DWT_TIME_UNITS*299702547.;
        if(peer>count || peer==tag || !log.dist[i] || !isfinite(meters) || meters>1000)continue;
        hello(peer);uint8_t body[23];put(body,MIN(tag,peer),2);put(body+2,MAX(tag,peer),2);
        put(body+4,(uint32_t)llround(meters*1000),4);put(body+8,log.superframe_id,4);
        put(body+12,effective,8);put(body+20,65535,2);body[22]=1;
        n=u3d_encode(3,wire_seq++,stamp,body,23,wire,sizeof(wire));if(n)console_binary(wire,n);
    }
}
static bool number(const char *s,unsigned long *v,int base) { char *end;*v=strtoul(s,&end,base);return *s && !*end; }
static bool key(const char *s,uint8_t outkey[16])
{
    if(strlen(s)!=32)return false;
    for(unsigned i=0;i<16;++i) { char pair[3]={s[i*2],s[i*2+1],0};unsigned long n;if(!number(pair,&n,16)||n>255)return false;outkey[i]=(uint8_t)n; }
    return true;
}
int reference_command(int argc,char **argv)
{
    if(argc==1 && !strcmp(argv[0],"status")) {
        out("REFERENCE running=%u id=%u/%u PAN=%04x period_ms=%u rounds=%u failures=%u role=%d STS=1 AES_GCM=1 security_qualified=0",active,id,count,pan,period,rounds,failures,app_current_role());return 0;
    }
    if(argc==1 && !strcmp(argv[0],"stop")) { reference_stop();return 0; }
    if(active || ota_connected())return -EBUSY;
    if(argc==5 && !strcmp(argv[0],"configure")) {
        unsigned long a,b,c,d;
        if(!number(argv[1],&a,10)||!number(argv[2],&b,10)||!number(argv[3],&c,10)||!number(argv[4],&d,16)||a<1||a>b||b<2||b>9||c<150||c>5000||d>=65535)return -EINVAL;
        id=a;count=b;period=c;pan=d;fixed_tag=rotating_tag=mask=0;configured=true;keyed=false;
        memset(uids,0,sizeof(uids));uids[id]=((uint64_t)NRF_FICR->DEVICEID[1]<<32)|NRF_FICR->DEVICEID[0];return 0;
    }
    if(argc==3 && !strcmp(argv[0],"keys")) {
        uint8_t aes[16],sts[16];if(!key(argv[1],aes)||!key(argv[2],sts))return -EINVAL;
        memset(&setup,0,sizeof(setup));memcpy(&setup.aes_key,aes,16);memcpy(&setup.sts_key,sts,16);
        memset(aes,0,16);memset(sts,0,16);keyed=true;return 0;
    }
    if(argc==3 && !strcmp(argv[0],"peer")) {
        unsigned long peer;if(!number(argv[1],&peer,10)||!peer||peer>count||strlen(argv[2])!=16)return -EINVAL;
        char *end;uint64_t uid=strtoull(argv[2],&end,16);if(*end || !uid)return -EINVAL;uids[peer]=uid;return 0;
    }
    if(argc==3 && !strcmp(argv[0],"roles")) {
        unsigned long tag,anchors;if(!number(argv[1],&tag,10)||!number(argv[2],&anchors,16)||tag>count||anchors>>count)return -EINVAL;
        fixed_tag=tag;mask=anchors;return 0;
    }
    if(argc==2 && !strcmp(argv[0],"start") && !strcmp(argv[1],"experimental")) {
        if(!configured||!keyed||uwb_running())return -EACCES;
        int rc=radio_reference_phy();if(rc)return rc;
        setup.mac_addr=id-1;setup.pan_id=pan;rc=app_init(&setup);if(rc)return -EIO;
        rounds=failures=0;next_round=k_uptime_get()+20;active=true;return 0;
    }
    return -EINVAL;
}
#endif
