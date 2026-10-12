#ifndef PCB_TAG_TABLE_H
#define PCB_TAG_TABLE_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#ifndef MAX_TAGS
#define MAX_TAGS 8
#endif
#define TAG_STALE_MS 3000U
typedef struct {
    uint16_t id;
    uint32_t attempts,success,failures,timeouts,packet_errors,last_seen,first_attempt;
    double latest,sum,min,max;
} tag_state_t;
typedef struct { tag_state_t tags[MAX_TAGS]; size_t count,cursor; } tag_table_t;
int tag_add(tag_table_t *table,uint16_t id,uint16_t local_id);
tag_state_t *tag_next(tag_table_t *table);
void tag_record(tag_state_t *tag,uint32_t now,int rc,double distance);
bool tag_active(const tag_state_t *tag,uint32_t now);
#endif
