#include "uwb3d_wire.h"
#include "validation_math.h"
#include <math.h>
#include <string.h>
static void put(uint8_t *p,uint64_t value,size_t n) { for(size_t i=0;i<n;++i) p[i]=(uint8_t)(value>>(8*i)); }
static uint32_t crc32(const uint8_t *p,size_t n)
{
    uint32_t crc=UINT32_MAX;
    for(size_t i=0;i<n;++i) { crc^=p[i]; for(int k=0;k<8;++k) crc=(crc>>1)^((0U-(crc&1U))&0xedb88320U); }
    return ~crc;
}
size_t u3d_encode(uint8_t type,uint32_t seq,uint64_t stamp,const uint8_t *payload,size_t n,uint8_t *out,size_t cap)
{
    uint8_t raw[148];
    if(!out || !type || type>10 || n>128 || (n && !payload) || cap<n+23) return 0;
    raw[0]=1;raw[1]=type;put(raw+2,n,2);put(raw+4,seq,4);put(raw+8,stamp,8);
    if(n) memcpy(raw+16,payload,n);
    put(raw+16+n,crc32(raw,16+n),4);
    /* Leading delimiter recovers from interspersed legacy ASCII diagnostics. */
    out[0]=0;size_t mark=1,pos=2;uint8_t code=1;
    for(size_t i=0;i<n+20;++i) {
        if(raw[i]) { out[pos++]=raw[i];++code; }
        else { out[mark]=code;mark=pos++;code=1; }
    }
    out[mark]=code;out[pos++]=0;return pos;
}
size_t u3d_range(uint16_t a,uint16_t b,double meters,uint32_t measurement,uint64_t stamp,uint32_t seq,uint8_t *out,size_t cap)
{
    if(!a || !b || a==UINT16_MAX || b==UINT16_MAX || a==b || !isfinite(meters) || meters<=0 || meters>1000) return 0;
    uint8_t payload[23];put(payload,a,2);put(payload+2,b,2);put(payload+4,(uint32_t)llround(meters*1000),4);
    put(payload+8,measurement,4);put(payload+12,stamp,8);put(payload+20,65535,2);payload[22]=1;
    return u3d_encode(3,seq,stamp,payload,sizeof(payload),out,cap);
}
size_t u3d_discovery(uint16_t node,uint64_t uid,uint16_t patch,uint64_t stamp,uint32_t seq,uint8_t *out,size_t cap)
{
    if(!node || node==UINT16_MAX)return 0;
    uint8_t payload[20]={0};put(payload,node,2);put(payload+2,uid,8);put(payload+10,1,2);put(payload+14,patch,2);
    put(payload+16,1,4); /* capability bit 0: UART range export, NOT BP TDMA */
    return u3d_encode(1,seq,stamp,payload,sizeof(payload),out,cap);
}
uint64_t u3d_predicted_tx(uint64_t raw,uint16_t delay)
{ return ((raw&UINT64_C(0xfffffffe00))+delay)&UINT64_C(0xffffffffff); }
bool u3d_pipeline_distance(uint32_t poll,uint32_t response,uint32_t final,uint8_t mask,
    uint64_t pt,uint64_t pr,uint64_t rt,uint64_t rr,uint64_t ft,uint64_t fr,double *meters)
{
    if(response!=poll || final!=(uint32_t)(poll+1U) || mask!=0x3f)return false;
    return ds_distance(pt,pr,rt,rr,ft,fr,meters);
}
