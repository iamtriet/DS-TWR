#ifndef PCB_RANGING_H
#define PCB_RANGING_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
bool range_retryable(int rc);
int range_initiate(uint16_t anchor,uint16_t tag,uint32_t token,double *meters);
int range_respond_many(uint16_t tag,const uint16_t *anchors,size_t count,uint32_t *token,uint16_t *source);
int range_respond(uint16_t tag,uint16_t anchor,uint32_t *token);
uint32_t range_rejected_frames(void);
#endif
