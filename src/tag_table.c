#include "tag_table.h"
#include <errno.h>
#include <math.h>
int tag_add(tag_table_t *table,uint16_t id,uint16_t local_id)
{
    if (!table || !id || id==0xffff || id==local_id) return -EINVAL;
    for (size_t i=0;i<table->count;++i) if (table->tags[i].id==id) return -EEXIST;
    if (table->count==MAX_TAGS) return -ENOSPC;
    table->tags[table->count++]=(tag_state_t){.id=id}; return 0;
}
tag_state_t *tag_next(tag_table_t *table)
{
    if (!table || !table->count) return NULL;
    tag_state_t *tag=&table->tags[table->cursor]; table->cursor=(table->cursor+1)%table->count; return tag;
}
void tag_record(tag_state_t *tag,uint32_t now,int rc,double distance)
{
    if (!tag) return;
    if (!tag->attempts) tag->first_attempt=now;
    ++tag->attempts;
    if (!rc && (!isfinite(distance) || distance<0 || distance>1000)) rc=-ERANGE;
    if (rc) {
        ++tag->failures;
        if (rc==-ETIMEDOUT) ++tag->timeouts;
        if (rc==-EBADMSG || rc==-EMSGSIZE) ++tag->packet_errors;
        return;
    }
    if (!tag->success || distance<tag->min) tag->min=distance;
    if (!tag->success || distance>tag->max) tag->max=distance;
    ++tag->success; tag->latest=distance; tag->sum+=distance; tag->last_seen=now;
}
bool tag_active(const tag_state_t *tag,uint32_t now)
{ return tag && tag->success && (uint32_t)(now-tag->last_seen)<TAG_STALE_MS; }
