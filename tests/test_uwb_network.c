#include "validation.h"
#include "radio.h"
#include "uwb_app.h"
#include "uwb_network.h"
#include "uwb3d_wire.h"
#include <hal/nrf_gpio.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define T UINT64_C(63897600)
#define MASK UINT64_C(0xffffffffff)
mock_ficr_t mock_ficr;
static uint64_t now;
static uint8_t incoming[512],transmitted[512];
static size_t incoming_n,transmitted_n;
static uint64_t incoming_time,our_tx;
static unsigned transmissions;
static bool phy,ota;
static radio_stats_t stats={.ready=true,.calibrated=true};
int64_t k_uptime_get(void) { return now/T; }
bool ota_connected(void) { return ota; }
bool uwb_running(void) { return false; }
void out(const char *fmt,...) { (void)fmt; }
int console_binary(const uint8_t *d,size_t n) { assert(d && n);return 0; }
const radio_stats_t *radio_stats(void) { return &stats; }
int radio_stop(void) { return 0; }
int radio_network_phy(bool enable) { phy=enable;return 0; }
uint64_t radio_system_time(void) { return now; }
uint16_t radio_tx_antenna_delay(void) { return 100; }
int radio_tx_delayed(const uint8_t *d,size_t n,uint64_t raw,uint64_t *stamp,bool response)
{
    (void)response;assert(n<=512);memcpy(transmitted,d,n);transmitted_n=n;
    *stamp=u3d_predicted_tx(raw,100);our_tx=*stamp;now=(*stamp+T)&MASK;++transmissions;return 0;
}
int radio_rx(uint8_t *d,size_t cap,size_t *n,uint64_t *stamp,uint32_t ms,bool enabled)
{
    (void)ms;(void)enabled;
    if(!incoming_n)return -ETIMEDOUT;
    assert(incoming_n<=cap);memcpy(d,incoming,incoming_n);*n=incoming_n;*stamp=incoming_time;incoming_n=0;return 0;
}
static void put(uint8_t *p,uint64_t v,unsigned n) { for(unsigned i=0;i<n;++i)p[i]=(uint8_t)(v>>(8*i)); }
int radio_network_rx(uint8_t *d,size_t cap,size_t *n,uint64_t *t) { return radio_rx(d,cap,n,t,1,false); }
static uint64_t get(uint8_t *p,unsigned n) { uint64_t v=0;for(unsigned i=0;i<n;++i)v|=(uint64_t)p[i]<<(8*i);return v; }
static void beacon(uint32_t sf,uint64_t tx,uint64_t prior_tx,uint64_t rx_our_response)
{
    memset(incoming,0,sizeof(incoming));incoming[0]=0xa7;incoming[1]=1;incoming[2]=1;incoming[3]=2;
    put(incoming+4,0x43594231,4);put(incoming+8,sf,4);put(incoming+12,tx,5);put(incoming+17,12345,8);
    put(incoming+30,10,2);put(incoming+32,500,2);put(incoming+44,14,2);incoming_n=46;
    if(sf) { incoming[34]=1;put(incoming+35,sf-1,4);put(incoming+39,prior_tx,5);
        put(incoming+25,2,4);put(incoming+46,sf-1,4);put(incoming+50,rx_our_response,5);incoming_n+=9; }
    incoming_time=(tx+1500)&MASK;now=incoming_time;network_service();
}
int main(void)
{
    char *bad[]={"configure","2","30","3","100","43594231"};assert(network_command(6,bad)==-EINVAL);
    char *configure[]={"configure","2","2","10","500","43594231"};assert(network_command(6,configure)==0);
    char *start[]={"start","experimental"};assert(network_command(2,start)==0);assert(network_running() && phy);
    const uint64_t first=MASK-200*T;
    uint64_t prior=0,last_our=0;
    for(uint32_t sf=0;sf<3;++sf) {
        uint64_t tx=(first+sf*500*T)&MASK;
        beacon(sf,tx,prior,(last_our+1500)&MASK);
        now=(incoming_time+7*T)&MASK;network_service();
        assert(transmissions==sf+1);last_our=our_tx;prior=tx;
    }
    assert(transmitted_n>46 && transmitted[29]==1);
    /* One timestamp receipt precedes the completed distance record. */
    size_t report=46+9;assert(transmitted[report]==1);
    uint32_t mm=get(transmitted+report+5,4);assert(mm>=7030 && mm<=7040);
    unsigned before=transmissions;
    now=(now+1000*T)&MASK;network_service();assert(transmissions==before);
    beacon(0,(now+20*T)&MASK,0,0);
    now=(incoming_time+7*T)&MASK;network_service();assert(transmissions==before+1);
    assert(transmitted[29]==0); /* Restart never reuses an old distance. */
    ota=true;network_service();assert(!network_running() && !phy);
    ota=false;now=1000*T;
    char *coordinator[]={"configure","1","2","10","500","43594231"};
    assert(network_command(6,coordinator)==0);assert(network_command(2,start)==0);
    before=transmissions;
    for(unsigned frame=0;frame<5;++frame) {
        now=(1000+7+frame*500)*T;network_service();
        assert(transmissions==before+frame+1);
        assert(get(transmitted+8,4)==frame);
    }
    network_stop();
    puts("experimental network pipeline, coordinator deadlines, rollover and sync-loss tests passed");return 0;
}
