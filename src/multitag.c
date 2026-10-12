#include "validation.h"
#include "uwb_app.h"
#include "radio.h"
#include "tag_table.h"
#include "ranging.h"
#include <stdlib.h>
#include <string.h>

static tag_table_t table;
static bool running;
static uint32_t next_ms,start_ms;
bool multitag_running(void) { return running; }
void multitag_stop(void) { running=false; }
void multitag_service(void)
{
    if (!running || (int32_t)(k_uptime_get_32()-next_ms)<0) return;
    tag_state_t *tag=tag_next(&table);
    if (!tag) { running=false; return; }
    double distance=0;
    int rc=uwb_measure(tag->id,&distance);
    uint32_t now=k_uptime_get_32(); tag_record(tag,now,rc,distance);
    /* Sequential, no overlapping requests. Guard after completion/timeout. */
    next_ms=now+20;
    if (!range_retryable(rc)) {
        running=false; out("MULTITAG stopped rc=%d",rc);
    }
}
static void status(void)
{
    uint32_t now=k_uptime_get_32(),active=0;
    uint64_t attempts=0,success=0,timeouts=0,errors=0;
    out("MULTITAG running=%u configured=%u max=%u stale_after=%u ms",running,(unsigned)table.count,MAX_TAGS,TAG_STALE_MS);
    for (size_t i=0;i<table.count;++i) {
        const tag_state_t *t=&table.tags[i];
        attempts+=t->attempts; success+=t->success; timeouts+=t->timeouts; errors+=t->packet_errors;
        active+=tag_active(t,now);
        if (t->success) out("TAG %04x distance=%.4f m age=%u ms active=%u",t->id,t->latest,now-t->last_seen,tag_active(t,now));
        else out("TAG %04x distance=NOT_MEASURED age=N/A active=0",t->id);
        if (t->success) out("TAG %04x min=%.4f max=%.4f mean=%.4f m",t->id,t->min,t->max,t->sum/t->success);
        uint32_t elapsed=now-t->first_attempt;
        out("TAG %04x attempts=%u success=%u failed=%u timeout=%u packet_error=%u success_pct=%.1f rate=%.2f Hz",t->id,t->attempts,t->success,t->failures,t->timeouts,t->packet_errors,t->attempts?100.0*t->success/t->attempts:0.0,t->attempts && elapsed?1000.0*t->success/elapsed:0.0);
    }
    /* This toolchain's nano printf omits ll conversions. These sums of eight
     * uint32_t counters are exactly representable in double (below 2^53). */
    out("MULTITAG active=%u attempts=%.0f success=%.0f timeout=%.0f packet_error=%.0f rate=%.2f Hz",active,(double)attempts,(double)success,(double)timeouts,(double)errors,now-start_ms?1000.0*(double)success/(now-start_ms):0.0);
    out("MULTITAG radio utilization/collision count NOT_MEASURED (RX errors are not proof of collisions)");
}
int multitag_command(int argc,char **argv)
{
    if (!argc) return -EINVAL;
    if ((!strcmp(argv[0],"status") || !strcmp(argv[0],"stats")) && argc==1) { status(); return 0; }
    if (!strcmp(argv[0],"stop") && argc==1) { multitag_stop(); return radio_stats()->ready?radio_stop():0; }
    if (uwb_running() || ota_connected()) return -EBUSY;
    if (!strcmp(argv[0],"clear") && argc==1) { memset(&table,0,sizeof(table)); start_ms=k_uptime_get_32(); return 0; }
    if (!strcmp(argv[0],"add") && argc==2) {
        if (!uwb_local_id()) return -EACCES;
        char *end; errno=0;
        if (!*argv[1] || *argv[1]=='-' || *argv[1]=='+') return -EINVAL;
        unsigned long id=strtoul(argv[1],&end,16);
        if (errno || *end || id>0xfffe) return -EINVAL;
        return tag_add(&table,(uint16_t)id,uwb_local_id());
    }
    if (!strcmp(argv[0],"start") && argc==1) {
        if (!uwb_is_anchor() || !uwb_local_id() || !table.count || !radio_stats()->ready || !radio_stats()->calibrated) return -EACCES;
        for (size_t i=0;i<table.count;++i) if (table.tags[i].id==uwb_local_id()) return -EINVAL;
        running=true; next_ms=k_uptime_get_32(); if (!start_ms) start_ms=next_ms;
        out("MULTITAG sequential anchor polling started; tags use uwb continuous"); return 0;
    }
    return -EINVAL;
}
