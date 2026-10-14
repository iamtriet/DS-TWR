/**
 * @file dstwr_tdma.h
 * @brief Anchor-Free Cooperative Localization — 30-node Broadcast Asymmetric DS-TWR
 *
 * HARDWARE:  Qorvo DWM3001C (nRF52833 + DW3110)
 * PROTOCOL:  TDMA Superframe with 30 × 1.3 ms slots = 39 ms period (~25.6 Hz)
 *            Each node broadcasts ONE frame per slot containing:
 *              - Its own TX timestamp (predicted via Delayed TX)
 *              - Reply-time deltas to every other node it received last cycle
 *              - NLOS quality metric per neighbor
 *
 * RANGING:   Asymmetric Double-Sided Two-Way Ranging (DS-TWR)
 *            ToF = (Round1 * Round2 - Reply1 * Reply2) / (Round1 + Round2 + Reply1 + Reply2)
 *            Eliminates clock-drift error without requiring synchronized clocks.
 *
 * SYNC:      Dynamic Master (lowest active ID) broadcasts sync beacon at slot 0.
 *            Failover after 2 missed superframes.
 *
 * Copyright (c) 2026 Cyborg Project — All rights reserved.
 */

#ifndef DSTWR_TDMA_H
#define DSTWR_TDMA_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * SYSTEM CONSTANTS
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Number of mobile nodes in the swarm (Cyborg cockroaches). */
#define DSTWR_NUM_NODES          30

/** TDMA slot duration in microseconds (1.3 ms = 1300 µs). */
#define DSTWR_SLOT_US            1300

/** Superframe period = 30 × 1.3 ms = 39 ms → 25.6 Hz update rate. */
#define DSTWR_SUPERFRAME_US      (DSTWR_NUM_NODES * DSTWR_SLOT_US)

/** DW3110 master clock: 499.2 MHz × 128 = 63.8976 GHz → 1 tick ≈ 15.65 ps. */
#define DW_TICK_FREQ             UINT64_C(63897600000)

/** DW3110 40-bit timestamp counter modulus (wraps at 2^40). */
#define DW_TIMER_MASK            UINT64_C(0xFFFFFFFFFF)
#define DW_TIMER_MODULUS         UINT64_C(0x10000000000)
#define DW_TIMER_HALF            UINT64_C(0x8000000000)

/** DW3110 ticks per microsecond: 63897600 ticks/ms / 1000 = 63897.6 ticks/µs. */
#define DW_TICKS_PER_US          UINT64_C(63898)

/** DW3110 ticks per millisecond: 63,897,600. */
#define DW_TICKS_PER_MS          UINT64_C(63897600)

/** Slot duration in DW ticks: 1300 µs × 63897.6 ≈ 83,066,880 ticks. */
#define DSTWR_SLOT_TICKS         (DSTWR_SLOT_US * DW_TICKS_PER_US)

/** Superframe duration in DW ticks. */
#define DSTWR_SUPERFRAME_TICKS   (DSTWR_NUM_NODES * DSTWR_SLOT_TICKS)

/** Guard time before TX to arm Delayed TX (200 µs). */
#define DSTWR_GUARD_TICKS        (200 * DW_TICKS_PER_US)

/** Failover timeout: 2 missed superframes. */
#define DSTWR_FAILOVER_COUNT     2

/** Speed of light adjusted for DW3110 tick period (m/tick). */
#define SPEED_OF_LIGHT_M_PER_TICK  (299702547.0 / (499.2e6 * 128.0))

/** Maximum reply_time value that maps to 1000 m (sanity bound). */
#define DSTWR_REPLY_TIME_MAX     UINT32_MAX

/** Antenna delay bias calibrated on cockroach body (ticks).
 *  Must be measured per-unit during production calibration.
 *  Default: 16436 ticks ≈ 257.0 ns for DWM3001C on CH5/PRF64. */
#define DSTWR_ANT_DLY_DEFAULT    16436

/** NLOS threshold: if (RX_total_power - first_path_power) > 6 dB → suspect NLOS. */
#define DSTWR_NLOS_THRESHOLD_DB  6

/** PHY configuration: Channel 5, 6.8 Mbps, Preamble 128, Extended PHR. */
#define DSTWR_UWB_CHANNEL        5
#define DSTWR_UWB_PREAMBLE_LEN   128   /* DWT_PLEN_128 */
#define DSTWR_UWB_DATA_RATE      6800  /* kbps */
#define DSTWR_UWB_PAC_SIZE       8     /* DWT_PAC8 */

