#include "uwb_swarm.h"
#ifdef SWARM_RANGING
#include "validation.h"
#include "uwb_app.h"
#include "radio.h"
#include "uwb3d_wire.h"
#include "swarm_wire.h"
#include "swarm_motion.h"
#include <hal/nrf_gpio.h>
#include <stdlib.h>
#include <string.h>
#define MASK40 UINT64_C(0xffffffffff)
#define PERIOD UINT64_C(2492006400) /* 39000 us * 63897.6 */
#define SLOT UINT64_C(83066880) /* 1300 us */
#define HEADER 40
/* Each peer record: uint32 reply DTU, uint16 received sequence, uint8 dB.
 * Invalid/missed RX is represented by the validity bitmap, never a wrapped age. */
typedef struct { uint64_t rx;uint16_t seq;uint8_t quality;uint32_t boot;bool valid; } peer_t;
static peer_t peers[31];
static bool running,sniffer,collector,synced,sent,pending,confirmed,configured;
static uint8_t id,count,master;
static uint16_t seq,previous_seq;
static uint32_t network,boot,term,wire_seq,rx_count,tx_count,missed,rejected;
static uint64_t epoch,previous_tx,master_seen,started,last_master_rx;
static uint16_t last_master_seq;
static double ratio=1.;
static int last_tx_rc,last_arm_us;
static swarm_frame_t tx_frame;
static uint8_t rxbuf[512];
static size_t tx_length;
static uint64_t get(const uint8_t *p,unsigned n) { uint64_t v=0;for(unsigned i=0;i<n;i++)v|=(uint64_t)p[i]<<(8*i);return v; }
static void put(uint8_t *p,uint64_t v,unsigned n) { for(unsigned i=0;i<n;i++)p[i]=(uint8_t)(v>>(8*i)); }
/* Signed modular difference is only used for intervals shorter than 2^39 DTU. */
static int64_t diff(uint64_t a,uint64_t b) { return swarm_difference40(a,b); }
static uint64_t period(void) { return (uint64_t)(PERIOD*ratio); }
/* Spread a partially populated fleet over the fixed 30-slot superframe.
 * count=30 retains contiguous 1.3 ms slots; count=4 uses 0,7,15,22.
 * Each node still transmits once per 39 ms; empty slots provide RX/CPU slack. */
