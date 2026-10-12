#ifndef VALIDATION_MATH_H
#define VALIDATION_MATH_H
#include <stdbool.h>
#include <stdint.h>
bool quaternion_rpy(float w,float x,float y,float z,float rpy[3]);
bool ds_distance(uint64_t poll_tx,uint64_t poll_rx,uint64_t resp_tx,
                 uint64_t resp_rx,uint64_t final_tx,uint64_t final_rx,double *meters);
#endif
