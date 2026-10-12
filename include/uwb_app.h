#ifndef PCB_UWB_APP_H
#define PCB_UWB_APP_H
#include <stdint.h>
#include <stdbool.h>
void uwb_stop_all(void);
bool uwb_is_anchor(void);
bool uwb_running(void);
bool uwb_configured(void);
uint16_t uwb_local_id(void);
int uwb_measure(uint16_t tag,double *distance);
void uwb_publish(uint16_t tag,double meters,uint32_t token);
void multitag_service(void);
bool multitag_running(void);
void multitag_stop(void);
bool ota_connected(void);
#endif
