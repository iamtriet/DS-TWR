/* Manual daughterboard outputs. Never commandeer DW3110 GPIO3 (RGB R1). */
#include "validation.h"
#include <drivers/gpio.h>
#include <string.h>
static const uint8_t pins[]={30,2,31,27,7,23};
static bool initialized;
static uint8_t levels[6];
static const char *profile="off";
static int apply(const uint8_t *values)
{
    const struct device *dev=DEVICE_DT_GET(DT_NODELABEL(gpio0));
    if(!device_is_ready(dev))return -ENODEV;
    if(!initialized){
        for(unsigned i=0;i<ARRAY_SIZE(pins);i++){
            int rc=gpio_pin_configure(dev,pins[i],GPIO_OUTPUT_LOW);
            if(rc)return rc;
        }
        initialized=true;
    }
    for(unsigned i=0;i<ARRAY_SIZE(pins);i++){
        int rc=gpio_pin_set_raw(dev,pins[i],values[i]);
        if(rc){
            for(unsigned j=0;j<ARRAY_SIZE(pins);j++)gpio_pin_set_raw(dev,pins[j],0);
            memset(levels,0,sizeof(levels));profile="off";return rc;
        }
    }
    memcpy(levels,values,sizeof(levels));return 0;
}
int aux_led_command(int argc,char **argv)
{
    uint8_t values[6]={0};int rc;
    if(argc==1 && !strcmp(argv[0],"status")){
        out("AUX_LED v=1 profile=%s UV_L=%u UV_R=%u RGB_R1=unsupported RGB_G1=%u RGB_B1=%u RGB_R2=%u RGB_G2=%u RGB_B2=%u",
            profile,levels[0],levels[1],levels[0],levels[2],levels[3],levels[4],levels[5]);return 0;
    }
    if(argc==1 && !strcmp(argv[0],"off")){
        rc=apply(values);if(!rc)profile="off";return rc;
    }
    bool uv=argc==3 && !strcmp(argv[0],"uv");
    bool rgb=argc==7 && !strcmp(argv[0],"rgb");
    if(!uv && !rgb)return -EINVAL;
    for(int i=1;i<argc;i++)if(strcmp(argv[i],"0") && strcmp(argv[i],"1"))return -EINVAL;
    if(rgb && !strcmp(argv[1],"1"))return -ENOTSUP;
    if(uv){values[0]=argv[1][0]-'0';values[1]=argv[2][0]-'0';}
    else {values[0]=argv[2][0]-'0';for(int i=3;i<7;i++)values[i-1]=argv[i][0]-'0';}
    rc=apply(values);if(!rc)profile=uv?"uv":"rgb";return rc;
}