/** Maximum air-frame payload (Extended PHR allows up to 1023 bytes). */
#define DSTWR_MAX_PAYLOAD        512

/** Network magic number for frame validation. */
#define DSTWR_MAGIC              0xCB30  /* "CyBorg 30" */

/** Sniffer node ID (node 31, passive listener). */
#define DSTWR_SNIFFER_ID         0

/* ═══════════════════════════════════════════════════════════════════════════
 * OVER-THE-AIR PACKET STRUCTURE
 *
 * Each node broadcasts exactly one frame per TDMA slot.
 * Frame layout (packed, little-endian):
 *
 *   Offset  Size   Field
 *   ──────  ─────  ─────────────────────────────────────────────────────────
 *   0       2      magic       = 0xCB30 (frame type identifier)
 *   2       1      node_id     = 1..30 (transmitter)
 *   3       2      seq_num     = monotonic sequence counter
 *   5       5      tx_ts       = 40-bit predicted TX timestamp (DW ticks)
 *   10      1      master_id   = current dynamic master ID (0 = unknown)
 *   11      4      sf_counter  = superframe counter (from master)
 *   15      4      alive_mask  = bitmask of nodes heard in last superframe
 *                                (bit k = node k+1 is alive, k=0..29)
 *   19      1      n_replies   = number of valid reply_time entries following
 *   20      var    reply entries: for each valid neighbor j:
 *                    peer_id      (uint8_t)   = neighbor node ID
 *                    reply_time   (uint32_t)  = T_TX(self) - T_RX(from j), compressed
 *                    nlos_metric  (uint8_t)   = NLOS quality indicator (0=LOS, 255=blocked)
 *                  → 6 bytes per entry × up to 29 entries = 174 bytes max
 *
 *   Total max: 20 + 174 = 194 bytes (well within Extended PHR 1023 limit)
 *
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Single reply-time entry for one neighbor. */
#pragma pack(push, 1)
typedef struct {
    uint8_t  peer_id;       /**< Neighbor node ID (1..30). */
    uint32_t reply_time;    /**< T_TX(self) - T_RX(from peer), in DW ticks.
                                 Compressed to 32-bit by right-shifting 8 bits
                                 (resolution ~4 ns, max range ~17 seconds). */
    uint8_t  nlos_metric;   /**< NLOS quality: 0 = clear LOS, 255 = fully blocked.
                                 Derived from |RX_power - FirstPath_power| in dB,
                                 scaled: metric = min(255, (diff_dB / 20) * 255). */
} dstwr_reply_entry_t;
#pragma pack(pop)

/** Over-the-air broadcast frame header (fixed part). */
#pragma pack(push, 1)
typedef struct {
    uint16_t magic;         /**< Frame type magic: DSTWR_MAGIC = 0xCB30. */
    uint8_t  node_id;       /**< Transmitter node ID (1..30). */
    uint16_t seq_num;       /**< Monotonic TX sequence number. */
    uint8_t  tx_ts[5];      /**< 40-bit predicted TX timestamp (little-endian). */
    uint8_t  master_id;     /**< Current dynamic master node ID. */
    uint32_t sf_counter;    /**< Superframe counter (epoch from master). */
    uint32_t alive_mask;    /**< Bitmask: bit k set → node (k+1) heard last SF. */
    uint8_t  n_replies;     /**< Number of reply_entry records following header. */
    /* Followed by n_replies × dstwr_reply_entry_t */
} dstwr_frame_header_t;
#pragma pack(pop)

/* Compile-time size checks. */
_Static_assert(sizeof(dstwr_frame_header_t) == 20,
               "dstwr_frame_header_t must be exactly 20 bytes");
_Static_assert(sizeof(dstwr_reply_entry_t) == 6,
               "dstwr_reply_entry_t must be exactly 6 bytes");

