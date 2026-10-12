#ifndef UWB_NETWORK_H
#define UWB_NETWORK_H
#include <stdbool.h>
bool network_running(void);
void network_stop(void);
void network_service(void);
int network_command(int argc,char **argv);
#endif
