/**
 * @file dstwr_tdma.c
 * @brief Anchor-Free 30-Node Broadcast Asymmetric DS-TWR — Firmware Implementation
 *
 * Implements the complete TDMA state machine, timestamp math, NLOS diagnostics,
 * and frame packing/parsing for DWM3001C cooperative localization.
 *
 * ARCHITECTURE:
 *   dstwr_service() is called from the main loop. It implements a state machine:
 *     1. Check if it's time to transmit in our slot → arm Delayed TX
 *     2. Otherwise, listen for incoming frames from other nodes
 *     3. On RX: update neighbor table, extract timestamps, compute NLOS
 *     4. Maintain synchronization to Dynamic Master
 *     5. Handle failover if master disappears
 *
 * Copyright (c) 2026 Cyborg Project — All rights reserved.
 */

#include "dstwr_tdma.h"
#include "validation.h"
#include "radio.h"
#include <deca_device_api.h>
#include <string.h>
#include <math.h>
#include <hal/nrf_gpio.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * PRIVATE STATE
 * ═══════════════════════════════════════════════════════════════════════════ */

static dstwr_state_t g_state;
static bool g_running;

/* Sniffer UART TX buffer (for node 31 / sniffer mode). */
static uint8_t g_sniffer_buf[512];

/* ═══════════════════════════════════════════════════════════════════════════
 * LITTLE-ENDIAN BYTE HELPERS
 * ═══════════════════════════════════════════════════════════════════════════ */

static void put_le8(uint8_t *p, uint8_t v)  { p[0] = v; }
static void put_le16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static void put_le40(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 5; i++) p[i] = (uint8_t)(v >> (8 * i));
}

static uint16_t get_le16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }
static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t get_le40(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 4; i >= 0; --i) v = (v << 8) | p[i];
    return v;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * CRC32 (same polynomial as COBS/u3d transport)
 * ═══════════════════════════════════════════════════════════════════════════ */

static uint32_t crc32_compute(const uint8_t *data, size_t len)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int k = 0; k < 8; ++k)
            crc = (crc >> 1) ^ ((0U - (crc & 1U)) & 0xEDB88320U);
    }
    return ~crc;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * DW3110 SYSTEM TIME
 *
 * DW3110 SYS_TIME register exposes bits [39:8] and latches until an SPI
 * write cycle. A zero-write to SYS_STATUS refreshes the latch without
 * clearing pending events (DW3000 User Manual §8.2.2.7).
 * ═══════════════════════════════════════════════════════════════════════════ */

static uint64_t dw_read_system_time(void)
{
    /* Refresh the SYS_TIME latch by writing 0 to SYS_STATUS. */
    dwt_writesysstatuslo(0);
    return (uint64_t)dwt_readsystimestamphi32() << 8;
}

/**
 * @brief Read the actual TX timestamp after a successful transmission.
 * @return 40-bit TX timestamp.
 */
static uint64_t dw_read_tx_timestamp(void)
{
    uint8_t b[5] = {0};
    dwt_readtxtimestamp(b);
    uint64_t v = 0;
    for (int i = 4; i >= 0; --i) v = (v << 8) | b[i];
    return v;
}

/**
 * @brief Read the actual RX timestamp of the most recently received frame.
 * @return 40-bit RX timestamp.
 */