/* ═══════════════════════════════════════════════════════════════════════════
 * SNIFFER → PC BINARY REPORT FORMAT
 *
 * The sniffer (node 31) captures all 30 slots and pushes each decoded
 * broadcast frame to PC over UART as a binary report.
 *
 *   Offset  Size   Field
 *   ──────  ─────  ─────────────────────────────────────────────────────────
 *   0       1      SYNC byte = 0xAA
 *   1       2      payload_len (little-endian, excludes sync + len + CRC)
 *   3       1      report_type = 0x01 (decoded broadcast)
 *   4       1      source_node_id
 *   5       5      source_tx_ts (40-bit)
 *   10      5      sniffer_rx_ts (40-bit, local clock)
 *   15      4      sf_counter
 *   19      1      n_replies
 *   20      var    reply_entry[n_replies] × 6 bytes each
 *   end     4      CRC32 of bytes [3..end-1]
 *
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Sniffer UART report header. */
#pragma pack(push, 1)
typedef struct {
    uint8_t  sync;          /**< Synchronization byte: 0xAA. */
    uint16_t payload_len;   /**< Payload length (after this field, before CRC). */
    uint8_t  report_type;   /**< Report type: 0x01 = broadcast capture. */
    uint8_t  source_id;     /**< Source node that transmitted. */
    uint8_t  source_tx_ts[5];  /**< TX timestamp from source's frame. */
    uint8_t  sniffer_rx_ts[5]; /**< RX timestamp at sniffer (local clock). */
    uint32_t sf_counter;    /**< Superframe counter from source frame. */
    uint8_t  n_replies;     /**< Reply count from source frame. */
    /* Followed by n_replies × dstwr_reply_entry_t, then 4-byte CRC32 */
} dstwr_sniffer_report_t;
#pragma pack(pop)

_Static_assert(sizeof(dstwr_sniffer_report_t) == 24,
               "dstwr_sniffer_report_t must be exactly 24 bytes");

/* ═══════════════════════════════════════════════════════════════════════════
 * PER-NODE RUNTIME STATE
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Reception record: what we know about the last frame from each neighbor. */
typedef struct {
    uint64_t rx_timestamp;   /**< Our local RX timestamp when we received it. */
    uint64_t their_tx_ts;    /**< TX timestamp they reported in their frame. */
    uint32_t sf_counter;     /**< Superframe counter of that frame. */
    uint16_t seq_num;        /**< Their sequence number. */
    uint8_t  nlos_metric;    /**< NLOS quality we measured on their signal. */
    bool     valid;          /**< True if we have a recent reception. */
} dstwr_neighbor_t;

/** Node operating mode. */
typedef enum {
    DSTWR_MODE_MOBILE   = 0, /**< Normal mobile node (TX + RX). */
    DSTWR_MODE_SNIFFER  = 1, /**< Passive sniffer (RX only, UART output). */
} dstwr_mode_t;

/** Master synchronization state. */
typedef enum {
    DSTWR_SYNC_NONE     = 0, /**< No synchronization. */
    DSTWR_SYNC_SLAVE    = 1, /**< Synchronized to a master's clock. */
    DSTWR_SYNC_MASTER   = 2, /**< This node IS the master. */
} dstwr_sync_state_t;

/** Complete runtime state for one node. */
typedef struct {
    /* ── Identity ── */
    uint8_t             my_id;          /**< This node's ID (1..30, or 0 for sniffer). */
    dstwr_mode_t        mode;           /**< Operating mode. */

    /* ── TDMA Synchronization ── */
    dstwr_sync_state_t  sync_state;
    uint8_t             master_id;      /**< Current network master ID. */
    uint32_t            sf_counter;     /**< Local superframe counter. */
    uint64_t            sf_epoch;       /**< DW timestamp of superframe start. */
    uint16_t            seq_num;        /**< TX sequence number. */
    uint8_t             master_miss;    /**< Consecutive superframes without master. */
    uint32_t            alive_mask;     /**< Bitmask of nodes heard recently. */

    /* ── Neighbor Table ── */
    dstwr_neighbor_t    neighbors[DSTWR_NUM_NODES + 1]; /**< Index by node_id (1..30). */

    /* ── Last TX record ── */
    uint64_t            last_tx_ts;     /**< Actual TX timestamp of our last broadcast. */
    uint32_t            last_tx_sf;     /**< Superframe when we last transmitted. */
    bool                last_tx_valid;  /**< True if last_tx_ts is usable. */

    /* ── Calibration ── */
    uint16_t            ant_delay_tx;   /**< TX antenna delay (DW ticks). */
    uint16_t            ant_delay_rx;   /**< RX antenna delay (DW ticks). */

    /* ── Statistics ── */
    uint32_t            stat_tx;
    uint32_t            stat_rx;
    uint32_t            stat_missed;
    uint32_t            stat_crc_err;
    uint32_t            stat_sync_loss;
    uint32_t            stat_failover;
} dstwr_state_t;

