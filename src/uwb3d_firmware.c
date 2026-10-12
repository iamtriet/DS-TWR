#include "validation.h"
#include "uwb3d_wire.h"
#include <hal/nrf_gpio.h>
void uwb3d_publish(uint16_t source,uint16_t target,double meters,uint32_t measurement)
{
    static uint32_t sequence;
    uint8_t packet[64];uint64_t stamp=(uint64_t)k_uptime_get()*1000U;
    uint64_t uid=((uint64_t)NRF_FICR->DEVICEID[1]<<32)|NRF_FICR->DEVICEID[0];
    size_t n=u3d_discovery(source,uid,FW_VERSION_PATCH,stamp,sequence++,packet,sizeof(packet));
    if(n) console_binary(packet,n);
    n=u3d_range(source,target,meters,measurement,stamp,sequence++,packet,sizeof(packet));
    if(n) console_binary(packet,n);
}
