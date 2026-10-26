#if !defined(REFERENCE_RANGING) && !defined(SWARM_RANGING)
/* Experimental sparse broadcast-pipelined DS-TWR. No physical anchors.
 * Coordinator 1 supplies a clock epoch. Delayed transmissions use DW time;
 * missed deadlines skip a slot, never transmit immediately into another slot.
 * Actual TX confirmation in the NEXT beacon validates predicted timestamps.
 * Conservative defaults deliberately do not claim 30-node / 10-Hz service. */
#include "validation.h"
#include "radio.h"
#include "uwb_app.h"
#include "uwb_network.h"
#include "uwb3d_wire.h"
#include "validation_math.h"
#include <hal/nrf_gpio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define NODES 30
#define MASK UINT64_C(0xffffffffff)
#define TICKS_MS UINT64_C(63897600)
#define HEADER 46
typedef struct { uint32_t frame;uint64_t tx,rx;bool valid; } observation_t;
typedef struct { uint32_t frame;uint64_t pt,pr,rt,rr,ft,fr;bool valid; } pending_t;
typedef struct { uint32_t frame,mm;bool valid; } result_t;
static observation_t seen[NODES+1],own;
static pending_t pending[NODES+1];
static result_t results[NODES+1];
static uint64_t uids[NODES+1],epoch,previous_beacon;
static uint16_t versions[NODES+1];
static uint32_t sf,netid=0x43594231,wire_sequence,tx_count,rx_count,missed,rejected,sync_losses;
static uint32_t previous_beacon_frame,slot_ms=10,period_ms=500,neighbors;
static uint32_t assignments[NODES+1];
static int last_tx_rc;
static int32_t last_arm_us,last_late_us;
static unsigned id=1,count=6;
static bool running,synced,sent,configured;
static double clock_ratio=1.;
static uint64_t get(const uint8_t *p,unsigned n) { uint64_t v=0;for(unsigned i=0;i<n;++i)v|=(uint64_t)p[i]<<(8*i);return v; }
static void put(uint8_t *p,uint64_t v,unsigned n) { for(unsigned i=0;i<n;++i)p[i]=(uint8_t)(v>>(8*i)); }
static int64_t difference(uint64_t a,uint64_t b) { return (int64_t)(((a-b)+(UINT64_C(1)<<39))&MASK)-(INT64_C(1)<<39); }
static uint64_t period_ticks(void) { return (uint64_t)(period_ms*TICKS_MS*clock_ratio); }
static uint64_t slot_ticks(unsigned n) { return (uint64_t)(n*slot_ms*TICKS_MS*clock_ratio); }
static void discovery(unsigned node,uint64_t uid,uint16_t version)
{
    if(uids[node]==uid && versions[node]==version)return;
    uids[node]=uid;versions[node]=version;
    if(id==1) { uint8_t wire[64];size_t n=u3d_discovery(node,uid,version,(uint64_t)k_uptime_get()*1000,wire_sequence++,wire,sizeof(wire));if(n)console_binary(wire,n); }
}
static void publish(unsigned a,unsigned b,uint32_t frame,uint32_t mm,uint32_t report_frame)
{
    if(id!=1 || !a || !b || a>=b || b>count || !mm || mm>1000000)return;
    uint32_t lag=report_frame-frame;if(lag>3)return;
    /* Effective time is midpoint of Poll and Final in coordinator time.
     * Clock synchronization/propagation uncertainty is not RF ToF. */
    uint64_t age_us=((uint64_t)lag*period_ms+period_ms/2+(b-a)*slot_ms)*1000;
    uint64_t now=(uint64_t)k_uptime_get()*1000;if(now<age_us)return;
    uint8_t payload[23],wire[64];put(payload,a,2);put(payload+2,b,2);put(payload+4,mm,4);
    put(payload+8,frame,4);put(payload+12,now-age_us,8);put(payload+20,65535,2);payload[22]=1;
    size_t n=u3d_encode(3,wire_sequence++,now,payload,sizeof(payload),wire,sizeof(wire));if(n)console_binary(wire,n);
}
static size_t packet(uint8_t *p,uint64_t predicted)
{
    memset(p,0,HEADER);p[0]=0xa7;p[1]=1;p[2]=(uint8_t)id;p[3]=(uint8_t)count;
    put(p+4,netid,4);put(p+8,sf,4);put(p+12,predicted,5);
    uint64_t uid=((uint64_t)NRF_FICR->DEVICEID[1]<<32)|NRF_FICR->DEVICEID[0];put(p+17,uid,8);
    uint32_t bitmap=0;for(unsigned n=1;n<=count;++n)if(n!=id && seen[n].valid && sf-seen[n].frame<=1)bitmap|=1U<<(n-1);
    put(p+25,bitmap,4);put(p+30,slot_ms,2);put(p+32,period_ms,2);
    if(own.valid && sf-own.frame==1) { p[34]=1;put(p+35,own.frame,4);put(p+39,own.tx,5); }
    put(p+44,FW_VERSION_PATCH,2);
    size_t pos=HEADER;
    for(unsigned n=1;n<=count;++n)if(bitmap&(1U<<(n-1))) { put(p+pos,seen[n].frame,4);put(p+pos+4,seen[n].rx,5);pos+=9; }
    for(unsigned n=1;n<id;++n)if(results[n].valid && sf-results[n].frame<=2) {
        p[pos]=(uint8_t)n;put(p+pos+1,results[n].frame,4);put(p+pos+5,results[n].mm,4);pos+=9;++p[29];
    }
    if(id==1) { p[34]|=2;for(unsigned n=1;n<=count;++n) { put(p+pos,assignments[n],4);pos+=4; } }
    return pos;
}
static void receive(const uint8_t *p,size_t length,uint64_t rx)
{
    if(length<HEADER || p[0]!=0xa7 || p[1]!=1 || p[3]!=count || get(p+4,4)!=netid || get(p+30,2)!=slot_ms || get(p+32,2)!=period_ms) { ++rejected;return; }
    unsigned source=p[2];uint32_t frame=get(p+8,4),bitmap=get(p+25,4);uint64_t tx=get(p+12,5);
    if(!source || source>count || source==id || bitmap>>count || p[29]>4 || (p[34]&~3U) || (bitmap&(1U<<(source-1)))) { ++rejected;return; }
    unsigned records=0;for(unsigned n=1;n<=count;++n)if(bitmap&(1U<<(n-1)))++records;
    bool maps=(p[34]&2)!=0;
    if((maps && source!=1) || length!=HEADER+9*(records+p[29])+(maps?4*count:0)) { ++rejected;return; }
    uint64_t uid=get(p+17,8);
    if(!uid || (uids[source] && uids[source]!=uid)) { ++rejected;return; }
    uint32_t assigned=neighbors;
    if(maps) {
        assigned=get(p+HEADER+9*(records+p[29])+4*(id-1),4);unsigned bits=0;
        for(unsigned n=1;n<id;++n)if(assigned&(1U<<(n-1)))++bits;
        if((assigned>>(id-1)) || bits!=(id>4?4:id-1)) { ++rejected;return; }
    }
    if(source==1 && id!=1 && !synced) {
        /* A restarted coordinator has a new sequence epoch. Never combine its
         * timestamps with pre-disconnection exchanges. UID still must match. */
        memset(seen,0,sizeof(seen));memset(pending,0,sizeof(pending));memset(results,0,sizeof(results));
        own.valid=false;previous_beacon=0;clock_ratio=1.;
    }
    if(seen[source].valid && (uint32_t)(frame-seen[source].frame)>=0x80000000U) { ++rejected;return; }
    if(seen[source].valid && frame==seen[source].frame)return;
    if(source==1 && id!=1) {
        if(previous_beacon && frame-previous_beacon_frame==1) {
            double ratio=(double)((rx-previous_beacon)&MASK)/(double)(period_ms*TICKS_MS);
            if(ratio>.9995 && ratio<1.0005)clock_ratio=.9*clock_ratio+.1*ratio;
        }
        previous_beacon=rx;previous_beacon_frame=frame;epoch=rx;sf=frame;synced=true;sent=false;
    }
    if(!synced || (uint32_t)(sf-frame)>1U) { ++rejected;return; }
    ++rx_count;discovery(source,get(p+17,8),get(p+44,2));
    uint64_t our_rx=0;uint32_t our_rx_frame=0;bool have_our_rx=false;size_t pos=HEADER;
    for(unsigned n=1;n<=count;++n)if(bitmap&(1U<<(n-1))) {
        if(n==id) { our_rx_frame=get(p+pos,4);our_rx=get(p+pos+4,5);have_our_rx=true; }
        pos+=9;
    }
    for(unsigned k=0;k<p[29];++k,pos+=9)publish(p[pos],source,get(p+pos+1,4),get(p+pos+5,4),frame);
    if(maps) {
        for(unsigned n=1;n<id;++n)if(!(assigned&(1U<<(n-1)))) { pending[n].valid=false;results[n].valid=false; }
        neighbors=assigned;
    }
    if(source<id && (neighbors&(1U<<(source-1)))) {
        pending_t *q=&pending[source];
        if(q->valid && (p[34]&1) && get(p+35,4)==q->frame) {
            uint64_t actual=get(p+39,5);double meters;
            if(actual==q->ft && u3d_pipeline_distance(q->frame-1,q->frame-1,q->frame,63,q->pt,q->pr,q->rt,q->rr,actual,q->fr,&meters)) {
                results[source]=(result_t){.frame=q->frame,.mm=(uint32_t)llround(meters*1000),.valid=true};
            } else ++rejected;
            q->valid=false;
        }
        if((p[34]&1) && get(p+35,4)==(uint32_t)(frame-1) && seen[source].valid && seen[source].frame==(uint32_t)(frame-1) && own.valid && own.frame==(uint32_t)(frame-1) && have_our_rx && our_rx_frame==(uint32_t)(frame-1)) {
            *q=(pending_t){.frame=frame,.pt=get(p+39,5),.pr=seen[source].rx,.rt=own.tx,.rr=our_rx,.ft=tx,.fr=rx,.valid=true};
        }
    }
    seen[source]=(observation_t){.frame=frame,.tx=tx,.rx=rx,.valid=true};
}
bool network_running(void) { return running; }
void network_stop(void)
{
    bool was=running;running=false;synced=false;
    if(was) { radio_stop();radio_network_phy(false); }
}
void network_service(void)
{
    if(!running)return;
    if(ota_connected()) { network_stop();return; }
    uint64_t now=radio_system_time();
    /* Arm the next delayed slot BEFORE its deadline, including node 1.
     * Advancing only at the epoch would make every later coordinator TX late. */
    if(id==1 && sent && difference(now,epoch)>=(int64_t)(period_ticks()-8*TICKS_MS)) {
        epoch=(epoch+period_ticks())&MASK;++sf;sent=false;
        uint8_t payload[10],wire[40];put(payload,sf,4);payload[4]=1;payload[5]=2;put(payload+6,period_ms*1000,4);
        size_t n=u3d_encode(6,wire_sequence++,(uint64_t)k_uptime_get()*1000,payload,sizeof(payload),wire,sizeof(wire));if(n)console_binary(wire,n);
        if(sf%10==0)for(unsigned node=1;node<=count;++node)if(uids[node]) {
            uint8_t hello[64];size_t size=u3d_discovery(node,uids[node],versions[node],(uint64_t)k_uptime_get()*1000,wire_sequence++,hello,sizeof(hello));
            if(size)console_binary(hello,size);
        }
    }
    if(id!=1 && synced && difference(now,epoch)>(int64_t)(period_ticks()+period_ticks()/2)) { synced=false;++sync_losses; }
    if(synced && !sent) {
        uint64_t raw=(epoch+slot_ticks(id-1)-radio_tx_antenna_delay())&MASK;
        int64_t remaining=difference(raw,now);
        if(remaining< (int64_t)(TICKS_MS/2)) { ++missed;sent=true;own.valid=false;last_late_us=(int32_t)(remaining/63898); }
        else if(remaining<(int64_t)(8*TICKS_MS)) {
            uint8_t data[RADIO_FRAME_MAX];uint64_t actual;size_t n=packet(data,u3d_predicted_tx(raw,radio_tx_antenna_delay()));
            last_arm_us=(int32_t)(remaining/63898);
            int rc=radio_tx_delayed(data,n,raw,&actual,false);sent=true;last_tx_rc=rc;
            if(rc) { ++missed;own.valid=false; }
            else { ++tx_count;own=(observation_t){.frame=sf,.tx=actual,.valid=true}; }
            return;
        }
    }
    uint8_t data[RADIO_FRAME_MAX];size_t length;uint64_t timestamp;
    int rc=radio_network_rx(data,sizeof(data),&length,&timestamp);
    if(!rc)receive(data,length,timestamp);
    else if(rc!=-EAGAIN && rc!=-ETIMEDOUT && rc!=-EBADMSG && rc!=-EMSGSIZE) { out("NETWORK fatal radio error=%d",rc);network_stop(); }
}
static bool number(const char *s,unsigned long *v,int base) { char *end;*v=strtoul(s,&end,base);return *s && !*end; }
int network_command(int argc,char **argv)
{
    if(argc==1 && !strcmp(argv[0],"status")) {
        out("NETWORK experimental_sparse_BP running=%u synchronized=%u node=%u/%u frame=%u slot_ms=%u superframe_ms=%u",running,synced,id,count,sf,slot_ms,period_ms);
        out("NETWORK tx=%u rx=%u missed_slots=%u rejected=%u sync_losses=%u clock_ppm=%.2f",tx_count,rx_count,missed,rejected,sync_losses,(clock_ratio-1)*1e6);
        out("NETWORK last_tx_rc=%d arm_us=%d late_us=%d",last_tx_rc,last_arm_us,last_late_us);return 0;
    }
    if(argc==1 && !strcmp(argv[0],"stop")) { network_stop();return 0; }
    if(argc==3 && !strcmp(argv[0],"assign") && id==1) {
        unsigned long node,mask;
        if(!number(argv[1],&node,10)||!number(argv[2],&mask,16)||node<2||node>count||mask>>(node-1))return -EINVAL;
        unsigned bits=0;for(unsigned n=0;n<node-1;++n)if(mask&(1U<<n))++bits;
        if(bits!=(node>4?4:node-1))return -EINVAL;
        assignments[node]=mask;uint8_t payload[6],wire[32];put(payload,node,2);put(payload+2,mask,4);
        size_t n=u3d_encode(9,wire_sequence++,(uint64_t)k_uptime_get()*1000,payload,6,wire,sizeof(wire));if(n)console_binary(wire,n);
        return 0;
    }
    if(running || ota_connected())return -EBUSY;
    if(argc==6 && !strcmp(argv[0],"configure")) {
        unsigned long a,b,c,d,e;
        if(!number(argv[1],&a,10)||!number(argv[2],&b,10)||!number(argv[3],&c,10)||!number(argv[4],&d,10)||!number(argv[5],&e,16))return -EINVAL;
        if(a<1 || a>b || b<2 || b>NODES || c<10 || c>50 || d<b*c+20 || d>500)return -EINVAL;
        id=a;count=b;slot_ms=c;period_ms=d;netid=e;neighbors=0;configured=true;
        for(unsigned node=1;node<=count;++node)assignments[node]=(1U<<((node>4)?4:node-1))-1U;
        for(unsigned n=1;n<id && n<=4;++n)neighbors|=1U<<(n-1);
        return 0;
    }
    if(argc>=1 && !strcmp(argv[0],"neighbors")) {
        if(argc>5)return -EINVAL;
        uint32_t mask=0;
        for(int k=1;k<argc;++k) { unsigned long n;if(!number(argv[k],&n,10)||!n||n>=id||(mask&(1U<<(n-1))))return -EINVAL;mask|=1U<<(n-1); }
        if(id>4 && argc!=5)return -EINVAL;
        neighbors=mask;return 0;
    }
    if(argc==2 && !strcmp(argv[0],"start") && !strcmp(argv[1],"experimental")) {
        if(!configured || uwb_running() || !radio_stats()->ready || !radio_stats()->calibrated)return -EACCES;
        int rc=radio_network_phy(true);if(rc)return rc;
        memset(seen,0,sizeof(seen));memset(pending,0,sizeof(pending));memset(results,0,sizeof(results));memset(uids,0,sizeof(uids));
        own.valid=false;sf=0;sent=false;clock_ratio=1.;previous_beacon=0;tx_count=rx_count=missed=rejected=sync_losses=0;
        last_tx_rc=last_arm_us=last_late_us=0;
        epoch=(radio_system_time()+10*TICKS_MS+radio_tx_antenna_delay())&MASK;synced=id==1;running=true;
        discovery(id,((uint64_t)NRF_FICR->DEVICEID[1]<<32)|NRF_FICR->DEVICEID[0],FW_VERSION_PATCH);return 0;
    }
    return -EINVAL;
}

#else
#include "uwb_network.h"
bool network_running(void) { return false; }
void network_stop(void) {}
void network_service(void) {}
int network_command(int n,char **v) { (void)n;(void)v;return -95; }
#endif
