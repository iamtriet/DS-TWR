/* Zephyr 2.7 port for the Qorvo driver. The application thread is the sole
 * driver owner. GPIO ISR only increments a counter; it never accesses SPI. */
#include "validation.h"
#include "radio.h"
#include <drivers/spi.h>
#include <drivers/gpio.h>
#include <sys/atomic.h>
#include <deca_device_api.h>
#include <deca_interface.h>
#include <string.h>

static const struct device *spi,*gpio0,*gpio1;
static struct spi_cs_control cs;
static struct spi_config config;
static struct gpio_callback irq_cb;
static bool cb_added;
static atomic_t irq_count;
static int fault;
static uint8_t tx[1032],rx[1032];
static int latch(int rc) { if (rc && !fault) fault=rc; return rc; }
int radio_port_error(void) { return fault; }
void validation_driver_fault(void) { latch(-ETIMEDOUT); }
uint32_t radio_irq_count(void) { return (uint32_t)atomic_get(&irq_count); }
static void irq_event(const struct device *dev,struct gpio_callback *cb,uint32_t pins)
{ ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins); atomic_inc(&irq_count); }
static int transfer(uint16_t h,const uint8_t *header,uint16_t length,const uint8_t *body,uint8_t *data,bool crc,uint8_t check)
{
    if (data) memset(data,0,length);
    if (fault) return fault;
    size_t total=(size_t)h+length+(crc?1:0);
    if (!header || !h || h>4 || total>sizeof(tx) || (!data && length && !body)) return latch(-EMSGSIZE);
    memset(tx,0,total); memcpy(tx,header,h);
    if (body) memcpy(tx+h,body,length);
    if (crc) tx[h+length]=check;
    struct spi_buf tb={.buf=tx,.len=total}, rb={.buf=rx,.len=total};
    struct spi_buf_set ts={.buffers=&tb,.count=1},rs={.buffers=&rb,.count=1};
    int rc=spi_transceive(spi,&config,&ts,data?&rs:NULL);
    if (!rc && data) memcpy(data,rx+h,length);
    return latch(rc);
}
static int32_t read_spi(uint16_t h,uint8_t *header,uint16_t n,uint8_t *data) { return transfer(h,header,n,NULL,data,false,0); }
static int32_t write_spi(uint16_t h,const uint8_t *header,uint16_t n,const uint8_t *data) { return transfer(h,header,n,data,NULL,false,0); }
static int32_t write_crc(uint16_t h,const uint8_t *header,uint16_t n,const uint8_t *data,uint8_t crc) { return transfer(h,header,n,data,NULL,true,crc); }
static void speed(void) { config.frequency=2000000; }
static void wakeup(void)
{
    if (fault) return;
    if (latch(gpio_pin_set_raw(gpio0,15,1))) return;
    k_msleep(1);
    if (latch(gpio_pin_set_raw(gpio0,15,0))) return;
    k_msleep(2);
}
static const struct dwt_spi_s spi_ops={read_spi,write_spi,write_crc,speed,speed};
extern const struct dwt_driver_s dw3000_driver;
static const struct dwt_driver_s *drivers[]={&dw3000_driver};
struct dwt_probe_s radio_probe={.dw=NULL,.spi=(void *)&spi_ops,.wakeup_device_with_io=wakeup,.driver_list=(struct dwt_driver_s **)drivers,.dw_driver_num=1};
decaIrqStatus_t decamutexon(void) { return 0; }
void decamutexoff(decaIrqStatus_t state) { ARG_UNUSED(state); }
void deca_sleep(unsigned int ms) { k_msleep(ms); }
void deca_usleep(unsigned long us) { k_busy_wait(us); }
int radio_port_init(void)
{
    fault=0;
    spi=DEVICE_DT_GET(DT_NODELABEL(spi3)); gpio0=DEVICE_DT_GET(DT_NODELABEL(gpio0)); gpio1=DEVICE_DT_GET(DT_NODELABEL(gpio1));
    if (!device_is_ready(spi) || !device_is_ready(gpio0) || !device_is_ready(gpio1)) return latch(-ENODEV);
    cs.gpio_dev=gpio1; cs.gpio_pin=6; cs.gpio_dt_flags=GPIO_ACTIVE_LOW; cs.delay=0;
    config=(struct spi_config){.frequency=2000000,.operation=SPI_OP_MODE_MASTER|SPI_WORD_SET(8)|SPI_TRANSFER_MSB,.slave=0,.cs=&cs};
    int rc=gpio_pin_configure(gpio0,15,GPIO_OUTPUT_LOW);
    if (!rc) rc=gpio_pin_configure(gpio1,2,GPIO_INPUT|GPIO_PULL_DOWN);
    if (!rc && !cb_added) {
        gpio_init_callback(&irq_cb,irq_event,BIT(2));
        rc=gpio_add_callback(gpio1,&irq_cb); if (!rc) cb_added=true;
    }
    if (!rc) rc=gpio_pin_interrupt_configure(gpio1,2,GPIO_INT_EDGE_RISING);
    /* RSTn is open-drain: assert low, release to input. Never drive it high. */
    if (!rc) rc=gpio_pin_configure(gpio0,25,GPIO_OUTPUT_LOW);
    if (rc) return latch(rc);
    k_msleep(2);
    rc=gpio_pin_configure(gpio0,25,GPIO_INPUT);
    k_msleep(5); return latch(rc);
}