/* ═══════════════════════════════════════════════════════════════════════════
 * PUBLIC API
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * @brief Initialize the DS-TWR TDMA subsystem.
 * @param node_id  This node's ID (1..30 for mobile, 0 for sniffer).
 * @return 0 on success, negative errno on failure.
 */
int dstwr_init(uint8_t node_id);

/**
 * @brief Start the TDMA protocol (call after dstwr_init).
 * @return 0 on success, negative errno on failure.
 */
int dstwr_start(void);

/**
 * @brief Stop the TDMA protocol.
 */
void dstwr_stop(void);

/**
 * @brief Service function — must be called from main loop at high frequency.
 *        Handles RX, TX scheduling, sync, and failover.
 */
void dstwr_service(void);

/**
 * @brief Get pointer to the global runtime state (read-only for debug).
 */
const dstwr_state_t *dstwr_get_state(void);

/**
 * @brief Print status to console.
 */
void dstwr_status(void);

/**
 * @brief CLI command handler for "dstwr" commands.
 */
int dstwr_command(int argc, char **argv);

/* ═══════════════════════════════════════════════════════════════════════════
 * TIMESTAMP MATH (40-BIT)
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * @brief Subtract two 40-bit DW3110 timestamps with wrap-around handling.
 *
 * The DW3110 timer is 40 bits wide, counting at 63.8976 GHz.
 * It wraps from 0xFFFFFFFFFF → 0x0000000000 every ~17.2 seconds.
 *
 * This function computes (a - b) mod 2^40 as an unsigned result.
 * The caller must ensure the true elapsed time is < 2^39 ticks (~8.6 s)
 * to avoid ambiguity.
 *
 * @param a  Minuend timestamp (40-bit).
 * @param b  Subtrahend timestamp (40-bit).
 * @return   (a - b) mod 2^40, as uint64_t in [0, 2^40).
 */
static inline uint64_t dstwr_ts_sub(uint64_t a, uint64_t b)
{
    return (a - b) & DW_TIMER_MASK;
}

/**
 * @brief Signed difference between two 40-bit timestamps.
 *
 * Returns a signed value in [-2^39, +2^39). Positive means a is after b.
 *
 * @param a  First timestamp.
 * @param b  Second timestamp.
 * @return   Signed difference.
 */
static inline int64_t dstwr_ts_diff(uint64_t a, uint64_t b)
{
    uint64_t raw = (a - b) & DW_TIMER_MASK;
    return (raw < DW_TIMER_HALF) ? (int64_t)raw : (int64_t)(raw - DW_TIMER_MODULUS);
}

/**
 * @brief Add offset to a 40-bit timestamp with wrap.
 */
static inline uint64_t dstwr_ts_add(uint64_t ts, uint64_t offset)
{
    return (ts + offset) & DW_TIMER_MASK;
}

/**
 * @brief Prepare a raw DW timestamp for dwt_setdelayedtrxtime().
 *
 * The DW3110 Delayed TX register uses bits [39:9] of the target time,
 * so we must:
 *   1. Clear the lowest 9 bits (round down to 512-tick boundary).
 *   2. Shift right by 8 to produce the 32-bit register value.
 *
 * The actual TX will occur at: (delayed_reg << 8) + antenna_delay.
 *
 * @param target_time  Desired TX time (40-bit DW ticks).
 * @return             Value to pass to dwt_setdelayedtrxtime().
 */
static inline uint32_t dstwr_delayed_tx_reg(uint64_t target_time)
{
    /* Clear 9 LSBs: target & 0xFFFFFFFE00, then shift right 8 */
    return (uint32_t)((target_time >> 8) & 0xFFFFFFFEUL);
}

/**
 * @brief Predict the actual TX timestamp from the delayed-TX target time.
 *
 * The DW3110 transmits at: (delayed_reg << 8) + TX_ANT_DLY.
 * Since delayed_reg = (target >> 8) & 0xFFFFFFFE, the actual TX time is:
 *   (target & 0xFFFFFFFE00) + TX_ANT_DLY.
 *
 * @param target_time  The raw target time passed to scheduling.
 * @param tx_ant_dly   TX antenna delay in DW ticks.
 * @return             Predicted actual TX timestamp (40-bit).
 */
