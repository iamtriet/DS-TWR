#include <stdint.h>
#include <stdbool.h>
uint32_t k_uptime_get_32(void);
uint32_t k_cycle_get_32(void);
void out(const char *fmt, ...);
void uwb3d_publish(uint16_t source,uint16_t target,double meters,uint32_t sequence);
int uwb_command(int argc,char **argv);
void uwb_service(void);
