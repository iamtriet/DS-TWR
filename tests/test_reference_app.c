/* Exercise the actual adapted upstream parser/scheduler with a fake transceiver. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../vendor/albifache/app/app.c"
static uint8_t frame[127];static uint16_t frame_length;static bool abort_now;
bool reference_abort(void) { return abort_now; }
void deca_usleep(unsigned long t) { (void)t; }
void dwt_forcetrxoff(void) {}
uint32_t dwt_readsysstatuslo(void) { return SYS_STATUS_RXFCG_BIT_MASK|SYS_STATUS_CIADONE_BIT_MASK|SYS_STATUS_TXFRS_BIT_MASK; }
uint32_t dwt_readsysstatushi(void) { return 0; }
void dwt_writesysstatuslo(uint32_t v) { (void)v; }
void dwt_writesysstatushi(uint32_t v) { (void)v; }
int32_t dwt_readstsquality(int16_t *p,int32_t s) { assert(p);*p=1024;(void)s;return 1; }
int32_t dwt_readstsstatus(uint16_t *p,int32_t s) { assert(p);*p=0;(void)s;return 0; }
uint16_t dwt_getframelength(uint8_t *p) { assert(p);*p=1;return frame_length; }
void dwt_readrxdata(uint8_t *p,uint16_t n,uint16_t offset) { assert(offset+n<=frame_length);memcpy(p,frame+offset,n); }
void dwt_configure_aes(dwt_aes_config_t *p) { (void)p; }
void dwt_configurestskey(dwt_sts_cp_key_t *p) { (void)p; }
void dwt_configurestsiv(dwt_sts_cp_iv_t *p) { (void)p; }
void dwt_configurestsloadiv(void) {}
void dwt_set_keyreg_128(dwt_aes_key_t *p) { (void)p; }
int8_t dwt_do_aes(dwt_aes_job_t *p,dwt_aes_core_type_e c) {
    (void)c;
    if(p->mode==AES_Decrypt) { assert(p->payload_len<=sizeof(frame)-HEADER_LEN);memcpy(p->payload,frame+HEADER_LEN,p->payload_len); }
    return 0;
}
int32_t dwt_starttx(uint8_t m) { (void)m;return 0; }
int32_t dwt_rxenable(int32_t m) { (void)m;return 0; }
void dwt_writetxfctrl(uint16_t n,uint16_t o,uint8_t r) { (void)n;(void)o;(void)r; }
void dwt_readrxtimestamp_sts(uint8_t *p) { memset(p,0,5); }
void dwt_readtxtimestamp(uint8_t *p) { memset(p,0,5); }
void dwt_setdelayedtrxtime(uint32_t t) { (void)t; }
void dwt_setrxtimeout(uint32_t t) { (void)t; }
static void init_frame(uint64_t number,uint8_t anchors) {
    memset(frame,0,sizeof(frame));frame_length=HEADER_LEN+7+MIC_LEN+FCS_LEN;
    mac_header_t mac={.frame_ctrl=MAC_FRAME_CTRL,.pan_id=0x4359,.dest_addr=BROADCAST_MAC_ADDR,.src_addr=0};
    app_header_t app={.version=1,.msg_type=APP_MSG_INIT,.superframe_id=number,.slot_id=0};
    assert(!mac_header_write(&mac,frame,sizeof(frame)));assert(!app_header_write(&app,frame,sizeof(frame)));
    frame[HEADER_LEN]=anchors;frame[HEADER_LEN+1]=2;frame[HEADER_LEN+3]=0;frame[HEADER_LEN+5]=1;
}
int main(void) {
    app_init_obj_t obj={.mac_addr=1,.pan_id=0x4359};assert(!app_init(&obj));
    init_frame(10,2);assert(app_wait_init_msg()==APP_SUCCESS);assert(app_current_role()==ANCHOR);
    assert(app_wait_init_msg()==APP_RUN_ERROR); /* Authenticated replay. */
    init_frame(11,255);assert(app_wait_init_msg()==APP_RUN_ERROR);
    init_frame(12,2);frame_length=HEADER_LEN+1;assert(app_wait_init_msg()==APP_RUN_ERROR);
    app_header_t h;assert(app_header_read(&h,frame,APP_HEADER_LEN)==APP_RUN_ERROR);
    abort_now=true;assert(app_run_ieee_802_15_4z_schedule()==APP_RUN_ERROR);
    puts("reference actual parser: role assignment, replay, bounds, driver pointers and bounded abort passed");
    return 0;
}
