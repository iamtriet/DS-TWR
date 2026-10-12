#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "validation_math.h"
#include "uwb_frame.h"
#include "tag_table.h"
#include "radio.h"
#include "ranging.h"

static int64_t clock_ms;
static int mode,step,stops;
static bool tag_mode;
static uint16_t test_anchor=0xa001;
static bool wrong_final;
static uint64_t pt=100000, pr=1100000, rt=2100000, rr=1102000, ft=3102000, fr=4102000;
int64_t k_uptime_get(void) { return clock_ms++; }
void k_msleep(int32_t ms) { clock_ms+=ms; }
int radio_stop(void) { ++stops; return 0; }
int radio_tx(const uint8_t *d,size_t n,uint64_t *ts,bool expect)
{
    uwb_frame_t f;
    assert(frame_parse(d,n,&f));
    if (tag_mode) {
        assert(f.source==0xb001 && f.destination==test_anchor && f.token==42);
        if (!step) { assert(f.type==MSG_RESP && expect); *ts=rt; }
        else {
            assert(f.type==MSG_REPORT && !expect);
            assert(get_ts40(f.payload)==pr && get_ts40(f.payload+5)==rt && get_ts40(f.payload+10)==fr);
            *ts=fr+10000;
        }
        ++step; return mode==3 ? -EIO : 0;
    }
    assert(f.source==0xa001 && f.destination==0xb001 && f.token==42);
    assert(expect);
    if (step==0) { assert(f.type==MSG_POLL); *ts=pt; }
    else { assert(f.type==MSG_FINAL); *ts=ft; }
    ++step; return mode==3 ? -EIO : 0;
}
int radio_rx(uint8_t *d,size_t cap,size_t *n,uint64_t *ts,uint32_t timeout,bool enabled)
{
    (void)enabled; assert(timeout>0 && timeout<=60);
    if (mode==1) return -ETIMEDOUT;
    if (mode==4) return -EBADMSG;
    if (tag_mode) {
        *n=frame_build(d,cap,step?MSG_FINAL:MSG_POLL,(step && wrong_final)?0xa005:test_anchor,mode==2?0xb002:0xb001,42,NULL,0);
        *ts=step?fr:pr; return 0;
    }
    uint8_t payload[15]; put_ts40(payload,pr); put_ts40(payload+5,rt); put_ts40(payload+10,fr);
    uint8_t type=step==1?MSG_RESP:MSG_REPORT;
    *n=frame_build(d,cap,type,0xb001,0xa001,mode==2?43:42,type==MSG_REPORT?payload:NULL,type==MSG_REPORT?15:0);
    *ts=rr; return 0;
}
static void frames(void)
{
    uint8_t b[64], payload[15]; uwb_frame_t f;
    put_ts40(payload,UINT64_C(0xffffffffff)); assert(get_ts40(payload)==UINT64_C(0xffffffffff));
    memset(payload,0,sizeof(payload));
    size_t n=frame_build(b,sizeof(b),MSG_REPORT,0xa001,0xb001,0x12345678,payload,15);
    assert(n==30 && frame_parse(b,n,&f));
    assert(f.token==0x12345678 && f.source==0xa001 && f.destination==0xb001 && f.payload_length==15);
    for (size_t i=0;i<n;++i) assert(!frame_parse(b,i,&f));
    assert(!frame_parse(b,n+1,&f));
    b[0]^=1; assert(!frame_parse(b,n,&f)); b[0]^=1;
    b[2]^=1; assert(!frame_parse(b,n,&f));
    assert(!frame_build(b,10,MSG_POLL,1,2,42,NULL,0));
    assert(!frame_build(b,64,MSG_POLL,0,2,42,NULL,0));
}
static void math_tests(void)
{
    double d=0; const double unit=299702547.0/(499.2e6*128.0);
    // 1000 DW ticks flight time, independent clock offsets, asymmetric delays.
    assert(ds_distance(pt,pr,rt,rr,ft,fr,&d)); assert(fabs(d-1000*unit)<1e-8);
    uint64_t shift=UINT64_C(0xfffffff000);
    assert(ds_distance(pt+shift,pr+shift,rt+shift,rr+shift,ft+shift,fr+shift,&d));
    assert(fabs(d-1000*unit)<1e-8);
    // Responder 10 ppm faster; rounding error < one timestamp tick.
    assert(ds_distance(pt,pr*100001/100000,rt*100001/100000,rr,ft,fr*100001/100000,&d));
    assert(fabs(d-1000*unit)<unit);
    assert(!ds_distance(pt,pr,rt,pt,ft,fr,&d));
    assert(!ds_distance(pt,pr,rt,rr,rr-1,fr,&d));
    assert(!ds_distance(pt,pr,rt,rr,ft,fr,NULL));
    float rpy[3]; assert(quaternion_rpy(1,0,0,0,rpy));
    assert(fabs(rpy[0])+fabs(rpy[1])+fabs(rpy[2])<0.001);
    assert(quaternion_rpy(.70710678f,0,0,.70710678f,rpy)); assert(fabs(rpy[2]-90)<.001);
    assert(quaternion_rpy(.70710678f,0,.70710678f,0,rpy)); assert(isfinite(rpy[1]));
    assert(!quaternion_rpy(NAN,0,0,0,rpy)); assert(!quaternion_rpy(0,0,0,0,rpy));
}
static void tags(void)
{
    tag_table_t t={0}; assert(!tag_next(&t));
    assert(tag_add(&t,1,1)==-EINVAL); assert(tag_add(&t,0xffff,1)==-EINVAL);
    for (unsigned i=0;i<MAX_TAGS;++i) assert(!tag_add(&t,(uint16_t)(i+2),1));
    assert(tag_add(&t,2,1)==-EEXIST); assert(tag_add(&t,99,1)==-ENOSPC);
    for (unsigned i=0;i<MAX_TAGS*3;++i) assert(tag_next(&t)->id==i%MAX_TAGS+2);
    tag_state_t *s=&t.tags[0]; assert(!tag_active(s,100));
    tag_record(s,UINT32_MAX-10,0,2); assert(tag_active(s,20));
    assert(!tag_active(s,4000)); tag_record(s,30,0,4); tag_record(s,31,-ETIMEDOUT,0);
    tag_record(s,32,0,NAN); tag_record(s,33,-EBADMSG,0);
    assert(s->attempts==5 && s->success==2 && s->failures==3 && s->timeouts==1 && s->packet_errors==1);
    assert(s->min==2 && s->max==4 && s->sum==6 && s->latest==4 && s->last_seen==30);
}
static void protocol(void)
{
    double d;
    for (mode=0;mode<5;++mode) {
        clock_ms=0; step=0; stops=0;
        int rc=range_initiate(0xa001,0xb001,42,&d);
        if (mode==0) assert(rc==0 && fabs(d-4.69035)<.0001);
        if (mode==1 || mode==2) assert(rc==-ETIMEDOUT);
        if (mode==3) assert(rc==-EIO);
        if (mode==4) assert(rc==-EBADMSG);
        assert(stops==1);
    }
    assert(range_rejected_frames()>0);
    tag_mode=true;
    for (mode=0;mode<5;++mode) {
        clock_ms=0; step=0; stops=0; uint32_t token=UINT32_MAX;
        int rc=range_respond(0xb001,0xa001,&token);
        if (mode==0) assert(rc==0 && token==42 && stops==1);
        if (mode==1 || mode==2) assert(rc==-ETIMEDOUT && token==UINT32_MAX && stops==0);
        if (mode==3) assert(rc==-EIO && stops==1);
        if (mode==4) assert(rc==-EBADMSG && stops==0);
    }
}
static void retry_policy(void)
{
    assert(range_retryable(0));
    assert(range_retryable(-ETIMEDOUT));
    assert(range_retryable(-EBADMSG));
    assert(range_retryable(-EMSGSIZE));
    assert(range_retryable(-ERANGE));
    assert(!range_retryable(-EIO));
    assert(!range_retryable(-EACCES));
    assert(!range_retryable(-ENODEV));
    /* A receive fault must allow the next valid tag exchange to run. */
    tag_mode=true; mode=4; clock_ms=0; step=0; stops=0;
    uint32_t token=UINT32_MAX;
    int rc=range_respond(0xb001,0xa001,&token);
    assert(rc==-EBADMSG && range_retryable(rc));
    mode=0; clock_ms=0; step=0; stops=0;
    assert(range_respond(0xb001,0xa001,&token)==0 && token==42);
}
static void multi_anchor(void)
{
    const uint16_t allowed[]={0xa001,0xa002,0xa003,0xa004};
    uint16_t source=0; uint32_t token=0;
    tag_mode=true; mode=0;
    for (unsigned i=0;i<4;++i) {
        test_anchor=allowed[i]; clock_ms=0; step=0; stops=0;
        assert(range_respond_many(0xb001,allowed,4,&token,&source)==0);
        assert(source==test_anchor && token==42 && step==2);
    }
    test_anchor=0xa005; clock_ms=0; step=0;
    assert(range_respond_many(0xb001,allowed,4,&token,&source)==-ETIMEDOUT && step==0);
    test_anchor=0xa001; wrong_final=true; clock_ms=0; step=0;
    assert(range_respond_many(0xb001,allowed,4,&token,&source)==-ETIMEDOUT && step==1);
    wrong_final=false;
    assert(range_respond_many(0xb001,allowed,0,&token,&source)==-EINVAL);
}
int main(void) { frames(); math_tests(); tags(); protocol(); retry_policy(); multi_anchor(); puts("PASS: frame bounds, timestamps/wrap/skew, quaternion, tag scheduler/staleness, mocked anchor/tag exchanges, recoverable receive fault followed by valid exchange, fatal error policy"); return 0; }