static uint64_t slot(unsigned n) { return (uint64_t)(SLOT*((30U*n)/count)*ratio); }
static void export_frame(const uint8_t *frame,size_t length,uint64_t timestamp)
{
    uint8_t payload[257],wire[290];
    if(length+5>sizeof(payload))return;
    put(payload,timestamp,5);memcpy(payload+5,frame,length);
    size_t n=u3d_encode(11,wire_seq++,(uint64_t)k_uptime_get()*1000,payload,length+5,wire,sizeof(wire));
    if(n)console_binary(wire,n);
}
static size_t make_packet(uint64_t predicted)
{
    memset(&tx_frame,0,sizeof(tx_frame));
    tx_frame.mac=(swarm_mac_t){.frame_control=0x8841,.sequence=(uint8_t)seq,.pan_id=(uint16_t)network,.destination=0xffff,.source=id};
    uint8_t *txbuf=(uint8_t *)&tx_frame.header;
    txbuf[0]='S';txbuf[1]='C';txbuf[2]=1;txbuf[3]=id;txbuf[4]=count;txbuf[5]=master;
    put(txbuf+6,seq,2);put(txbuf+8,network,4);put(txbuf+12,boot,4);put(txbuf+16,term,4);put(txbuf+20,predicted,5);
    if(confirmed) { put(txbuf+25,previous_seq,2);put(txbuf+27,previous_tx,5);txbuf[36]=1; }
    swarm_motion_encode(txbuf);
    uint32_t valid=0;size_t offset=HEADER;
    for(unsigned p=1;p<=count;p++)if(p!=id) {
        uint64_t age=(predicted-peers[p].rx)&MASK40;
        if(peers[p].valid && age && age<=UINT32_MAX) {
            valid|=1U<<(p-1);put(txbuf+offset,age,4);put(txbuf+offset+4,peers[p].seq,2);txbuf[offset+6]=peers[p].quality;
        } else txbuf[offset+6]=255;
        offset+=7;
    }
    put(txbuf+32,valid,4);return sizeof(swarm_mac_t)+offset;
}
static void receive(size_t length,uint64_t rx,uint8_t quality)
{
    const uint8_t *p=rxbuf+sizeof(swarm_mac_t);
    if(length!=9+HEADER+7*(count-1) || get(rxbuf,2)!=0x8841 || get(rxbuf+3,2)!=(uint16_t)network || get(rxbuf+5,2)!=0xffff || p[0]!='S' || p[1]!='C' || p[2]!=1 || p[4]!=count || get(p+8,4)!=network || get(rxbuf+7,2)!=p[3]) { ++rejected;return; }
    unsigned source=p[3],leader=p[5];uint16_t frame=get(p+6,2);uint32_t source_boot=get(p+12,4),new_term=get(p+16,4);
    if(!source || source>count || !leader || leader>count || get(p+32,4)>>count) { ++rejected;return; }
    ++rx_count;
    if(sniffer || collector)export_frame(rxbuf,length,rx);
    if(sniffer)return;
    if(source==id)return;
    if(source==leader && (!synced || leader<=master || diff(rx,master_seen)>2*(int64_t)period())) {
        if(master==leader && term==new_term && (uint16_t)(frame-last_master_seq)==1) {
            double observed=(double)((rx-last_master_rx)&MASK40)/PERIOD;
            if(observed>.9995 && observed<1.0005)ratio=.9*ratio+.1*observed;
        } else { ratio=1.;confirmed=false;memset(peers,0,sizeof(peers)); }
        master=leader;term=new_term;epoch=(rx-slot(source-1))&MASK40;
        master_seen=rx;last_master_rx=rx;last_master_seq=frame;
        seq=frame;synced=true;sent=id<source;
    }
    if(peers[source].valid && peers[source].boot==source_boot) {
        uint16_t delta=frame-peers[source].seq;
        if(!delta || delta>=0x8000)return;
    }
    peers[source]=(peer_t){.rx=rx,.seq=frame,.quality=quality,.boot=source_boot,.valid=true};
}
bool swarm_running(void) { return running; }
void swarm_stop(void)
{
    bool was=running;running=false;synced=false;pending=false;
    radio_swarm_cancel();
    if(was) { radio_stop();radio_network_phy(false);radio_port_fast(false); }
}
void swarm_service(void)
{
    if(!running)return;
    if(ota_connected()) { swarm_stop();return; }
    if(pending) {
        uint64_t actual;int rc=radio_swarm_complete(&actual);
        if(rc==-EAGAIN)return;
        pending=false;confirmed=!rc;
        if(!rc) {
            previous_tx=actual;previous_seq=seq;++tx_count;
            /* Export only completed TX; next frame confirms its actual stamp. */
            if(collector)export_frame((const uint8_t *)&tx_frame,tx_length,actual);
        } else ++missed;
        if(!rc && count<=6)swarm_motion_poll();
    }
    size_t length;uint64_t rx;uint8_t quality=255;
    if(!radio_swarm_rx(rxbuf,sizeof(rxbuf),&length,&rx,sniffer?NULL:&quality))receive(length,rx,quality);
    uint64_t now=radio_system_time();
    if(!sniffer) {
        bool lost=!synced?diff(now,started)>2*(int64_t)PERIOD:
            (master!=id && diff(now,master_seen)>2*(int64_t)period()) ||
            (master>id && diff(now,started)>2*(int64_t)PERIOD);
        if(lost) {
            unsigned candidate=id;
            for(unsigned p=1;p<id;p++)if(peers[p].valid && diff(now,peers[p].rx)<2*(int64_t)PERIOD) { candidate=p;break; }
            if(candidate==id) {
                master=id;term=boot^(uint32_t)now;ratio=1.;epoch=(now+SLOT-slot(id-1))&MASK40;
                synced=true;sent=false;confirmed=false;memset(peers,0,sizeof(peers));
            } else synced=false;
        }
        if(synced) {
            uint64_t target=(epoch+slot(id-1))&MASK40;
            if(sent && diff(now,target)>=(int64_t)period()-SLOT) { epoch=(epoch+period())&MASK40;++seq;sent=false;target=(epoch+slot(id-1))&MASK40; }
            int64_t ahead=diff(target,now);
            if(!sent && ahead>1000*63898)swarm_timer_arm((uint32_t)(ahead/63898)-1000);
            if(!sent && ahead<0) { sent=true;++missed;confirmed=false; }
            else if(!sent && ahead<1000*63898 && ahead>100*63898) {
                uint64_t raw=(target-radio_tx_antenna_delay())&MASK40;
                size_t length=make_packet(swarm_predicted_tx(raw,radio_tx_antenna_delay()));
                tx_length=length;
                last_arm_us=ahead/63898;
                int rc=radio_swarm_arm((const uint8_t *)&tx_frame,length,raw);last_tx_rc=rc;sent=true;
                if(!rc)pending=true;else { ++missed;confirmed=false; }
                return;
            }
        }
    }
}
static bool number(const char *s,unsigned long *v,int base) { char *end;if(!*s || *s=='-')return false;*v=strtoul(s,&end,base);return !*end; }
int swarm_command(int argc,char **argv)
{
    if(argc==1 && !strcmp(argv[0],"motion")) { swarm_motion_status();return 0; }
    if(argc==1 && !strcmp(argv[0],"status")) {
        out("SWARM running=%u sniffer=%u id=%u count=%u master=%u sync=%u seq=%u rx=%u tx=%u missed=%u rejected=%u drops=%u",running,sniffer,id,count,master,synced,seq,rx_count,tx_count,missed,rejected,console_drops());out("SWARM arm_us=%d tx_rc=%d prepare_us=%u rx_us=%u",last_arm_us,last_tx_rc,radio_swarm_prepare_us(),radio_swarm_rx_us());return 0;
    }
    if(argc==1 && !strcmp(argv[0],"stop")) { swarm_stop();return 0; }
    if(argc==2 && !strcmp(argv[0],"collector")) {
        unsigned long enabled;
        if(running)return -EBUSY;
        if(!number(argv[1],&enabled,10) || enabled>1)return -EINVAL;
        collector=enabled;return 0;
    }
    if(argc==4 && !strcmp(argv[0],"configure")) {
        unsigned long a,b,c;if(uwb_running())return -EBUSY;
        if(!number(argv[1],&a,10) || !number(argv[2],&b,10) || !number(argv[3],&c,16) || b<3 || b>30 || a>b)return -EINVAL;
        id=a;count=b;network=c;sniffer=id==0;collector=false;configured=true;return 0;
    }
    if(argc==1 && !strcmp(argv[0],"start")) {
        if(!configured || uwb_running() || !radio_stats()->ready)return -EBUSY;
        /* 1-Mbaud UART cannot carry all 30 full raw broadcasts at 25.6 Hz.
         * Fail explicitly instead of silently dropping raw timestamp packets. */
        if((sniffer || collector) && count>17)return -ENOSPC;
        if(!sniffer && count<=6) { swarm_motion_init();swarm_motion_status(); }
        int rc=radio_network_phy(true);if(rc)return rc;radio_port_fast(true);radio_swarm_setup(9+HEADER+7*(count-1));swarm_timer_init();
        boot=NRF_FICR->DEVICEID[0]^k_cycle_get_32();if(!boot)boot=1;
        memset(peers,0,sizeof(peers));synced=sent=pending=confirmed=false;seq=0;ratio=1.;master=0;
        started=radio_system_time();rx_count=tx_count=missed=rejected=0;running=true;return 0;
    }
    return -EINVAL;
}
#else
bool swarm_running(void) { return false; }
void swarm_stop(void) {}
void swarm_service(void) {}
int swarm_command(int argc,char **argv) { (void)argc;(void)argv;return -95; }
#endif
