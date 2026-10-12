#ifndef UWB_REFERENCE_H
#define UWB_REFERENCE_H
#include <stdbool.h>
#ifdef REFERENCE_RANGING
bool reference_running(void);
bool reference_anchor(void);
void reference_stop(void);
void reference_service(void);
int reference_command(int argc,char **argv);
#else
static inline bool reference_running(void) { return false; }
static inline bool reference_anchor(void) { return false; }
static inline void reference_stop(void) {}
static inline void reference_service(void) {}
static inline int reference_command(int argc,char **argv) { (void)argc;(void)argv;return -95; }
#endif
#endif
