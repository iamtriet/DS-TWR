#ifndef REFERENCE_PORT_H
#define REFERENCE_PORT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <dw3000/dw3000_deca_regs.h>
void deca_usleep(unsigned long us);
bool reference_abort(void);
#endif