static uint64_t dw_read_rx_timestamp(void)
{
    uint8_t b[5] = {0};
    dwt_readrxtimestamp(b, DWT_IP_M);
    uint64_t v = 0;
    for (int i = 4; i >= 0; --i) v = (v << 8) | b[i];
    return v;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * NLOS DIAGNOSTIC EXTRACTION
 *
 * The DW3110 provides diagnostic data via dwt_readdiagnostics():
 *   - ipatovPower:   Total channel impulse response (CIR) power
 *   - ipatovF1/F2/F3: First path amplitude samples
 *   - ipatovAccumCount: Number of preamble symbols accumulated
 *
 * NLOS detection formula (from Decawave Application Note APS006):
 *   First_Path_Power = 10 * log10( (F1^2 + F2^2 + F3^2) / (N^2) ) - A
 *   RX_Power         = 10 * log10( (CIR_power * 2^17) / (N^2) ) - A
 *   where A = 121.7 dB for PRF 64 MHz, N = preamble accumulation count
 *
 *   NLOS indicator = RX_Power - First_Path_Power
 *   If > 6 dB → likely NLOS (multipath dominant)
 *   If > 10 dB → severe NLOS
 * ═══════════════════════════════════════════════════════════════════════════ */

uint8_t dstwr_compute_nlos_metric(void)
{
    dwt_rxdiag_t diag;
    dwt_readdiagnostics(&diag);

    /* Extract first-path amplitude components.
     * For DW3110 (DW3000 family), the diagnostic structure contains:
     *   ipatovFpIndex: first path index
     *   ipatovPower:   total CIR power (proportional to RX signal energy)
     *   ipatovF1/F2/F3: first path amplitude samples
     *   ipatovAccumCount: preamble accumulation count N
     */
    double f1 = (double)diag.ipatovF1;
    double f2 = (double)diag.ipatovF2;
    double f3 = (double)diag.ipatovF3;
    double n  = (double)diag.ipatovAccumCount;
    double cir = (double)diag.ipatovPower;

    /* Protect against division by zero. */
    if (n < 1.0 || cir < 1.0) return 255;

    /* First Path Power (relative, no need for absolute dBm offset A).
     * FPP = 10 * log10( (F1^2 + F2^2 + F3^2) / N^2 )
     * RXP = 10 * log10( CIR_power * 2^17 / N^2 )
     *
     * diff_dB = RXP - FPP = 10 * log10( CIR_power * 2^17 / (F1^2+F2^2+F3^2) )
     */
    double fp_energy = f1 * f1 + f2 * f2 + f3 * f3;
    if (fp_energy < 1.0) return 255;

    double diff_db = 10.0 * log10((cir * 131072.0) / fp_energy);

    /* Clamp and scale to [0, 255].
     * 0 dB → 0 (perfect LOS), 20 dB → 255 (heavy NLOS).
     * Negative values (unusual) clamp to 0.
     */
    if (diff_db < 0.0) diff_db = 0.0;
    if (diff_db > 20.0) diff_db = 20.0;

    return (uint8_t)(diff_db * (255.0 / 20.0));
}

/* ═══════════════════════════════════════════════════════════════════════════
 * DS-TWR DISTANCE COMPUTATION
 *
 * Asymmetric Double-Sided Two-Way Ranging formula:
 *   ToF = (R1 × R2 - D1 × D2) / (R1 + R2 + D1 + D2)
 *
 * where R1 = round-trip-1, R2 = round-trip-2,
 *       D1 = reply-delay-1, D2 = reply-delay-2.
 *
 * This eliminates clock drift between the two nodes to first order.
 * All times are in DW3110 ticks (~15.65 ps).
 * ═══════════════════════════════════════════════════════════════════════════ */

bool dstwr_compute_distance(uint64_t round1, uint64_t reply1,
                            uint64_t round2, uint64_t reply2,
                            double *meters)
{
    if (!meters) return false;

    double r1 = (double)round1;
    double d1 = (double)reply1;
    double r2 = (double)round2;
    double d2 = (double)reply2;

    /* Sanity: each interval should be positive and < 0.5 seconds worth of ticks.
     * 0.5s = 31,948,800,000 ticks. Rejects stale/wrapped timestamps. */
    const double limit = 31948800000.0;
    if (r1 <= 0 || r2 <= 0 || d1 <= 0 || d2 <= 0) return false;
    if (r1 > limit || r2 > limit || d1 > limit || d2 > limit) return false;

    double denominator = r1 + r2 + d1 + d2;
    if (denominator <= 0) return false;

    double tof = (r1 * r2 - d1 * d2) / denominator;

    /* Convert ticks to meters.
     * c / (499.2e6 × 128) = 299,702,547 / 63,897,600,000 ≈ 4.6917 mm/tick. */
    double dist = tof * SPEED_OF_LIGHT_M_PER_TICK;

    if (!isfinite(dist) || dist < 0.0 || dist > 1000.0) return false;

    *meters = dist;
    return true;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * FRAME PACKING
 * ═══════════════════════════════════════════════════════════════════════════ */

size_t dstwr_pack_frame(const dstwr_state_t *state, uint8_t *buf,
                        uint64_t predicted_tx)
{
    if (!state || !buf) return 0;

    size_t pos = 0;

    /* Fixed header (20 bytes). */
    put_le16(buf + pos, DSTWR_MAGIC);  pos += 2;
    put_le8(buf + pos, state->my_id);  pos += 1;
    put_le16(buf + pos, state->seq_num); pos += 2;
    put_le40(buf + pos, predicted_tx); pos += 5;
    put_le8(buf + pos, state->master_id); pos += 1;
    put_le32(buf + pos, state->sf_counter); pos += 4;
    put_le32(buf + pos, state->alive_mask); pos += 4;

    /* Count valid replies and remember position for n_replies. */
    size_t n_replies_pos = pos;
    put_le8(buf + pos, 0);  /* placeholder */
    pos += 1;

    /* Reply entries: for each neighbor we received from in the last superframe,
     * include reply_time = T_TX(self_last) - T_RX(from_neighbor).
     * This requires that we transmitted last superframe (last_tx_valid). */
    uint8_t n_replies = 0;
    for (uint8_t j = 1; j <= DSTWR_NUM_NODES; j++) {
        if (j == state->my_id) continue;

        const dstwr_neighbor_t *nb = &state->neighbors[j];
        if (!nb->valid) continue;

        /* Only include replies from the current or previous superframe. */
        uint32_t age = state->sf_counter - nb->sf_counter;
        if (age > 1) continue;

        /* Compute reply_time = T_TX(self_last) - T_RX(from j).
         * This is only meaningful if we transmitted in the previous SF. */
        uint32_t reply_time_compressed = 0;
        if (state->last_tx_valid) {
            uint64_t delta = dstwr_ts_sub(state->last_tx_ts, nb->rx_timestamp);
            reply_time_compressed = dstwr_compress_reply(delta);
        }

        /* Pack entry: peer_id (1) + reply_time (4) + nlos_metric (1) = 6 bytes. */
        put_le8(buf + pos, j);                           pos += 1;
        put_le32(buf + pos, reply_time_compressed);      pos += 4;
        put_le8(buf + pos, nb->nlos_metric);             pos += 1;
        n_replies++;

        if (n_replies >= (DSTWR_NUM_NODES - 1)) break;
    }

    /* Patch n_replies into header. */
    buf[n_replies_pos] = n_replies;

    return pos;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * FRAME PARSING
 * ═══════════════════════════════════════════════════════════════════════════ */

bool dstwr_parse_frame(const uint8_t *buf, size_t len,
                       dstwr_frame_header_t *hdr,
                       dstwr_reply_entry_t *entries,
                       uint8_t *n_entries)
{
    if (!buf || !hdr || !entries || !n_entries) return false;
    if (len < sizeof(dstwr_frame_header_t)) return false;

    /* Parse fixed header. */
    hdr->magic      = get_le16(buf + 0);
    hdr->node_id    = buf[2];
    hdr->seq_num    = get_le16(buf + 3);
    memcpy(hdr->tx_ts, buf + 5, 5);
    hdr->master_id  = buf[10];
    hdr->sf_counter = get_le32(buf + 11);
    hdr->alive_mask = get_le32(buf + 15);
    hdr->n_replies  = buf[19];

    /* Validate. */
    if (hdr->magic != DSTWR_MAGIC) return false;
    if (hdr->node_id < 1 || hdr->node_id > DSTWR_NUM_NODES) return false;
    if (hdr->n_replies > (DSTWR_NUM_NODES - 1)) return false;

    size_t expected = sizeof(dstwr_frame_header_t) +
                      (size_t)hdr->n_replies * sizeof(dstwr_reply_entry_t);
    if (len < expected) return false;

    /* Parse reply entries. */
    const uint8_t *p = buf + sizeof(dstwr_frame_header_t);
    for (uint8_t k = 0; k < hdr->n_replies; k++) {
        entries[k].peer_id     = p[0];
        entries[k].reply_time  = get_le32(p + 1);
        entries[k].nlos_metric = p[5];
        p += sizeof(dstwr_reply_entry_t);

        /* Validate peer_id. */
        if (entries[k].peer_id < 1 || entries[k].peer_id > DSTWR_NUM_NODES) {
            return false;
        }
    }

    *n_entries = hdr->n_replies;
    return true;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * DYNAMIC MASTER ELECTION & FAILOVER
 *
 * Rules:
 *   - The node with the LOWEST active ID is the Dynamic Master.
 *   - The master sets master_id = its own ID in every broadcast.
 *   - All slaves track the master's sf_counter and synchronize to its slot 0.
 *   - If a slave doesn't hear the master for DSTWR_FAILOVER_COUNT superframes,
 *     it checks if it has the lowest alive ID → if yes, promotes itself.
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * @brief Determine the lowest active node ID from the alive_mask.
 *        Our own ID is always considered "alive".
 */
static uint8_t find_lowest_alive(const dstwr_state_t *state)
{
    uint32_t mask = state->alive_mask | (1U << (state->my_id - 1));
    for (uint8_t id = 1; id <= DSTWR_NUM_NODES; id++) {
        if (mask & (1U << (id - 1))) return id;
    }
    return state->my_id; /* Fallback: we're always alive. */
}

/**
 * @brief Handle master failover logic.
 *        Called once per superframe boundary.
 */
static void handle_master_failover(dstwr_state_t *state)
{
    if (state->sync_state == DSTWR_SYNC_MASTER) {
        /* We are master. Check if a lower-ID node appeared. */
        uint8_t lowest = find_lowest_alive(state);
        if (lowest < state->my_id) {
            /* A lower-ID node is alive — demote self. */
            state->sync_state = DSTWR_SYNC_NONE;
            state->master_id = lowest;
            out("[DSTWR] Demoting self, new master candidate: %u", lowest);
        }
        return;
    }

    /* We are slave or unsynced. */
    if (state->master_id == 0 || state->master_miss >= DSTWR_FAILOVER_COUNT) {
        /* Master missing — check if we should become master. */
        /* Clear the old master from alive mask. */
        if (state->master_id > 0 && state->master_id <= DSTWR_NUM_NODES) {
            state->alive_mask &= ~(1U << (state->master_id - 1));
        }

        uint8_t lowest = find_lowest_alive(state);
        if (lowest == state->my_id) {
            /* We are the lowest alive node → become master. */
            state->sync_state = DSTWR_SYNC_MASTER;
            state->master_id = state->my_id;
            state->sf_counter++;
            state->sf_epoch = dstwr_ts_add(dw_read_system_time(),
                                           10 * DW_TICKS_PER_MS);
            state->master_miss = 0;
            state->stat_failover++;
            out("[DSTWR] FAILOVER: promoting self as master (ID=%u)", state->my_id);
        } else {
            /* Wait for the new lowest to claim master. */
            state->sync_state = DSTWR_SYNC_NONE;
            state->master_id = 0;
            state->stat_sync_loss++;
        }
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * RECEIVE HANDLER
 *
 * Called when a valid broadcast frame is received from another node.
 * Updates the neighbor table and synchronization state.
 * ═══════════════════════════════════════════════════════════════════════════ */

static void handle_rx_frame(dstwr_state_t *state,
                           const uint8_t *frame_data, size_t frame_len,
                           uint64_t rx_timestamp)
{
    dstwr_frame_header_t hdr;
    dstwr_reply_entry_t entries[DSTWR_NUM_NODES - 1];
    uint8_t n_entries = 0;

    if (!dstwr_parse_frame(frame_data, frame_len, &hdr, entries, &n_entries)) {
        return;
    }

    uint8_t src = hdr.node_id;
    if (src == state->my_id) return; /* Ignore self (shouldn't happen). */

    state->stat_rx++;

    /* Mark this node as alive. */
    state->alive_mask |= (1U << (src - 1));

    /* Compute NLOS metric for this reception. */
    uint8_t nlos = dstwr_compute_nlos_metric();

    /* Update neighbor table. */
    dstwr_neighbor_t *nb = &state->neighbors[src];
    nb->rx_timestamp = rx_timestamp;
    nb->their_tx_ts  = get_le40(hdr.tx_ts);
    nb->sf_counter   = hdr.sf_counter;
    nb->seq_num      = hdr.seq_num;
    nb->nlos_metric  = nlos;
    nb->valid        = true;

    /* ── Synchronization to Master ── */
    if (hdr.master_id > 0 && hdr.master_id <= DSTWR_NUM_NODES) {
        if (src == hdr.master_id) {
            /* Direct reception from the master node. */
            state->master_id = hdr.master_id;
            state->sf_counter = hdr.sf_counter;
            state->master_miss = 0;

            /* Synchronize our superframe epoch to the master's slot.
             * The master's slot is slot (master_id - 1).
             * SF epoch = rx_timestamp - slot_offset(master_id - 1).
             * We correct using the master's TX timestamp relative to
             * our RX to account for propagation + clock drift. */
            uint64_t slot_offset = (uint64_t)(hdr.master_id - 1) * DSTWR_SLOT_TICKS;
            state->sf_epoch = dstwr_ts_sub(rx_timestamp, slot_offset);

            if (state->sync_state != DSTWR_SYNC_MASTER) {
                state->sync_state = DSTWR_SYNC_SLAVE;
            }
        } else if (hdr.master_id == state->master_id) {
            /* Indirect: another slave relayed the master's SF counter.
             * Only use if we haven't heard the master directly. */
        }
    }

    /* Merge the source's alive_mask with ours (transitive awareness). */
    state->alive_mask |= hdr.alive_mask;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * SNIFFER MODE — UART REPORT OUTPUT
 *
 * The sniffer (node 31) passively receives all broadcasts and pushes
 * binary reports to PC over UART for the Python localization engine.
 * ═══════════════════════════════════════════════════════════════════════════ */

static void sniffer_report(const uint8_t *frame_data, size_t frame_len,
                          uint64_t rx_timestamp)
{
    dstwr_frame_header_t hdr;
    dstwr_reply_entry_t entries[DSTWR_NUM_NODES - 1];
    uint8_t n_entries = 0;

    if (!dstwr_parse_frame(frame_data, frame_len, &hdr, entries, &n_entries)) {
        return;
    }

    /* Build sniffer report.
     * Format:
     *   [0xAA] [len_lo] [len_hi] [type=0x01] [src_id]
     *   [src_tx_ts × 5] [sniff_rx_ts × 5]
     *   [sf_counter × 4] [n_replies]
     *   [reply_entries × 6 × n_replies]
     *   [CRC32 × 4]
     */
    size_t payload_len = 1 + 1 + 5 + 5 + 4 + 1 +
                         (size_t)n_entries * sizeof(dstwr_reply_entry_t);
    size_t total = 3 + payload_len + 4; /* sync + len + payload + crc */

    if (total > sizeof(g_sniffer_buf)) return;

    uint8_t *p = g_sniffer_buf;
    p[0] = 0xAA;                              /* Sync */
    put_le16(p + 1, (uint16_t)payload_len);   /* Payload length */

    uint8_t *payload_start = p + 3;
    size_t pos = 0;

    payload_start[pos++] = 0x01;              /* Report type: broadcast capture */
    payload_start[pos++] = hdr.node_id;       /* Source node ID */
    memcpy(payload_start + pos, hdr.tx_ts, 5); pos += 5; /* Source TX timestamp */
    put_le40(payload_start + pos, rx_timestamp); pos += 5; /* Sniffer RX timestamp */
    put_le32(payload_start + pos, hdr.sf_counter); pos += 4;
    payload_start[pos++] = n_entries;

    /* Copy reply entries. */
    for (uint8_t k = 0; k < n_entries; k++) {
        payload_start[pos++] = entries[k].peer_id;
        put_le32(payload_start + pos, entries[k].reply_time); pos += 4;
        payload_start[pos++] = entries[k].nlos_metric;
    }

    /* CRC32 over payload (excludes sync and length fields). */
    uint32_t crc = crc32_compute(payload_start, pos);
    put_le32(payload_start + pos, crc);
    pos += 4;

    /* Send over UART/USB CDC to PC. */
    console_binary(g_sniffer_buf, 3 + pos);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * TRANSMIT: BUILD AND SEND BROADCAST FRAME
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * @brief Transmit our broadcast frame in our assigned TDMA slot.
 *
 * Uses DW3110 Delayed TX to transmit at a precise time within the slot.
 * The target TX time is: sf_epoch + (my_id - 1) × slot_ticks.
 *
 * Steps:
 *   1. Compute target TX time in DW ticks.
 *   2. Subtract TX antenna delay (hardware adds it back).
 *   3. Clear 9 LSBs for Delayed TX register alignment.
 *   4. Build the frame with predicted TX timestamp.
 *   5. Arm Delayed TX and wait for completion.
 */
static void do_transmit(dstwr_state_t *state)
{
    /* Compute target TX time for our slot. */
    uint64_t slot_offset = (uint64_t)(state->my_id - 1) * DSTWR_SLOT_TICKS;
    uint64_t target_raw = dstwr_ts_add(state->sf_epoch, slot_offset);

    /* Subtract TX antenna delay — the DW3110 adds it back automatically. */
    uint64_t raw_for_reg = dstwr_ts_sub(target_raw, state->ant_delay_tx);

    /* Check that we're not already past the deadline. */
    uint64_t now = dw_read_system_time();
    int64_t remaining = dstwr_ts_diff(raw_for_reg, now);

    if (remaining < (int64_t)DSTWR_GUARD_TICKS) {
        /* Too late — we missed our slot. */
        state->stat_missed++;
        state->last_tx_valid = false;
        return;
    }

    if (remaining > (int64_t)(10 * DW_TICKS_PER_MS)) {
        /* Too early — not our slot yet, don't arm. */
        return;
    }

    /* Predict actual TX timestamp (after 9-bit rounding + antenna delay). */
    uint64_t predicted = dstwr_predicted_tx(raw_for_reg, state->ant_delay_tx);

    /* Build the broadcast frame. */
    uint8_t frame[DSTWR_MAX_PAYLOAD];
    size_t frame_len = dstwr_pack_frame(state, frame, predicted);
    if (frame_len == 0) {
        state->stat_missed++;
        return;
    }

    /* Arm Delayed TX. */
    dwt_forcetrxoff();
    dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK |
                         SYS_STATUS_ALL_RX_GOOD |
                         SYS_STATUS_ALL_RX_ERR |
                         SYS_STATUS_ALL_RX_TO);

    int rc = dwt_writetxdata((uint16_t)frame_len, frame, 0);
    if (rc) {
        state->stat_missed++;
        return;
    }

    /* Frame length + 2 for FCS, ranging bit = 1 (enables timestamp capture). */
    dwt_writetxfctrl((uint16_t)(frame_len + 2), 0, 1);

    /* Set delayed TX register. */
    uint32_t delayed_reg = dstwr_delayed_tx_reg(raw_for_reg);
    dwt_setdelayedtrxtime(delayed_reg);

    /* Start Delayed TX (no response expected — broadcast). */
    rc = dwt_starttx(DWT_START_TX_DELAYED);
    if (rc) {
        /* Delayed TX failed (HPDWARN — too late). */
        state->stat_missed++;
        state->last_tx_valid = false;
        dwt_forcetrxoff();
        return;
    }

    /* Wait for TX completion (bounded poll). */
    int64_t deadline = k_uptime_get() + 5; /* 5 ms max wait */
    while (k_uptime_get() < deadline) {
        uint32_t status = dwt_readsysstatuslo();
        if (status & DWT_INT_TXFRS_BIT_MASK) {
            /* TX complete — read actual timestamp. */
            uint64_t actual_tx = dw_read_tx_timestamp();
            dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK);

            /* Verify timestamp matches prediction. */
            if (actual_tx != predicted) {
                /* Timestamp mismatch — discard this TX. */
                state->stat_missed++;
                state->last_tx_valid = false;
            } else {
                state->last_tx_ts    = actual_tx;
                state->last_tx_sf    = state->sf_counter;
                state->last_tx_valid = true;
                state->seq_num++;
                state->stat_tx++;
            }
            return;
        }
        k_usleep(100);
    }

    /* TX timed out. */
    state->stat_missed++;
    state->last_tx_valid = false;
    dwt_forcetrxoff();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * RECEIVE: LISTEN FOR BROADCAST FRAMES IN OTHER SLOTS
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * @brief Attempt to receive a single frame. Non-blocking short poll.
 * @return 0 on successful reception, negative errno otherwise.
 */
static int do_receive_once(dstwr_state_t *state)
{
    uint8_t rx_buf[DSTWR_MAX_PAYLOAD];
    size_t rx_len = 0;
    uint64_t rx_ts = 0;

    /* Use the existing radio_network_rx which polls with RX armed. */
    int rc = radio_network_rx(rx_buf, sizeof(rx_buf), &rx_len, &rx_ts);

    if (rc == 0 && rx_len >= sizeof(dstwr_frame_header_t)) {
        if (state->mode == DSTWR_MODE_SNIFFER) {
            sniffer_report(rx_buf, rx_len, rx_ts);
        } else {
            handle_rx_frame(state, rx_buf, rx_len, rx_ts);
        }
    }
    return rc;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * SUPERFRAME BOUNDARY MANAGEMENT
 *
 * At each superframe boundary:
 *   1. Increment sf_counter (master) or wait for master sync (slave).
 *   2. Age out neighbors not heard for 2+ superframes.
 *   3. Run failover check.
 *   4. Update alive_mask.
 * ═══════════════════════════════════════════════════════════════════════════ */

static void superframe_boundary(dstwr_state_t *state)
{
    if (state->sync_state == DSTWR_SYNC_MASTER) {
        state->sf_counter++;
        state->sf_epoch = dstwr_ts_add(state->sf_epoch, DSTWR_SUPERFRAME_TICKS);
    }

    /* Age out stale neighbors and update alive_mask. */
    uint32_t new_mask = (1U << (state->my_id - 1)); /* We're always alive. */
    for (uint8_t j = 1; j <= DSTWR_NUM_NODES; j++) {
        if (j == state->my_id) continue;
        dstwr_neighbor_t *nb = &state->neighbors[j];
        if (nb->valid) {
            uint32_t age = state->sf_counter - nb->sf_counter;
            if (age > 2) {
                nb->valid = false;
            } else {
                new_mask |= (1U << (j - 1));
            }
        }
    }
    state->alive_mask = new_mask;

    /* Track master presence. */
    if (state->sync_state == DSTWR_SYNC_SLAVE) {
        state->master_miss++;
    }

    /* Run failover logic. */
    handle_master_failover(state);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * MAIN SERVICE FUNCTION
 *
 * Called repeatedly from the main loop. Implements the TDMA schedule:
 *   - If our slot is approaching → arm TX
 *   - Otherwise → listen for RX
 *   - At superframe boundaries → run housekeeping
 * ═══════════════════════════════════════════════════════════════════════════ */

void dstwr_service(void)
{
    if (!g_running) return;

    dstwr_state_t *state = &g_state;
    uint64_t now = dw_read_system_time();

    /* ── Sniffer mode: just listen continuously ── */
    if (state->mode == DSTWR_MODE_SNIFFER) {
        do_receive_once(state);
        return;
    }

    /* ── Check synchronization ── */
    if (state->sync_state == DSTWR_SYNC_NONE) {
        /* Not synchronized — just listen and hope to hear a master. */
        do_receive_once(state);
        return;
    }

    /* ── Determine where we are in the superframe ── */
    int64_t elapsed = dstwr_ts_diff(now, state->sf_epoch);

    /* Check if we've passed the end of the superframe. */
    if (elapsed >= (int64_t)DSTWR_SUPERFRAME_TICKS) {
        superframe_boundary(state);
        return; /* Will re-enter next loop iteration with updated epoch. */
    }

    /* Negative elapsed means we're before the SF epoch (shouldn't happen
     * unless clock wrapped or sync is stale). */
    if (elapsed < -(int64_t)(2 * DSTWR_SUPERFRAME_TICKS)) {
        state->sync_state = DSTWR_SYNC_NONE;
        state->stat_sync_loss++;
        return;
    }

    /* ── Determine current slot ── */
    int current_slot = -1;
    if (elapsed >= 0) {
        current_slot = (int)(elapsed / DSTWR_SLOT_TICKS);
        if (current_slot >= DSTWR_NUM_NODES) current_slot = -1;
    }

    /* ── Check if it's time to transmit ── */
    int my_slot = state->my_id - 1; /* 0-indexed */
    uint64_t my_slot_start = dstwr_ts_add(state->sf_epoch,
                                          (uint64_t)my_slot * DSTWR_SLOT_TICKS);
    int64_t time_to_my_slot = dstwr_ts_diff(my_slot_start, now);

    if (time_to_my_slot > 0 && time_to_my_slot < (int64_t)(8 * DW_TICKS_PER_MS)) {
        /* Our slot is approaching — try to transmit. */
        do_transmit(state);
        return;
    }

    /* ── Not our slot — listen for others ── */
    do_receive_once(state);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * INITIALIZATION
 * ═══════════════════════════════════════════════════════════════════════════ */

int dstwr_init(uint8_t node_id)
{
    memset(&g_state, 0, sizeof(g_state));
    g_running = false;

    if (node_id > DSTWR_NUM_NODES) {
        /* node_id == 0 → sniffer mode */
        if (node_id != 0) return -EINVAL;
    }

    g_state.my_id = node_id;
    g_state.mode = (node_id == 0) ? DSTWR_MODE_SNIFFER : DSTWR_MODE_MOBILE;

    /* Load antenna delay from radio calibration. */
    g_state.ant_delay_tx = radio_tx_antenna_delay();
    g_state.ant_delay_rx = radio_tx_antenna_delay(); /* Symmetric default. */

    out("[DSTWR] Initialized: ID=%u, mode=%s, ant_dly=%u",
        node_id,
        (g_state.mode == DSTWR_MODE_SNIFFER) ? "SNIFFER" : "MOBILE",
        g_state.ant_delay_tx);

    return 0;
}

int dstwr_start(void)
{
    if (g_running) return -EBUSY;

    const radio_stats_t *rs = radio_stats();
    if (!rs->ready || !rs->calibrated) {
        out("[DSTWR] Radio not ready/calibrated");
        return -EACCES;
    }

    /* Switch PHY to network mode (Preamble 128, Extended PHR). */
    int rc = radio_network_phy(true);
    if (rc) {
        out("[DSTWR] Failed to set network PHY: %d", rc);
        return rc;
    }

    /* Reset state. */
    for (int i = 0; i <= DSTWR_NUM_NODES; i++) {
        g_state.neighbors[i].valid = false;
    }
    g_state.sf_counter = 0;
    g_state.seq_num = 0;
    g_state.last_tx_valid = false;
    g_state.master_miss = 0;
    g_state.stat_tx = 0;
    g_state.stat_rx = 0;
    g_state.stat_missed = 0;
    g_state.stat_crc_err = 0;
    g_state.stat_sync_loss = 0;
    g_state.stat_failover = 0;

    if (g_state.mode == DSTWR_MODE_SNIFFER) {
        /* Sniffer: start listening immediately. */
        g_state.sync_state = DSTWR_SYNC_NONE;
        g_state.master_id = 0;
    } else if (g_state.my_id == 1) {
        /* Node 1 always starts as master. */
        g_state.sync_state = DSTWR_SYNC_MASTER;
        g_state.master_id = g_state.my_id;
        g_state.sf_epoch = dstwr_ts_add(dw_read_system_time(),
                                        10 * DW_TICKS_PER_MS);
        g_state.alive_mask = (1U << (g_state.my_id - 1));
    } else {
        /* Non-master mobile nodes start unsynced, listening for master. */
        g_state.sync_state = DSTWR_SYNC_NONE;
        g_state.master_id = 0;
        g_state.alive_mask = (1U << (g_state.my_id - 1));
    }

    g_running = true;
    out("[DSTWR] Started: master=%u, sync=%d",
        g_state.master_id, g_state.sync_state);

    return 0;
}

void dstwr_stop(void)
{
    if (!g_running) return;
    g_running = false;
    radio_stop();
    radio_network_phy(false);
    out("[DSTWR] Stopped");
}

const dstwr_state_t *dstwr_get_state(void)
{
    return &g_state;
}

void dstwr_status(void)
{
    const dstwr_state_t *s = &g_state;
    out("DSTWR running=%u mode=%s id=%u master=%u sync=%d sf=%u",
        g_running,
        (s->mode == DSTWR_MODE_SNIFFER) ? "SNIFFER" : "MOBILE",
        s->my_id, s->master_id, s->sync_state, s->sf_counter);
    out("DSTWR tx=%u rx=%u missed=%u crc_err=%u sync_loss=%u failover=%u",
        s->stat_tx, s->stat_rx, s->stat_missed,
        s->stat_crc_err, s->stat_sync_loss, s->stat_failover);

    /* Show alive neighbors. */
    uint8_t alive_count = 0;
    for (uint8_t j = 1; j <= DSTWR_NUM_NODES; j++) {
        if (s->alive_mask & (1U << (j - 1))) alive_count++;
    }
    out("DSTWR alive_mask=0x%08x (%u nodes)", s->alive_mask, alive_count);
}

int dstwr_command(int argc, char **argv)
{
    if (argc == 1 && !strcmp(argv[0], "status")) {
        dstwr_status();
        return 0;
    }
    if (argc == 1 && !strcmp(argv[0], "stop")) {
        dstwr_stop();
        return 0;
    }
    if (argc == 2 && !strcmp(argv[0], "start")) {
        /* "dstwr start <node_id>" — node_id 1..30, or 0 for sniffer. */
        char *end;
        unsigned long id = strtoul(argv[1], &end, 10);
        if (*end || id > DSTWR_NUM_NODES) return -EINVAL;

        int rc = dstwr_init((uint8_t)id);
        if (rc) return rc;
        return dstwr_start();
    }
    return -EINVAL;
}
