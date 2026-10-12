#include "validation.h"
#include "radio.h"
#include "ranging.h"
#include "uwb_frame.h"
#include "uwb_app.h"
#include "uwb_network.h"
#include "uwb_reference.h"
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <hal/nrf_gpio.h>

static bool anchor,continuous,csv,responder_only,cooperative,binary;
static uint16_t my_id,peer_id,last_source;
static uint16_t anchor_ids[12];
static size_t anchor_count;
static uint32_t token,next_ms,attempts,success,failures,last_ok,timeouts,served;
static double latest,min_distance,max_distance,sum;
static bool token_seeded;
static int last_rc;
static int last_stop_rc;
static uint32_t transient_errors;
bool uwb_is_anchor(void) { return anchor; }
bool uwb_running(void) { return continuous || multitag_running() || network_running() || reference_running(); }
bool uwb_configured(void) { return my_id && peer_id && my_id!=peer_id; }
uint16_t uwb_local_id(void) { return my_id; }
static uint32_t next_token(void)
{
    if (!token_seeded) { token=NRF_FICR->DEVICEID[0]^k_cycle_get_32(); token_seeded=true; }
    if (++token==UINT32_MAX) token=0;
    return token;
}
void uwb_stop_all(void)
{
    continuous=false; responder_only=false; cooperative=false; multitag_stop();
    network_stop();
    reference_stop();
    if (radio_stats()->ready) { int rc=radio_stop(); if (rc) out("UWB stop rc=%d",rc); }
}
int uwb_init(void) { uwb_stop_all(); return radio_init(); }
void uwb_publish(uint16_t tag,double meters,uint32_t sequence)
{
    if (binary) { uwb3d_publish(my_id,tag,meters,sequence);return; }
    if (csv) out("UWB,%04X,%.4f,0",tag,meters);
    else out("[UWB][RANGE] tag=0x%04x anchor=0x%04x seq=%u distance=%.4f m timestamp_ms=%u",tag,my_id,sequence,meters,k_uptime_get_32());
}
int uwb_measure(uint16_t tag,double *distance)
{
    if ((!anchor && !cooperative) || !my_id || !tag || my_id==tag || !distance) return -EINVAL;
    if (ota_connected()) return -EBUSY;
    if (!radio_stats()->ready) return -ENODEV;
    if (!radio_stats()->calibrated) return -EACCES;
    uint32_t sequence=next_token(); ++attempts;
    int rc=range_initiate(my_id,tag,sequence,distance); last_rc=rc;
    if (!rc) {
        ++success; last_source=tag; latest=*distance; sum+=*distance; last_ok=k_uptime_get_32();
        if (success==1 || *distance<min_distance) min_distance=*distance;
        if (success==1 || *distance>max_distance) max_distance=*distance;
        uwb_publish(tag,*distance,sequence);
    } else { ++failures; if (rc==-ETIMEDOUT) ++timeouts; }
    return rc;
}
static int one_range(void)
{
    if (!uwb_configured()) return -EACCES;
    if (ota_connected()) return -EBUSY;
    if (anchor && !responder_only) { double distance; return uwb_measure(peer_id,&distance); }
    if (!radio_stats()->ready || !radio_stats()->calibrated) return -EACCES;
    uint32_t sequence=UINT32_MAX;
    uint16_t source=peer_id;
    int rc=anchor_count ? range_respond_many(my_id,anchor_ids,anchor_count,&sequence,&source) : range_respond(my_id,peer_id,&sequence);
    if (sequence!=UINT32_MAX) {
        ++attempts; last_rc=rc;
        if (!rc) { ++served; last_source=source; last_ok=k_uptime_get_32(); }
        else { ++failures; if (rc==-ETIMEDOUT) ++timeouts; }
    }
    return rc;
}
void uwb_status(void)
{
    radio_status();
    out("UWB role=%s id=0x%04x peer=0x%04x continuous=%u last_rx_source=0x%04x output=%s",anchor?"anchor":"tag",my_id,peer_id,continuous,last_source,csv?"csv":"text");
    out("RANGE DS-TWR attempts=%u measured=%u tag_served=%u failed=%u timeout=%u rejected_frames=%u last_rc=%d",attempts,success,served,failures,timeouts,range_rejected_frames(),last_rc);
    out("RANGE recoverable_errors=%u last_stop_rc=%d",transient_errors,last_stop_rc);
    out("UWB allowed_anchor_count=%u (zero uses peer)",(unsigned)anchor_count);
    out("UWB anchor_listener=%u",responder_only);
    out("UWB cooperative=%u",cooperative);
    if (success) out("RANGE latest=%.4f min=%.4f max=%.4f mean=%.4f m success=%.2f%% last_ms=%u",latest,min_distance,max_distance,sum/success,100.0*success/attempts,last_ok);
    else out("RANGE distance=NOT_MEASURED");
}
static bool number(const char *text,int base,uint32_t max,uint32_t *value)
{
    if (!text || !*text || *text=='-' || *text=='+') return false;
    char *end; errno=0; unsigned long v=strtoul(text,&end,base);
    if (errno || *end || v>max) return false;
    *value=(uint32_t)v; return true;
}
int uwb_command(int argc,char **argv)
{
    if (!argc) return -EINVAL;
    const char *cmd=argv[0];
    if (!strcmp(cmd,"capabilities") && argc==1) {
#ifdef REFERENCE_RANGING
        out("U3D version=1 reference_tdma=1 experimental_sparse_BP=0 STS=1 AES=1 security_qualified=0 max_nodes=9");
#else
        out("U3D version=1 uart_range_export=1 experimental_sparse_BP=1 qualified_BP=0 extended_frame=1 STS=0 max_radio_frame=512");
#endif
        out("U3D net start experimental: min slot 10ms, max superframe 500ms; 30-node/10Hz NOT qualified");
        return 0;
    }
    if (!strcmp(cmd,"status") && argc==1) { uwb_status(); return 0; }
    if (!strcmp(cmd,"stop") && argc==1) { uwb_stop_all(); return 0; }
    if (!strcmp(cmd,"output") && argc==2) {
        if (!strcmp(argv[1],"csv")) { csv=true;binary=false; }
        else if (!strcmp(argv[1],"text")) { csv=false;binary=false; }
        else if (!strcmp(argv[1],"binary")) binary=true;
        else return -EINVAL;
        return 0;
    }
    if (ota_connected()) return -EBUSY;
    /* Host schedules one initiator at a time. An anchor returns to listening
     * after this synchronous exchange without changing its role or LED. */
    if (!strcmp(cmd,"range") && argc==2 && (anchor || cooperative) && responder_only && continuous) {
        uint32_t id; double distance;
        if (!number(argv[1],16,0xfffe,&id) || !id) return -EINVAL;
        return uwb_measure((uint16_t)id,&distance);
    }
    if (uwb_running()) return -EBUSY;
    if (!strcmp(cmd,"anchors") && argc>=2 && argc<=13) {
        if (argc==2 && !strcmp(argv[1],"clear")) { anchor_count=0; return 0; }
        uint16_t selected[12];
        for (int i=1;i<argc;++i) {
            uint32_t id;
            if (!number(argv[i],16,0xfffe,&id) || !id || id==my_id) return -EINVAL;
            for (int j=1;j<i;++j) if (selected[j-1]==id) return -EINVAL;
            selected[i-1]=(uint16_t)id;
        }
        memcpy(anchor_ids,selected,(argc-1)*sizeof(selected[0])); anchor_count=argc-1; return 0;
    }
    if (!strcmp(cmd,"init") && argc==1) return uwb_init();
    if (!strcmp(cmd,"role") && argc==2) {
        if (!strcmp(argv[1],"anchor")) anchor=true;
        else if (!strcmp(argv[1],"tag")) anchor=false;
        else return -EINVAL;
        return 0;
    }
    if ((!strcmp(cmd,"id") || !strcmp(cmd,"peer")) && argc==2) {
        uint32_t id;
        if (!number(argv[1],16,0xfffe,&id) || !id) return -EINVAL;
        if (!strcmp(cmd,"id")) {
            if (id==peer_id) return -EINVAL;
            for (size_t i=0;i<anchor_count;++i) if (id==anchor_ids[i]) return -EINVAL;
            my_id=(uint16_t)id;
        }
        else { if (id==my_id) return -EINVAL; peer_id=(uint16_t)id; }
        return 0;
    }
    if (!strcmp(cmd,"calibration") && argc==5) {
        uint32_t t,r,p,g;
        if (!number(argv[1],10,65534,&t) || !number(argv[2],10,65534,&r) || !number(argv[3],16,UINT32_MAX,&p) || !number(argv[4],16,255,&g)) return -EINVAL;
        return radio_calibrate(t,r,p,(uint8_t)g);
    }
    if ((!strcmp(cmd,"continuous") || !strcmp(cmd,"listen")) && argc==1) {
        if (!uwb_configured() || !radio_stats()->ready || !radio_stats()->calibrated) return -EACCES;
        if (!strcmp(cmd,"listen") && (!anchor || !anchor_count)) return -EINVAL;
        responder_only=!strcmp(cmd,"listen");
        last_stop_rc=0; continuous=true; next_ms=k_uptime_get_32(); return 0;
    }
    if (!strcmp(cmd,"cooperate") && argc==1) {
        if (!uwb_configured() || !anchor_count || !radio_stats()->ready || !radio_stats()->calibrated) return -EACCES;
        cooperative=true; responder_only=true; continuous=true; last_stop_rc=0; return 0;
    }
    if (!strcmp(cmd,"range") && argc==1) return one_range();
    if (!strcmp(cmd,"range") && argc==2) {
        uint32_t id; double distance;
        if (!number(argv[1],16,0xfffe,&id) || !id) return -EINVAL;
        return uwb_measure((uint16_t)id,&distance);
    }
    if (!strcmp(cmd,"tx") && argc==1) {
        if (!uwb_configured()) return -EACCES;
        uint8_t frame[RADIO_FRAME_MAX]; uint64_t ts;
        size_t len=frame_build(frame,sizeof(frame),MSG_TEST,my_id,peer_id,next_token(),NULL,0);
        return radio_tx(frame,len,&ts,false);
    }
    if (!strcmp(cmd,"rx") && argc==1) {
        if (!my_id) return -EACCES;
        uint8_t frame[RADIO_FRAME_MAX]; size_t len; uint64_t ts;
        int rc=radio_rx(frame,sizeof(frame),&len,&ts,500,false);
        if (rc) return rc;
        uwb_frame_t f;
        if (!frame_parse(frame,len,&f) || f.destination!=my_id) return -EBADMSG;
        last_source=f.source;
        out("UWB RX source=0x%04x length=%u type=%u hw_ts=%.0f",f.source,(unsigned)len,f.type,(double)ts);
        return 0;
    }
    return -EINVAL;
}
void uwb_service(void)
{
    if (ota_connected()) { if (uwb_running()) { uwb_stop_all(); out("UWB stopped for BLE/OTA connection"); } return; }
    if (multitag_running()) { multitag_service(); return; }
    if (!continuous) return;
    uint32_t now=k_uptime_get_32();
    if (anchor && !responder_only && (int32_t)(now-next_ms)<0) return;
    int rc=one_range(); next_ms=k_uptime_get_32()+100;
    if (!range_retryable(rc)) {
        last_stop_rc=rc; out("UWB continuous stopped rc=%d",rc); uwb_stop_all();
    } else if (rc && rc!=-ETIMEDOUT) {
        ++transient_errors;
    }
}
