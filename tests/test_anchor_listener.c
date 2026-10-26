#include "validation.h"
#include "radio.h"
#include "uwb_app.h"
#include "ranging.h"
#include <hal/nrf_gpio.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
mock_ficr_t mock_ficr;
static radio_stats_t stats={.ready=true,.calibrated=true};
static bool ota;
static unsigned initiated,responded;
uint32_t k_uptime_get_32(void) { return 1000; }
uint32_t k_cycle_get_32(void) { return 1; }
void out(const char *fmt,...) { (void)fmt; }
void uwb3d_publish(uint16_t a,uint16_t b,double m,uint32_t s) { (void)a;(void)b;(void)m;(void)s; }
bool ota_connected(void) { return ota; }
bool multitag_running(void) { return false; }
void multitag_stop(void) {}
bool network_running(void) { return false; }
void network_stop(void) {}
bool swarm_running(void) { return false; }
void swarm_stop(void) {}
void multitag_service(void) {}
const radio_stats_t *radio_stats(void) { return &stats; }
void radio_status(void) {}
int radio_stop(void) { return 0; }
int radio_init(void) { return 0; }
int radio_calibrate(uint16_t t,uint16_t r,uint32_t p,uint8_t g) { (void)t;(void)r;(void)p;(void)g;return 0; }
int radio_tx(const uint8_t *d,size_t l,uint64_t *t,bool e) { (void)d;(void)l;(void)t;(void)e;return 0; }
int radio_rx(uint8_t *d,size_t c,size_t *l,uint64_t *t,uint32_t m,bool e) { (void)d;(void)c;(void)l;(void)t;(void)m;(void)e;return -ETIMEDOUT; }
int range_initiate(uint16_t a,uint16_t t,uint32_t s,double *d) { assert(a==0xA001);assert(t!=a);(void)s;*d=1.;++initiated;return 0; }
int range_respond_many(uint16_t t,const uint16_t *a,size_t n,uint32_t *s,uint16_t *source) { assert(t==0xA001);assert(n==1 && a[0]==0xA002);(void)s;(void)source;++responded;return -ETIMEDOUT; }
int range_respond(uint16_t t,uint16_t a,uint32_t *s) { (void)t;(void)a;(void)s;return -ETIMEDOUT; }
uint32_t range_rejected_frames(void) { return 0; }
bool range_retryable(int r) { return r==0 || r==-ETIMEDOUT; }
static int cmd(char *a,char *b) { char *v[]={a,b};return uwb_command(b?2:1,v); }
int main(void) {
    assert(!cmd("id","A001")); assert(!cmd("peer","B001"));
    assert(cmd("listen",NULL)==-EINVAL);
    assert(!cmd("role","anchor")); assert(cmd("listen",NULL)==-EINVAL);
    assert(!cmd("anchors","A002")); assert(!cmd("listen",NULL));
    assert(uwb_is_anchor() && uwb_running());
    uwb_service(); assert(responded==1 && initiated==0);
    assert(!cmd("range","A002")); assert(initiated==1);
    uwb_service(); assert(responded==2 && uwb_is_anchor());
    assert(cmd("role","tag")==-EBUSY);
    ota=true; assert(cmd("range","A002")==-EBUSY);
    uwb_service(); assert(!uwb_running()); ota=false;
    assert(!cmd("continuous",NULL));
    assert(cmd("range","A002")==-EBUSY);
    uwb_service(); assert(initiated==2);
    uwb_stop_all(); assert(!uwb_running() && uwb_is_anchor());
    assert(!cmd("role","tag"));
    assert(cmd("range","A002")==-EINVAL);
    assert(!cmd("cooperate",NULL));
    assert(!uwb_is_anchor() && uwb_running());
    assert(!cmd("range","A002"));
    uwb_service(); assert(responded==3 && !uwb_is_anchor());
    ota=true; assert(cmd("range","A002")==-EBUSY); ota=false;
    uwb_stop_all(); assert(cmd("range","A002")==-EINVAL);
    puts("anchor listener role, scheduling and OTA guards: pass");
}
