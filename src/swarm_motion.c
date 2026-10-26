#ifdef SWARM_RANGING
#include "validation.h"
#include "swarm_motion.h"
#include <drivers/i2c.h>
#include <stdlib.h>

/* LIS2DH12 on the module's existing I2C0 bus. No GPIO/pull-up changes.
 * ST LIS2DH12 datasheet: WHO_AM_I=0x33, 50 Hz, HR +/-2g, BDU.
 * Samples are taken only after our own TX, in a sparsely populated TDMA grid. */
static const struct device *bus;
static uint8_t address,score;
static bool ready,previous_valid,sample_valid;
static int16_t previous[3];
static uint32_t stamp,last_motion,samples,errors;
static int last_error;
static bool bno_sample;

void swarm_motion_init(void)
{
    ready=previous_valid=sample_valid=false;samples=errors=0;score=0;
    bno_sample=false;
    int bno_rc=imu_init();out("SWARM BNO085 init rc=%d",bno_rc);
    last_motion=k_uptime_get_32();bus=DEVICE_DT_GET(DT_NODELABEL(i2c0));
    if(!device_is_ready(bus)) { last_error=-ENODEV;return; }
    uint8_t who=0;
    for(address=0x18;address<=0x19;address++) {
        last_error=i2c_reg_read_byte(bus,address,0x0f,&who);
        if(!last_error && who==0x33)break;
    }
    if(address>0x19) { last_error=-ENODEV;return; }
    last_error=i2c_reg_write_byte(bus,address,0x23,0x88);
    if(!last_error)last_error=i2c_reg_write_byte(bus,address,0x20,0x47);
    ready=!last_error;
}
void swarm_motion_poll(void)
{
    float acceleration,rotation;uint32_t age;
    if(imu_motion_sample(&acceleration,&rotation,&age)) {
        uint32_t now=k_uptime_get_32();
        if(!sample_valid || acceleration>.18f || rotation>.12f)last_motion=now;
        unsigned mg=(unsigned)(acceleration*101.9716f);
        score=mg>255?255:mg;stamp=now-age;sample_valid=bno_sample=true;++samples;return;
    }
    bno_sample=false;
    if(!ready)return;
    uint8_t status,data[6];
    int rc=i2c_reg_read_byte(bus,address,0x27,&status);
    if(!rc && !(status&8))return;
    if(!rc)rc=i2c_burst_read(bus,address,0x28|0x80,data,sizeof(data));
    if(rc) {
        last_error=rc;++errors;ready=sample_valid=false;
        return; /* no repeated blocking I2C retries during ranging */
    }
    uint32_t now=k_uptime_get_32();unsigned peak=0;
    for(unsigned i=0;i<3;i++) {
        int16_t value=(int16_t)((uint16_t)data[2*i]|((uint16_t)data[2*i+1]<<8));
        value/=16; /* high resolution +/-2g: 1 mg/LSB */
        unsigned delta=abs((int)value-previous[i]);
        if(delta>peak)peak=delta;
        previous[i]=value;
    }
    if(!previous_valid || peak>15)last_motion=now;
    score=peak>255?255:peak;
    previous_valid=sample_valid=true;stamp=now;++samples;last_error=0;
}
void swarm_motion_encode(uint8_t *header)
{
    uint32_t now=k_uptime_get_32(),age=now-stamp,quiet=now-last_motion;
    if((!ready && !bno_sample) || !sample_valid || age>150)return;
    header[36]|=2;
    if(bno_sample)header[36]|=8;
    if(quiet<500)header[36]|=4;
    header[37]=score;
    header[38]=quiet/20>255?255:quiet/20;
    header[39]=age/10;
}
void swarm_motion_status(void)
{
    out("SWARM LIS2DH12 ready=%u address=0x%02x samples=%u errors=%u rc=%d score_mg=%u quiet_ms=%u age_ms=%u",
        ready,address,samples,errors,last_error,score,k_uptime_get_32()-last_motion,
        sample_valid?k_uptime_get_32()-stamp:UINT32_MAX);
}
#endif
