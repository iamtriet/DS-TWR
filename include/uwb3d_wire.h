#ifndef UWB3D_WIRE_H
#define UWB3D_WIRE_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
/* U3D v1 transport codec; UART bytes, not an over-air MAC frame. */
size_t u3d_encode(uint8_t type,uint32_t sequence,uint64_t time_us,
                  const uint8_t *payload,size_t length,uint8_t *out,size_t capacity);
size_t u3d_range(uint16_t source,uint16_t target,double meters,uint32_t measurement,
                 uint64_t time_us,uint32_t sequence,uint8_t *out,size_t capacity);
size_t u3d_discovery(uint16_t node,uint64_t uid,uint16_t patch,uint64_t time_us,
                     uint32_t sequence,uint8_t *out,size_t capacity);
uint64_t u3d_predicted_tx(uint64_t raw_time,uint16_t antenna_delay);
bool u3d_pipeline_distance(uint32_t poll_frame,uint32_t response_frame,uint32_t final_frame,
    uint8_t valid_mask,uint64_t pt,uint64_t pr,uint64_t rt,uint64_t rr,uint64_t ft,uint64_t fr,double *meters);
#endif
