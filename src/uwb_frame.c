#include "uwb_frame.h"
#include <string.h>
static void put16(uint8_t *b,uint16_t v) { b[0]=(uint8_t)v; b[1]=(uint8_t)(v>>8); }
static uint16_t get16(const uint8_t *b) { return (uint16_t)b[0]|((uint16_t)b[1]<<8); }
void put_ts40(uint8_t *b,uint64_t v) { for (int i=0;i<5;++i) b[i]=(uint8_t)(v>>(8*i)); }
uint64_t get_ts40(const uint8_t *b) { uint64_t v=0; for (int i=4;i>=0;--i) v=(v<<8)|b[i]; return v; }
size_t frame_build(uint8_t *buf,size_t capacity,uint8_t type,uint16_t source,uint16_t destination,uint32_t token,const uint8_t *payload,size_t length)
{
    if (length!=(type==MSG_REPORT ? 15U : 0U)) return 0;
    if (!buf || capacity<UWB_HEADER_SIZE || length>capacity-UWB_HEADER_SIZE || (length && !payload) || !source || source==0xffff || !destination || destination==0xffff || type<MSG_TEST || type>MSG_REPORT) return 0;
    /* IEEE 802.15.4 data, PAN compression, short addresses, no ACK request.
     * Byte serialization avoids unaligned packed structs and native endian assumptions. */
    buf[0]=0x41; buf[1]=0x88; buf[2]=(uint8_t)token;
    put16(buf+3,0xdeca); put16(buf+5,destination); put16(buf+7,source);
    buf[9]=type; buf[10]=1; /* Validation protocol version. */
    for (int i=0;i<4;++i) buf[11+i]=(uint8_t)(token>>(8*i));
    if (length) memcpy(buf+UWB_HEADER_SIZE,payload,length);
    return UWB_HEADER_SIZE+length;
}
bool frame_parse(const uint8_t *buf,size_t length,uwb_frame_t *frame)
{
    if (!buf || !frame || length<UWB_HEADER_SIZE || buf[0]!=0x41 || buf[1]!=0x88 || get16(buf+3)!=0xdeca || buf[10]!=1 || buf[9]<MSG_TEST || buf[9]>MSG_REPORT) return false;
    frame->source=get16(buf+7); frame->destination=get16(buf+5); frame->type=buf[9]; frame->token=0;
    for (int i=0;i<4;++i) frame->token|=(uint32_t)buf[11+i]<<(8*i);
    if (!frame->source || frame->source==0xffff || !frame->destination || frame->destination==0xffff || buf[2]!=(uint8_t)frame->token) return false;
    frame->payload=buf+UWB_HEADER_SIZE; frame->payload_length=length-UWB_HEADER_SIZE;
    return frame->payload_length==(frame->type==MSG_REPORT ? 15U : 0U);
}
