#ifndef UWB_ALBIFACHE_H
#define UWB_ALBIFACHE_H
#include <stdbool.h>
#include <stdint.h>
/* albifache IEEE 802.15.4z TDMA DS-TWR integration.
 * Replaces legacy DS-TWR, uwb_network, multitag, and reference modules.
 * All 6 nodes are equal; PAN coordinator rotates every ROTATION_INTERVAL
 * superframes so that no single node is a permanent master. */
bool albifache_running(void);
bool albifache_is_anchor(void);
uint16_t albifache_local_id(void);
void albifache_service(void);
void albifache_stop(void);
int albifache_command(int argc, char **argv);
void albifache_status(void);
#endif
