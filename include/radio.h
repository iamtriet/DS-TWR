#ifndef PCB_RADIO_H
#define PCB_RADIO_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define RADIO_FRAME_MAX 512
typedef struct {
    uint32_t tx,rx,crc_errors,rx_errors,tx_errors,timeouts;
    uint32_t last_ms,device_id;
    uint16_t last_length;
    bool ready,calibrated;
    int error;
} radio_stats_t;
bool radio_busy(void);
#ifdef SWARM_RANGING
void radio_port_fast(bool fast);
int radio_swarm_arm(const uint8_t *data,size_t length,uint64_t raw);
int radio_swarm_complete(uint64_t *actual);
void radio_swarm_cancel(void);
uint32_t radio_swarm_prepare_us(void);
uint32_t radio_swarm_rx_us(void);
void radio_swarm_setup(size_t length);
int radio_swarm_rx(uint8_t *data,size_t capacity,size_t *length,uint64_t *timestamp,uint8_t *quality);
#endif
#ifdef REFERENCE_RANGING
int radio_reference_phy(void);
int radio_reference_acquire(void);
void radio_reference_release(void);
#endif
int radio_init(void);
int radio_calibrate(uint16_t tx_delay,uint16_t rx_delay,uint32_t power,uint8_t pg_delay);
int radio_tx(const uint8_t *data,size_t len,uint64_t *timestamp,bool response_expected);
/* Experimental primitive; raw device time, not antenna-adjusted TX time. */
int radio_tx_delayed(const uint8_t *data,size_t len,uint64_t raw_time,uint64_t *actual_timestamp,bool response_expected);
uint64_t radio_system_time(void);
uint16_t radio_tx_antenna_delay(void);
int radio_network_phy(bool enable);
/* Bounded poll that leaves RX armed between calls; -EAGAIN means no frame yet. */
int radio_network_rx(uint8_t *data,size_t capacity,size_t *length,uint64_t *timestamp);
int radio_rx(uint8_t *data,size_t capacity,size_t *length,uint64_t *timestamp,uint32_t timeout_ms,bool already_enabled);
int radio_stop(void);
void radio_status(void);
const radio_stats_t *radio_stats(void);
int radio_port_init(void);
int radio_port_error(void);
uint32_t radio_irq_count(void);
void validation_driver_fault(void);
extern struct dwt_probe_s radio_probe;
#endif
