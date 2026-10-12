#ifndef PCB_UWB_FRAME_H
#define PCB_UWB_FRAME_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define UWB_HEADER_SIZE 15
enum uwb_message { MSG_TEST=1,MSG_POLL,MSG_RESP,MSG_FINAL,MSG_REPORT };
typedef struct { uint16_t source,destination; uint32_t token; uint8_t type; const uint8_t *payload; size_t payload_length; } uwb_frame_t;
size_t frame_build(uint8_t *buf,size_t capacity,uint8_t type,uint16_t source,uint16_t destination,uint32_t token,const uint8_t *payload,size_t length);
bool frame_parse(const uint8_t *buf,size_t length,uwb_frame_t *frame);
void put_ts40(uint8_t *buf,uint64_t value);
uint64_t get_ts40(const uint8_t *buf);
#endif