static inline uint64_t dstwr_predicted_tx(uint64_t target_time, uint16_t tx_ant_dly)
{
    return ((target_time & UINT64_C(0xFFFFFFFE00)) + tx_ant_dly) & DW_TIMER_MASK;
}

/**
 * @brief Compress a 40-bit reply_time delta to 32-bit for air transmission.
 *
 * Right-shift by 8 bits: resolution becomes ~4 ns (256 × 15.65 ps).
 * Maximum representable interval: ~67 seconds (far more than 39 ms superframe).
 *
 * @param delta  The 40-bit time difference to compress.
 * @return       32-bit compressed value.
 */
static inline uint32_t dstwr_compress_reply(uint64_t delta)
{
    return (uint32_t)(delta >> 8);
}

/**
 * @brief Decompress a 32-bit reply_time back to 40-bit ticks.
 * @param compressed  Value from air frame.
 * @return            Approximate 40-bit time difference.
 */
static inline uint64_t dstwr_decompress_reply(uint32_t compressed)
{
    return (uint64_t)compressed << 8;
}

/**
 * @brief Compute Asymmetric DS-TWR Time-of-Flight.
 *
 * Given two nodes A and B exchanging frames in slots:
 *   Slot i: A transmits → B receives
 *   Slot j: B transmits → A receives
 *
 * T_round1 = B_rx(A) - B_tx(B_prev)   [B's perspective: time from its last TX to receiving A]
 *   ... actually for broadcast DS-TWR with 2 exchanges:
 *
 * With broadcast asymmetric DS-TWR between nodes i and j:
 *   Exchange 1 (superframe t-1): i TX at T1, j RX at T2
 *   Exchange 2 (superframe t-1): j TX at T3, i RX at T4
 *   Exchange 3 (superframe t):   i TX at T5, j RX at T6
 *
 *   T_round1 = T4 - T1  (node i: time from own TX to receiving j's reply)
 *   T_reply1 = T3 - T2  (node j: time from receiving i to own TX)
 *   T_round2 = T6 - T3  (node j: time from own TX to receiving i's second)
 *   T_reply2 = T5 - T4  (node i: time from receiving j to own second TX)
 *
 *   ToF = (T_round1 × T_round2 - T_reply1 × T_reply2)
 *         / (T_round1 + T_round2 + T_reply1 + T_reply2)
 *
 *   Distance = ToF × c
 *
 * @param round1  T_round1 in DW ticks.
 * @param reply1  T_reply1 in DW ticks.
 * @param round2  T_round2 in DW ticks.
 * @param reply2  T_reply2 in DW ticks.
 * @param[out] meters  Computed distance in meters.
 * @return true if computation succeeded, false if inputs are invalid.
 */
bool dstwr_compute_distance(uint64_t round1, uint64_t reply1,
                            uint64_t round2, uint64_t reply2,
                            double *meters);

/**
 * @brief Read DW3110 diagnostic registers and compute NLOS quality metric.
 *
 * Reads dwt_rxdiag_t and computes:
 *   diff_dB = 10 * log10(CIR_power / first_path_power)
 *
 * When diff_dB > DSTWR_NLOS_THRESHOLD_DB (6 dB), the signal likely has
 * significant multipath/NLOS component.
 *
 * @return NLOS metric: 0 = clear LOS, 255 = fully blocked.
 */
uint8_t dstwr_compute_nlos_metric(void);

/**
 * @brief Pack the broadcast frame into a byte buffer.
 *
 * @param state   Current node state.
 * @param buf     Output buffer (must be ≥ DSTWR_MAX_PAYLOAD).
 * @param predicted_tx  The predicted TX timestamp to embed.
 * @return        Number of bytes written, or 0 on error.
 */
size_t dstwr_pack_frame(const dstwr_state_t *state, uint8_t *buf,
                        uint64_t predicted_tx);

/**
 * @brief Parse a received broadcast frame.
 *
 * @param buf      Raw frame bytes.
 * @param len      Frame length.
 * @param[out] hdr Parsed header.
 * @param[out] entries  Array to fill with reply entries (max 29).
 * @param[out] n_entries  Number of entries parsed.
 * @return true on success.
 */
bool dstwr_parse_frame(const uint8_t *buf, size_t len,
                       dstwr_frame_header_t *hdr,
                       dstwr_reply_entry_t *entries,
                       uint8_t *n_entries);

#endif /* DSTWR_TDMA_H */
