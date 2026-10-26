#ifndef UWB_SWARM_H
#define UWB_SWARM_H
#include <stdbool.h>
#include <stdint.h>
void swarm_wake(void);
void swarm_timer_init(void);
void swarm_timer_arm(uint32_t us);
void swarm_wait(void);
bool swarm_running(void);
void swarm_stop(void);
void swarm_service(void);
int swarm_command(int argc,char **argv);
#endif
