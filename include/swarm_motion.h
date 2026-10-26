#ifndef SWARM_MOTION_H
#define SWARM_MOTION_H
#include <stdint.h>
void swarm_motion_init(void);
void swarm_motion_poll(void);
void swarm_motion_encode(uint8_t *header);
void swarm_motion_status(void);
#endif
