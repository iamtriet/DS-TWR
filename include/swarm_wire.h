#ifndef SWARM_WIRE_H
#define SWARM_WIRE_H
#include <stdint.h>
#include <stddef.h>
#define SWARM_TIMER_MODULUS UINT64_C(0x10000000000)
#define SWARM_TIMER_MASK (SWARM_TIMER_MODULUS-1)
#pragma pack(push,1)
/* IEEE 802.15.4 data MAC: short source/destination, PAN compression,
 * broadcast destination 0xffff, no ACK. DW3110 appends the hardware FCS.
 * Extended PHR allows the complete 30-node application payload. */
typedef struct {
    uint16_t frame_control; /* little-endian 0x8841 */
    uint8_t sequence;
    uint16_t pan_id;
    uint16_t destination;
    uint16_t source;
} swarm_mac_t;
typedef struct {
    uint8_t magic[2],version,node_id,node_count,master_id;
    uint16_t sequence;
    uint32_t network_id,boot_nonce,master_term;
    uint8_t tx_timestamp[5];
    uint16_t confirmed_sequence;
    uint8_t confirmed_tx[5];
    uint32_t valid_peers;
    /* flags: bit0 previous TX confirmed, bit1 fresh LIS2DH12, bit2 motion, bit3 BNO085 source (requires bit1).
     * optional bytes: acceleration delta mg (LIS) or linear norm mg (BNO), quiet/20ms, age/10ms. */
    uint8_t flags,reserved[3];
} swarm_header_t;
typedef struct {
    uint32_t reply_time;
    uint16_t rx_sequence; /* Disambiguates missed packets and sequence wrap. */
    uint8_t fpp_rxp_diff; /* whole dB; 255 explicitly means unavailable */
} swarm_peer_t;
typedef struct {
    swarm_mac_t mac;
    swarm_header_t header;
    swarm_peer_t peers[29]; /* ascending node IDs, excluding this sender */
} swarm_frame_t;
#pragma pack(pop)
_Static_assert(sizeof(swarm_mac_t)==9,"MAC layout");
_Static_assert(sizeof(swarm_header_t)==40,"Swarm header layout");
_Static_assert(sizeof(swarm_frame_t)==252,"Extended frame layout");
static inline uint64_t swarm_elapsed40(uint64_t later,uint64_t earlier)
{ return (later-earlier)&SWARM_TIMER_MASK; }
static inline int64_t swarm_difference40(uint64_t a,uint64_t b)
{ return (int64_t)(((a-b)+(SWARM_TIMER_MODULUS>>1))&SWARM_TIMER_MASK)-(int64_t)(SWARM_TIMER_MODULUS>>1); }
static inline uint64_t swarm_delayed_raw(uint64_t requested)
{ return requested&SWARM_TIMER_MASK&~UINT64_C(0x1ff); }
static inline uint64_t swarm_predicted_tx(uint64_t requested,uint16_t tx_ant_dly)
{ return (swarm_delayed_raw(requested)+tx_ant_dly)&SWARM_TIMER_MASK; }
#endif
