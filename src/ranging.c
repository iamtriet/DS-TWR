/* Asymmetric double-sided TWR: POLL -> RESP -> FINAL -> timestamp REPORT.
 * All six values are actual adjusted hardware timestamps. No assumed turn time.
 * Fourth frame carries the responder's three timestamps, letting the anchor
 * compute DS-TWR without embedding a predicted final TX time in the FINAL. */
#include "validation.h"
#include <string.h>
#include "radio.h"
#include "ranging.h"
#include "uwb_frame.h"
#include "validation_math.h"
static uint32_t rejected;
bool range_retryable(int rc)
{
    /* A bad/oversized frame or invalid sample rejects this exchange, not the
     * listening session. Hardware/configuration errors remain fatal. */
    return rc==0 || rc==-ETIMEDOUT || rc==-EBADMSG || rc==-EMSGSIZE || rc==-ERANGE;
}
uint32_t range_rejected_frames(void) { return rejected; }
static int send(uint8_t type,uint16_t src,uint16_t dst,uint32_t token,const uint8_t *payload,size_t length,uint64_t *ts,bool expect)
{
    uint8_t frame[RADIO_FRAME_MAX];
    size_t n=frame_build(frame,sizeof(frame),type,src,dst,token,payload,length);
    if (!n) return -EINVAL;
    /* Leave a small RX turnaround margin. Distance uses measured timestamps,
     * so this wait is not part of the distance model. UART logging is deferred. */
    if (type!=MSG_POLL) k_msleep(2);
    return radio_tx(frame,n,ts,expect);
}
static int receive(uint8_t type,uint16_t src,uint16_t dst,uint32_t token,bool any_token,uint8_t *payload,uint64_t *ts,uint32_t *received_token,uint32_t timeout,bool enabled,const uint16_t *sources,size_t count,uint16_t *actual)
{
    int64_t deadline=k_uptime_get()+timeout;
    do {
        uint8_t frame[RADIO_FRAME_MAX]; size_t len=0;
        int64_t remaining=deadline-k_uptime_get(); if (remaining<=0) break;
        int rc=radio_rx(frame,sizeof(frame),&len,ts,(uint32_t)remaining,enabled); enabled=false;
        if (rc) return rc;
        uwb_frame_t decoded;
        bool parsed=frame_parse(frame,len,&decoded), source_ok=false;
        if (parsed) {
            source_ok=src && decoded.source==src;
            for (size_t i=0;!src && i<count;++i) if (decoded.source==sources[i]) source_ok=true;
        }
        if (parsed && source_ok && decoded.type==type && decoded.destination==dst && (any_token || decoded.token==token)) {
            if (payload && decoded.payload_length) memcpy(payload,decoded.payload,decoded.payload_length);
            if (received_token) *received_token=decoded.token;
            if (actual) *actual=decoded.source;
            return 0;
        }
        ++rejected;
    } while (k_uptime_get()<deadline);
    return -ETIMEDOUT;
}
int range_initiate(uint16_t anchor,uint16_t tag,uint32_t token,double *meters)
{
    if (!meters) return -EINVAL;
    uint64_t poll_tx,resp_rx,final_tx,report_rx;
    uint8_t stamps[15];
    int rc=send(MSG_POLL,anchor,tag,token,NULL,0,&poll_tx,true);
    if (!rc) rc=receive(MSG_RESP,tag,anchor,token,false,NULL,&resp_rx,NULL,60,true,NULL,0,NULL);
    if (!rc) rc=send(MSG_FINAL,anchor,tag,token,NULL,0,&final_tx,true);
    if (!rc) rc=receive(MSG_REPORT,tag,anchor,token,false,stamps,&report_rx,NULL,60,true,NULL,0,NULL);
    if (!rc && !ds_distance(poll_tx,get_ts40(stamps),get_ts40(stamps+5),resp_rx,final_tx,get_ts40(stamps+10),meters)) rc=-ERANGE;
    int stop=radio_stop(); return rc ? rc : stop;
}
int range_respond_many(uint16_t tag,const uint16_t *anchors,size_t count,uint32_t *token,uint16_t *source)
{
    if (!token || !anchors || !count || count>12 || !source) return -EINVAL;
    uint16_t anchor=0;
    uint64_t poll_rx,resp_tx,final_rx,report_tx; uint8_t stamps[15];
    int rc=receive(MSG_POLL,0,tag,0,true,NULL,&poll_rx,token,20,false,anchors,count,&anchor);
    if (rc) return rc;
    *source=anchor;
    rc=send(MSG_RESP,tag,anchor,*token,NULL,0,&resp_tx,true);
    if (!rc) rc=receive(MSG_FINAL,anchor,tag,*token,false,NULL,&final_rx,NULL,60,true,NULL,0,NULL);
    if (!rc) {
        put_ts40(stamps,poll_rx); put_ts40(stamps+5,resp_tx); put_ts40(stamps+10,final_rx);
        rc=send(MSG_REPORT,tag,anchor,*token,stamps,sizeof(stamps),&report_tx,false);
    }
    int stop=radio_stop(); return rc ? rc : stop;
}

int range_respond(uint16_t tag,uint16_t anchor,uint32_t *token)
{
    uint16_t source;
    return range_respond_many(tag,&anchor,1,token,&source);
}
