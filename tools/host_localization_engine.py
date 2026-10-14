#!/usr/bin/env python3
"""
Host Localization Engine for 30-Node Anchor-Free Cooperative UWB Ranging
=========================================================================

Receives distance measurements from 30 DWM3001C nodes via TDMA Broadcast
Asymmetric DS-TWR at 25 Hz and estimates relative positions in real-time.

Pipeline:
  1. AsymmetricDSTWRAssembler    — Decode UART stream, assemble D and W matrices
  2. WeightedSMACOFSolver        — Iterative Weighted MDS (Guttman Transform)
  3. OpportunisticKabschAligner  — ZUPT + Weighted Procrustes alignment
  4. SwarmEKFTracker             — Per-node Constant-Velocity EKF
  5. SwarmSimulationAndVisualizer — Full pipeline simulation & visualization

Author:  Cyborg Project
Date:    2026-10-14
"""

import struct
import time
import threading
import warnings
from collections import deque
from typing import Optional, Tuple, Dict, List

import numpy as np
from numpy.linalg import svd, pinv, norm, eigh
import scipy.sparse.csgraph as csgraph
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

N_NODES = 30
SPEED_OF_LIGHT = 299702547.0         # m/s (in vacuum, adjusted)
DW_TICK_PERIOD = 1.0 / (499.2e6 * 128)  # ~15.65 ps per tick
SUPERFRAME_HZ = 25.6                 # Superframe rate (Hz)
NLOS_THRESHOLD_DB = 6.0              # NLOS detection threshold
V_MAX = 1.0                          # Maximum cockroach velocity (m/s)

# Sniffer UART sync byte
SYNC_BYTE = 0xAA
REPORT_TYPE_BROADCAST = 0x01

# CRC32 (same polynomial as firmware)
def _crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & (-(crc & 1))) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


# ═══════════════════════════════════════════════════════════════════════════════
# CLASS 1: AsymmetricDSTWRAssembler
# ═══════════════════════════════════════════════════════════════════════════════

class AsymmetricDSTWRAssembler:
    """
    Assembles the 30×30 distance matrix D and weight matrix W from the
    TDMA broadcast stream.

    Each node i broadcasts its reply_time[j] = T_TX(i) - T_RX(i ← j) for
    all neighbors j it heard. Combined with node j's reply_time[i] from the
    previous superframe, we compute Asymmetric DS-TWR distance:

        T_round1 = T_RX(i ← j_prev_broadcast) - T_TX(j_prev_broadcast)
                 → but this is from i's local clock perspective, so:
                 T_round1 = i's reply_time to j (from j's broadcast)

    For broadcast DS-TWR with 3 messages across 2 superframes:
        SF t-1, Slot i: Node i TX at T1, Node j RX at T2
        SF t-1, Slot j: Node j TX at T3, Node i RX at T4
        SF t,   Slot i: Node i TX at T5, Node j RX at T6

        reply_time_i(j) from SF t-1 = T1 - T4  (used in SF t's broadcast)
        reply_time_j(i) from SF t   = T3 - T2  (from SF t-1's j broadcast)

        T_round1 = T4 - T1 = -reply_time_i(j)  → need unsigned: MASK - reply + 1
        T_reply1 = T3 - T2 = reply_time_j(i)
        T_round2 = T6 - T3 = j can report in SF t
        T_reply2 = T5 - T4 = i's new reply_time to j

    Actually, for broadcast asymmetric DS-TWR assembled from sequential slots:

        Let slot_i occur before slot_j in each superframe (i < j):
        SF(t): slot i → TX(i,t), slot j → TX(j,t)
        SF(t+1): slot i → TX(i,t+1)

        node j receives TX(i,t) at RX(j←i,t)
        node i receives TX(j,t) at RX(i←j,t)
        node j receives TX(i,t+1) at RX(j←i,t+1)

        Round1 = RX(i←j,t) - TX(i,t)                          [node i clock]
        Reply1 = TX(j,t) - RX(j←i,t)     = reply_time_j→i     [node j clock]
        Round2 = RX(j←i,t+1) - TX(j,t)                         [node j clock]
        Reply2 = TX(i,t+1) - RX(i←j,t)   = reply_time_i→j     [node i clock]

    What each node BROADCASTS:
        Node j at SF(t) reports: reply_time[i] = TX(j,t) - RX(j←i,t) = Reply1
        Node i at SF(t+1) reports: reply_time[j] = TX(i,t+1) - RX(i←j,t) = Reply2

    What the SNIFFER can compute:
        Round1 is NOT directly available (it spans node i's local clock).
        But if the sniffer heard TX(i,t), TX(j,t), TX(i,t+1), it can
        reconstruct Round1 and Round2 from the SNIFFER's own clock:
            Sniff_Round1 = Sniff_RX(j,t) - Sniff_RX(i,t)   ≈ actual over-air
            → NO, this includes different propagation delays.

    CORRECTED APPROACH: All information is in the BROADCAST payloads.
        From node j's broadcast (SF t): reply_time_j[i] = Reply1
        From node i's broadcast (SF t+1): reply_time_i[j] = Reply2
        Round1 and Round2 require the OTHER node's timestamps.

        Round1 = RX(i←j,t) - TX(i,t)
               = TX(j,t) - RX(j←i,t) + ToF ... no, clocks differ.

    THE KEY INSIGHT: With only broadcast reply_times, we need BOTH nodes'
    timestamps from TWO superframes to assemble a full DS-TWR measurement.

        From i's SF(t) broadcast:   tx_ts_i(t),   reply_time_i(j) from SF(t-1)
        From j's SF(t) broadcast:   tx_ts_j(t),   reply_time_j(i) from SF(t)
        From i's SF(t+1) broadcast: tx_ts_i(t+1), reply_time_i(j) from SF(t)

        reply_time_j(i) at SF(t) = TX(j,t) - RX(j←i,t)               = Reply1
        reply_time_i(j) at SF(t+1) = TX(i,t+1) - RX(i←j,t)           = Reply2

        Now we need Round1 and Round2:
        Round1 = RX(i←j,t) - TX(i,t)
               = [TX(i,t+1) - Reply2] - TX(i,t)
               = TX(i,t+1) - TX(i,t) - Reply2       ← from i's broadcasts!

        Round2 = RX(j←i,t+1) - TX(j,t)
               ← We need j's SF(t+1) reply_time to i:
               reply_time_j(i) at SF(t+1) = TX(j,t+1) - RX(j←i,t+1)
               → Round2 = TX(j,t+1) - reply_time_j(i, t+1) - TX(j,t)
                         = [TX(j,t+1) - TX(j,t)] - reply_time_j(i, t+1)

    So for the sniffer assembler, we need data from SF(t) and SF(t+1):
        From i: tx_ts_i(t), tx_ts_i(t+1), reply_time_i(j) in SF(t+1) = Reply2
        From j: tx_ts_j(t), tx_ts_j(t+1), reply_time_j(i) in SF(t) = Reply1
                                            reply_time_j(i) in SF(t+1) → for Round2

    This class buffers the last 2 superframes of data and assembles D, W.
    """

    def __init__(self, n_nodes: int = N_NODES,
                 ant_delay_bias_m: float = 0.0,
                 nlos_threshold_db: float = NLOS_THRESHOLD_DB):
        """
        Args:
            n_nodes: Number of mobile nodes.
            ant_delay_bias_m: Antenna delay bias to subtract (meters).
            nlos_threshold_db: NLOS detection threshold (dB).
        """
        self.n = n_nodes
        self.ant_delay_bias = ant_delay_bias_m
        self.nlos_threshold = nlos_threshold_db

        # Per-node, per-superframe buffer:
        # Key: (sf_counter, node_id) → {tx_ts, reply_times: {peer: (reply_ticks, nlos)}}
        self._buffer: Dict[Tuple[int, int], dict] = {}

        # Last assembled SF counter
        self._last_sf = -1

        # Distance and weight matrices
        self.D = np.full((n_nodes, n_nodes), np.nan)
        self.W = np.zeros((n_nodes, n_nodes))

    def feed_sniffer_report(self, report: dict):
        """
        Feed a decoded sniffer report into the assembler.

        Args:
            report: Dict with keys:
                'source_id': int (1..30)
                'source_tx_ts': int (40-bit timestamp)
                'sf_counter': int
                'replies': list of (peer_id, reply_time_compressed, nlos_metric)
        """
        src = report['source_id']
        sf = report['sf_counter']
        tx_ts = report['source_tx_ts']

        key = (sf, src)
        entry = {
            'tx_ts': tx_ts,
            'replies': {}
        }
        for peer_id, reply_compressed, nlos_metric in report['replies']:
            # Decompress: reply_time in ticks = compressed << 8
            reply_ticks = reply_compressed << 8
            entry['replies'][peer_id] = (reply_ticks, nlos_metric)

        self._buffer[key] = entry

        # Garbage-collect old superframes (keep last 4)
        if sf > self._last_sf + 10:
            old_keys = [k for k in self._buffer if k[0] < sf - 4]
            for k in old_keys:
                del self._buffer[k]

    def assemble(self, sf_counter: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Assemble the distance matrix D and weight matrix W for the given
        superframe, using data from SF(t-1) and SF(t).

        Returns:
            D: (n×n) distance matrix in meters. NaN where unavailable.
            W: (n×n) weight matrix in [0, 1]. 0 where unavailable.
        """
        n = self.n
        D = np.full((n, n), np.nan)
        W = np.zeros((n, n))
        np.fill_diagonal(D, 0.0)

        sf_prev = sf_counter - 1
        sf_curr = sf_counter

        for i in range(1, n + 1):
            for j in range(i + 1, n + 1):
                dist, weight = self._compute_pair(i, j, sf_prev, sf_curr)
                if dist is not None and weight > 0:
                    D[i-1, j-1] = dist
                    D[j-1, i-1] = dist
                    W[i-1, j-1] = weight
                    W[j-1, i-1] = weight

        self.D = D
        self.W = W
        self._last_sf = sf_counter
        return D, W

    def _compute_pair(self, i: int, j: int,
                      sf_prev: int, sf_curr: int) -> Tuple[Optional[float], float]:
        """
        Compute DS-TWR distance between nodes i and j using broadcasts
        from SF(t-1) = sf_prev and SF(t) = sf_curr.

        Assembly (assuming i < j, so slot i comes before slot j):

            Reply1 = reply_time_j→i at SF(sf_prev)
                   = TX(j, sf_prev) - RX(j←i, sf_prev)

            Reply2 = reply_time_i→j at SF(sf_curr)
                   = TX(i, sf_curr) - RX(i←j, sf_prev)

            Round1 = RX(i←j, sf_prev) - TX(i, sf_prev)
                   = [TX(i, sf_curr) - Reply2] - TX(i, sf_prev)
                   = ΔTX_i - Reply2
                   where ΔTX_i = TX(i, sf_curr) - TX(i, sf_prev) [node i clock]

            Round2 = RX(j←i, sf_curr) - TX(j, sf_prev)
                   = [TX(j, sf_curr) - Reply1_at_curr] - TX(j, sf_prev)
                   ... we need reply_time_j→i at SF(sf_curr) for Round2.

        Alternatively, reply_time_j→i at SF(sf_curr) exists if j heard i at SF(sf_curr).
            reply_time_j→i at SF(sf_curr) = TX(j, sf_curr) - RX(j←i, sf_curr)

        So:
            Round2 = RX(j←i, sf_curr) - TX(j, sf_prev)
                   = TX(j, sf_curr) - reply_time_j(i, sf_curr) - TX(j, sf_prev)
                   = ΔTX_j - reply_time_j(i, sf_curr)
                   where ΔTX_j = TX(j, sf_curr) - TX(j, sf_prev) [node j clock]

        Required data:
            - i's broadcast at sf_prev: tx_ts_i_prev
            - i's broadcast at sf_curr: tx_ts_i_curr, reply_time_i[j] = Reply2
            - j's broadcast at sf_prev: tx_ts_j_prev, reply_time_j[i] = Reply1
            - j's broadcast at sf_curr: tx_ts_j_curr, reply_time_j[i] = Reply1_curr (for Round2)
        """
        # Fetch buffered data
        key_i_prev = (sf_prev, i)
        key_i_curr = (sf_curr, i)
        key_j_prev = (sf_prev, j)
        key_j_curr = (sf_curr, j)

        for key in [key_i_prev, key_i_curr, key_j_prev, key_j_curr]:
            if key not in self._buffer:
                return None, 0.0

        data_i_prev = self._buffer[key_i_prev]
        data_i_curr = self._buffer[key_i_curr]
        data_j_prev = self._buffer[key_j_prev]
        data_j_curr = self._buffer[key_j_curr]

        # Check reply_times exist
        if j not in data_i_curr['replies']:  # i→j reply at sf_curr = Reply2
            return None, 0.0
        if i not in data_j_prev['replies']:  # j→i reply at sf_prev = Reply1
            return None, 0.0
        if i not in data_j_curr['replies']:  # j→i reply at sf_curr (for Round2)
            return None, 0.0

        reply2, nlos_i = data_i_curr['replies'][j]
        reply1, nlos_j_prev = data_j_prev['replies'][i]
        reply1_curr, nlos_j_curr = data_j_curr['replies'][i]

        # TX timestamps (40-bit, must handle wrap)
        tx_i_prev = data_i_prev['tx_ts']
        tx_i_curr = data_i_curr['tx_ts']
        tx_j_prev = data_j_prev['tx_ts']
        tx_j_curr = data_j_curr['tx_ts']

        # ΔTX_i = TX(i, sf_curr) - TX(i, sf_prev) [mod 2^40]
        MASK = 0xFFFFFFFFFF
        delta_tx_i = (tx_i_curr - tx_i_prev) & MASK
        delta_tx_j = (tx_j_curr - tx_j_prev) & MASK

        # Round1 = ΔTX_i - Reply2
        if delta_tx_i < reply2:
            # Wrap-around: shouldn't happen for 39ms interval, but handle it
            round1 = (delta_tx_i + (MASK + 1) - reply2) & MASK
        else:
            round1 = delta_tx_i - reply2

        # Round2 = ΔTX_j - Reply1_curr
        if delta_tx_j < reply1_curr:
            round2 = (delta_tx_j + (MASK + 1) - reply1_curr) & MASK
        else:
            round2 = delta_tx_j - reply1_curr

        # Sanity checks: all intervals should be positive and < 0.5s
        limit = int(0.5 / DW_TICK_PERIOD)  # ~31.9 billion ticks
        for val in [round1, reply1, round2, reply2]:
            if val == 0 or val > limit:
                return None, 0.0

        # DS-TWR formula: ToF = (R1*R2 - D1*D2) / (R1+R2+D1+D2)
        r1 = float(round1)
        d1 = float(reply1)
        r2 = float(round2)
        d2 = float(reply2)

        denom = r1 + r2 + d1 + d2
        if denom <= 0:
            return None, 0.0

        tof = (r1 * r2 - d1 * d2) / denom
        distance = tof * DW_TICK_PERIOD * SPEED_OF_LIGHT

        # Subtract antenna delay bias
        distance -= self.ant_delay_bias

        if not np.isfinite(distance) or distance < 0 or distance > 1000:
            return None, 0.0

        # Weight based on NLOS metrics
        # Use the worst NLOS metric from the pair
        nlos_max = max(nlos_i, nlos_j_prev, nlos_j_curr)
        nlos_db = nlos_max * (20.0 / 255.0)  # Scale back to dB

        if nlos_db <= self.nlos_threshold:
            weight = 1.0
        else:
            # Exponential decay above threshold
            weight = np.exp(-(nlos_db - self.nlos_threshold) / 3.0)

        weight = max(0.01, min(1.0, weight))

        return distance, weight


# ═══════════════════════════════════════════════════════════════════════════════
# CLASS 2: WeightedSMACOFSolver
# ═══════════════════════════════════════════════════════════════════════════════

class WeightedSMACOFSolver:
    """
    Iterative Weighted Multidimensional Scaling via SMACOF (Scaling by
    MAjorizing a COmplicated Function) using the Guttman Transform.

    Minimizes the weighted Stress function:
        σ(X) = Σ_{i<j} w_ij × (d_ij - ‖x_i - x_j‖)²

    where d_ij are measured distances and x_i are estimated positions.

    The Guttman update rule:
        X^{k+1} = V⁺ · B(X^{k}) · X^{k}

    where:
        V = weighted degree Laplacian: V_ii = Σ_{j≠i} w_ij, V_ij = -w_ij
        V⁺ = Moore-Penrose pseudoinverse of V
        B(X) = matrix defined by:
            B_ij(X) = -w_ij × d_ij / ‖x_i - x_j‖   if i≠j and ‖x_i-x_j‖>0
            B_ij(X) = 0                                if i≠j and ‖x_i-x_j‖=0
            B_ii(X) = -Σ_{j≠i} B_ij(X)

    Initialization:
        - Warm-start from previous frame (X_prev) if available.
        - Cold-start using Classical MDS (Torgerson) with Floyd-Warshall
          shortest-path imputation for missing distances.
    """

    def __init__(self, n_nodes: int = N_NODES, dim: int = 2,
                 max_iter: int = 15, tol: float = 1e-5):
        """
        Args:
            n_nodes: Number of nodes.
            dim: Dimensionality (2 for cockroaches on a surface).
            max_iter: Maximum SMACOF iterations per frame.
            tol: Convergence tolerance on relative stress change.
        """
        self.n = n_nodes
        self.dim = dim
        self.max_iter = max_iter
        self.tol = tol
        self.X_prev: Optional[np.ndarray] = None  # Warm-start coordinates

    def solve(self, D: np.ndarray, W: np.ndarray,
              X_init: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Solve the weighted MDS problem.

        Args:
            D: (n×n) distance matrix. NaN for missing entries.
            W: (n×n) weight matrix. 0 for missing entries.
            X_init: Optional (n×dim) initial coordinates. If None, uses
                    warm-start from previous frame or Classical MDS.

        Returns:
            X: (n×dim) estimated coordinates.
        """
        n = self.n
        d = self.dim

        # Replace NaN in D with 0 (irrelevant where W=0)
        D_clean = np.nan_to_num(D, nan=0.0)
        W_clean = W.copy()

        # Ensure symmetry
        D_clean = (D_clean + D_clean.T) / 2.0
        W_clean = (W_clean + W_clean.T) / 2.0
        np.fill_diagonal(D_clean, 0.0)
        np.fill_diagonal(W_clean, 0.0)

        # Build V (weighted Laplacian)
        V = self._build_V(W_clean)

        # Compute V⁺ (Moore-Penrose pseudoinverse)
        V_pinv = pinv(V)

        # Initialize X
        if X_init is not None:
            X = X_init.copy()
        elif self.X_prev is not None:
            X = self.X_prev.copy()
        else:
            # Cold start: Classical MDS (Torgerson)
            X = self._classical_mds(D_clean, W_clean)

        # SMACOF iterations
        stress_prev = self._compute_stress(X, D_clean, W_clean)

        for iteration in range(self.max_iter):
            # Build B(X)
            B = self._build_B(X, D_clean, W_clean)

            # Guttman update: X_new = V⁺ · B(X) · X
            X_new = V_pinv @ B @ X

            # Compute stress
            stress = self._compute_stress(X_new, D_clean, W_clean)

            # Check convergence
            if stress_prev > 0:
                rel_change = abs(stress_prev - stress) / stress_prev
                if rel_change < self.tol:
                    X = X_new
                    break

            X = X_new
            stress_prev = stress

        # Store for warm-start
        self.X_prev = X.copy()
        return X

    def _build_V(self, W: np.ndarray) -> np.ndarray:
        """
        Build the weighted degree Laplacian matrix V (vectorized).

        V_ii = Σ_{j≠i} w_ij
        V_ij = -w_ij     (i ≠ j)
        """
        V = -W.copy()
        np.fill_diagonal(V, np.sum(W, axis=1))
        return V

    def _build_B(self, X: np.ndarray, D: np.ndarray, W: np.ndarray) -> np.ndarray:
        """
        Build the Guttman matrix B(X) — fully vectorized.

        B_ij(X) = -w_ij × d_ij / ‖x_i - x_j‖   if i≠j and ‖x_i-x_j‖ > eps
        B_ij(X) = 0                                if i≠j and ‖x_i-x_j‖ ≤ eps
        B_ii(X) = -Σ_{j≠i} B_ij(X)
        """
        # Pairwise distances in current embedding (vectorized)
        diff = X[:, np.newaxis, :] - X[np.newaxis, :, :]  # (n, n, dim)
        dist_X = np.sqrt(np.sum(diff ** 2, axis=2))        # (n, n)

        eps = 1e-10
        # Safe division: where dist_X > eps, compute ratio; else 0
        ratio = np.zeros_like(dist_X)
        valid = dist_X > eps
        ratio[valid] = D[valid] / dist_X[valid]

        # B_ij = -w_ij * d_ij / dist(x_i, x_j) for off-diagonal
        B = -W * ratio
        np.fill_diagonal(B, 0.0)

        # B_ii = -Σ_{j≠i} B_ij
        np.fill_diagonal(B, -np.sum(B, axis=1))

        return B

    def _compute_stress(self, X: np.ndarray, D: np.ndarray,
                        W: np.ndarray) -> float:
        """
        Compute weighted Stress (vectorized):
        σ(X) = Σ_{i<j} w_ij × (d_ij - ‖x_i - x_j‖)²
        """
        diff = X[:, np.newaxis, :] - X[np.newaxis, :, :]
        dist_X = np.sqrt(np.sum(diff ** 2, axis=2))
        residual = D - dist_X
        # Only upper triangle (i < j), weighted
        mask = np.triu(np.ones_like(W, dtype=bool), k=1)
        stress = np.sum(W[mask] * residual[mask] ** 2)
        return stress

    def _classical_mds(self, D: np.ndarray, W: np.ndarray) -> np.ndarray:
        """
        Classical MDS (Torgerson) with Floyd-Warshall shortest-path imputation
        for missing distance entries.

        Steps:
            1. Impute missing distances using shortest-path through the graph.
            2. Double-center the squared distance matrix.
            3. Extract top-d eigenvectors.
        """
        n = self.n
        d = self.dim

        # Create the graph: use measured distances where available,
        # infinity where not.
        graph = np.full((n, n), np.inf)
        np.fill_diagonal(graph, 0.0)
        for i in range(n):
            for j in range(i + 1, n):
                if W[i, j] > 0 and D[i, j] > 0:
                    graph[i, j] = D[i, j]
                    graph[j, i] = D[i, j]

        # Floyd-Warshall shortest paths
        D_full = csgraph.shortest_path(graph, method='FW', directed=False)

        # Handle disconnected components: replace inf with mean distance
        finite_mask = np.isfinite(D_full)
        if not np.all(finite_mask):
            mean_dist = np.mean(D_full[finite_mask]) if np.any(finite_mask) else 1.0
            D_full[~finite_mask] = mean_dist

        # Squared distance matrix
        D2 = D_full ** 2

        # Double centering: B = -0.5 * J * D² * J, where J = I - (1/n)*11ᵀ
        H = np.eye(n) - np.ones((n, n)) / n
        B = -0.5 * H @ D2 @ H

        # Eigendecomposition (B is symmetric)
        eigenvalues, eigenvectors = eigh(B)

        # Sort by descending eigenvalue
        idx = np.argsort(-eigenvalues)
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # Take top-d positive eigenvalues
        X = np.zeros((n, d))
        for k in range(d):
            if eigenvalues[k] > 0:
                X[:, k] = eigenvectors[:, k] * np.sqrt(eigenvalues[k])

        return X


# ═══════════════════════════════════════════════════════════════════════════════
# CLASS 3: OpportunisticKabschAligner
# ═══════════════════════════════════════════════════════════════════════════════

class OpportunisticKabschAligner:
    """
    Aligns the current coordinate frame X(t) to the previous X(t-1)
    using the Weighted Kabsch algorithm (Weighted Orthogonal Procrustes).

    Prevents reference frame drift by:
    1. Detecting stationary nodes (ZUPT — Zero-velocity UPdaTe) from
       small inter-frame distance variations.
    2. Assigning high weights (20-50×) to stationary nodes as
       "Opportunistic Anchors" that pin the coordinate system.
    3. Finding the optimal rotation R and translation t that minimizes:
       Σ_i α_i × ‖R·x_i(t) + t - x_i(t-1)‖²

    Also prevents mirror flips by checking det(R):
       If det(R) < 0 → flip the sign of the column of V corresponding
       to the smallest singular value.
    """

    def __init__(self, n_nodes: int = N_NODES,
                 zupt_threshold: float = 0.02,
                 zupt_min_neighbors: int = 3,
                 anchor_weight_factor: float = 30.0):
        """
        Args:
            n_nodes: Number of nodes.
            zupt_threshold: Max distance variance for ZUPT detection (meters).
            zupt_min_neighbors: Min neighbors needed for ZUPT classification.
            anchor_weight_factor: Weight multiplier for stationary nodes.
        """
        self.n = n_nodes
        self.zupt_threshold = zupt_threshold
        self.zupt_min_neighbors = zupt_min_neighbors
        self.anchor_weight_factor = anchor_weight_factor

        self.X_prev: Optional[np.ndarray] = None
        self.D_prev: Optional[np.ndarray] = None
        self.stationary_mask = np.zeros(n_nodes, dtype=bool)

    def align(self, X_curr: np.ndarray,
              D_curr: np.ndarray, W_curr: np.ndarray) -> np.ndarray:
        """
        Align X_curr to the previous frame's coordinate system.

        Args:
            X_curr: (n×2) current MDS solution.
            D_curr: (n×n) current distance matrix.
            W_curr: (n×n) current weight matrix.

        Returns:
            X_aligned: (n×2) aligned coordinates.
        """
        if self.X_prev is None or self.D_prev is None:
            # First frame — just save and return
            self.X_prev = X_curr.copy()
            self.D_prev = D_curr.copy()
            return X_curr

        n = self.n

        # ── Step 1: Detect stationary nodes (ZUPT) ──
        self.stationary_mask = self._detect_stationary(D_curr, W_curr)
        n_stationary = np.sum(self.stationary_mask)

        # ── Step 2: Build alignment weights ──
        # All active nodes get weight 1.0; stationary nodes get boosted
        alpha = np.ones(n)
        for i in range(n):
            # Only include nodes that were present in both frames
            if np.all(np.isnan(X_curr[i])) or np.all(np.isnan(self.X_prev[i])):
                alpha[i] = 0.0
            elif self.stationary_mask[i]:
                alpha[i] = self.anchor_weight_factor

        total_weight = np.sum(alpha)
        if total_weight < 1e-10:
            # No valid points — can't align
            self.X_prev = X_curr.copy()
            self.D_prev = D_curr.copy()
            return X_curr

        # ── Step 3: Weighted Kabsch alignment ──
        X_aligned = self._weighted_kabsch(X_curr, self.X_prev, alpha)

        # Update state
        self.X_prev = X_aligned.copy()
        self.D_prev = D_curr.copy()

        return X_aligned

    def _detect_stationary(self, D_curr: np.ndarray,
                           W_curr: np.ndarray) -> np.ndarray:
        """
        Detect stationary nodes using ZUPT criterion.

        A node i is considered stationary if:
        - It has at least zupt_min_neighbors valid connections
        - For a sufficient number of neighbor pairs (i,j), the distance
          variation |D_curr[i,j] - D_prev[i,j]| < zupt_threshold

        This is more robust than checking individual distances because
        if BOTH i and j are stationary, their mutual distance shouldn't change.
        """
        n = self.n
        stationary = np.zeros(n, dtype=bool)

        if self.D_prev is None:
            return stationary

        for i in range(n):
            stable_count = 0
            total_count = 0

            for j in range(n):
                if i == j:
                    continue
                if W_curr[i, j] > 0 and np.isfinite(D_curr[i, j]):
                    d_prev = self.D_prev[i, j]
                    if np.isfinite(d_prev):
                        total_count += 1
                        delta = abs(D_curr[i, j] - d_prev)
                        if delta < self.zupt_threshold:
                            stable_count += 1

            if total_count >= self.zupt_min_neighbors:
                # If >70% of neighbors show stable distance → stationary
                if stable_count / total_count > 0.7:
                    stationary[i] = True

        return stationary

    def _weighted_kabsch(self, P: np.ndarray, Q: np.ndarray,
                         weights: np.ndarray) -> np.ndarray:
        """
        Weighted Kabsch algorithm: find R, t that minimize
            Σ_i w_i × ‖R·p_i + t - q_i‖²

        Steps:
            1. Compute weighted centroids.
            2. Center both point sets.
            3. Build weighted cross-covariance matrix H.
            4. SVD(H) → R = V · S · Uᵀ, where S corrects for reflections.
            5. Compute t = q̄ - R·p̄.
            6. Apply transform to all points.

        Mirror/flip prevention:
            If det(V·Uᵀ) < 0, negate the column of V corresponding to
            the smallest singular value.

        Args:
            P: (n×d) source points (current frame).
            Q: (n×d) target points (previous frame).
            weights: (n,) per-point weights.

        Returns:
            P_aligned: (n×d) transformed source points.
        """
        n = P.shape[0]
        d = P.shape[1]
        w = weights.copy()
        w_sum = np.sum(w)

        if w_sum < 1e-10:
            return P.copy()

        # Weighted centroids
        centroid_P = np.sum(w[:, None] * P, axis=0) / w_sum
        centroid_Q = np.sum(w[:, None] * Q, axis=0) / w_sum

        # Center
        P_centered = P - centroid_P
        Q_centered = Q - centroid_Q

        # Weighted cross-covariance: H = Σ_i w_i × p_i_centered ⊗ q_i_centered
        # H = P_centered.T @ diag(w) @ Q_centered
        H = (P_centered * w[:, None]).T @ Q_centered

        # SVD
        U, S, Vt = svd(H)
        V = Vt.T

        # Detect reflection: if det(V · Uᵀ) < 0, flip
        det_sign = np.linalg.det(V @ U.T)
        if det_sign < 0:
            # Flip the column of V corresponding to smallest singular value
            V[:, -1] *= -1

        # Rotation matrix
        R = V @ U.T

        # Translation
        t = centroid_Q - R @ centroid_P

        # Apply to all points
        P_aligned = (R @ P.T).T + t

        return P_aligned


# ═══════════════════════════════════════════════════════════════════════════════
# CLASS 4: SwarmEKFTracker
# ═══════════════════════════════════════════════════════════════════════════════

class SwarmEKFTracker:
    """
    Extended Kalman Filter for 30 cockroach nodes.

    State per node: [x, y, vx, vy] (position + velocity in 2D).
    Motion model: Constant Velocity with process noise.
    Measurement: position from SMACOF + Kabsch alignment.

    Features:
    - Velocity clamping: v_max = 1.0 m/s (cockroach physical limit)
    - Mahalanobis distance gating: reject outlier measurements
    - Independent filter per node (no cross-coupling)
    """

    def __init__(self, n_nodes: int = N_NODES,
                 dt: float = 1.0 / SUPERFRAME_HZ,
                 process_noise_pos: float = 0.005,
                 process_noise_vel: float = 0.5,
                 measurement_noise: float = 0.05,
                 v_max: float = V_MAX,
                 gate_threshold: float = 9.21):
        """
        Args:
            n_nodes: Number of nodes.
            dt: Time step (seconds).
            process_noise_pos: Position process noise σ² (m²).
            process_noise_vel: Velocity process noise σ² ((m/s)²).
            measurement_noise: Measurement noise σ² (m²).
            v_max: Maximum allowed velocity (m/s).
            gate_threshold: Mahalanobis distance² threshold for gating.
                           9.21 ≈ chi²(2, 0.01) — 99% gate for 2D.
        """
        self.n = n_nodes
        self.dt = dt
        self.v_max = v_max
        self.gate_threshold = gate_threshold

        # State: x[i] = [x, y, vx, vy]
        self.x = np.zeros((n_nodes, 4))
        self.P = np.zeros((n_nodes, 4, 4))
        self.initialized = np.zeros(n_nodes, dtype=bool)

        # State transition matrix F (constant velocity)
        self.F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1]
        ])

        # Process noise covariance Q
        # Derived from piecewise constant white noise acceleration model:
        # Q = integral of F·G·q·Gᵀ·Fᵀ dt
        dt2 = dt * dt
        dt3 = dt2 * dt / 2
        dt4 = dt2 * dt2 / 4
        q_p = process_noise_pos
        q_v = process_noise_vel
        self.Q = np.array([
            [dt4*q_v + q_p,  0,             dt3*q_v,       0],
            [0,              dt4*q_v + q_p, 0,             dt3*q_v],
            [dt3*q_v,        0,             dt2*q_v,       0],
            [0,              dt3*q_v,       0,             dt2*q_v]
        ])

        # Measurement matrix H: we observe [x, y]
        self.H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ])

        # Measurement noise R
        self.R = np.eye(2) * measurement_noise

        # Initial covariance
        self._P0 = np.diag([1.0, 1.0, 0.5, 0.5])

    def predict(self):
        """Predict step for all initialized nodes."""
        for i in range(self.n):
            if not self.initialized[i]:
                continue
            self.x[i] = self.F @ self.x[i]
            self.P[i] = self.F @ self.P[i] @ self.F.T + self.Q
            self._clamp_velocity(i)

    def update(self, positions: np.ndarray):
        """
        Update step: incorporate position measurements from MDS pipeline.

        Args:
            positions: (n×2) measured positions. NaN rows are skipped.
        """
        for i in range(self.n):
            z = positions[i]
            if np.any(np.isnan(z)):
                continue

            if not self.initialized[i]:
                # Initialize state from first measurement
                self.x[i] = np.array([z[0], z[1], 0.0, 0.0])
                self.P[i] = self._P0.copy()
                self.initialized[i] = True
                continue

            # Innovation
            z_pred = self.H @ self.x[i]
            y = z - z_pred

            # Innovation covariance
            S = self.H @ self.P[i] @ self.H.T + self.R

            # Mahalanobis distance gating
            S_inv = np.linalg.inv(S)
            mahal_sq = y @ S_inv @ y

            if mahal_sq > self.gate_threshold:
                # Outlier — reject this measurement
                continue

            # Kalman gain
            K = self.P[i] @ self.H.T @ S_inv

            # State update
            self.x[i] = self.x[i] + K @ y

            # Covariance update (Joseph form for numerical stability)
            I_KH = np.eye(4) - K @ self.H
            self.P[i] = I_KH @ self.P[i] @ I_KH.T + K @ self.R @ K.T

            # Clamp velocity
            self._clamp_velocity(i)

    def _clamp_velocity(self, i: int):
        """Clamp velocity to v_max for node i."""
        vx, vy = self.x[i, 2], self.x[i, 3]
        speed = np.sqrt(vx**2 + vy**2)
        if speed > self.v_max:
            scale = self.v_max / speed
            self.x[i, 2] *= scale
            self.x[i, 3] *= scale

    def get_positions(self) -> np.ndarray:
        """Get filtered positions for all nodes. NaN for uninitialized."""
        pos = np.full((self.n, 2), np.nan)
        for i in range(self.n):
            if self.initialized[i]:
                pos[i] = self.x[i, :2]
        return pos

    def get_velocities(self) -> np.ndarray:
        """Get filtered velocities for all nodes."""
        vel = np.full((self.n, 2), np.nan)
        for i in range(self.n):
            if self.initialized[i]:
                vel[i] = self.x[i, 2:4]
        return vel


# ═══════════════════════════════════════════════════════════════════════════════
# CLASS 5: SwarmSimulationAndVisualizer
# ═══════════════════════════════════════════════════════════════════════════════

class SwarmSimulationAndVisualizer:
    """
    Full simulation + visualization of the 30-node cooperative localization
    pipeline.

    Simulates:
    - 30 cockroaches with Stop-and-Go motion (random bursts of movement
      followed by stationary periods)
    - UWB ranging with Gaussian noise + NLOS outliers
    - 20% random packet loss
    - Ground bounce / body attenuation effects

    Pipeline:
    1. Generate true positions → compute true distances
    2. Add noise, NLOS, packet loss → D, W matrices
    3. SMACOF → raw MDS solution
    4. Kabsch alignment → drift-corrected coordinates
    5. EKF → filtered trajectories

    Visualization: matplotlib animation comparing true vs estimated positions.
    """

    def __init__(self, n_nodes: int = N_NODES,
                 arena_size: float = 3.0,
                 duration_s: float = 20.0,
                 fps: float = SUPERFRAME_HZ,
                 range_noise_std: float = 0.05,
                 nlos_probability: float = 0.1,
                 nlos_bias_range: Tuple[float, float] = (0.1, 0.5),
                 packet_loss_rate: float = 0.20,
                 max_range: float = 5.0,
                 seed: int = 42):
        """
        Args:
            n_nodes: Number of cockroach nodes.
            arena_size: Arena side length (meters).
            duration_s: Simulation duration (seconds).
            fps: Frame rate matching superframe rate.
            range_noise_std: Gaussian ranging noise σ (meters).
            nlos_probability: Probability of NLOS error per measurement.
            nlos_bias_range: (min, max) NLOS additive bias in meters.
            packet_loss_rate: Probability of missing a measurement.
            max_range: Maximum reliable range (meters).
            seed: Random seed for reproducibility.
        """
        self.n = n_nodes
        self.arena = arena_size
        self.duration = duration_s
        self.fps = fps
        self.dt = 1.0 / fps
        self.n_frames = int(duration_s * fps)
        self.range_noise = range_noise_std
        self.nlos_prob = nlos_probability
        self.nlos_bias = nlos_bias_range
        self.packet_loss = packet_loss_rate
        self.max_range = max_range
        self.rng = np.random.RandomState(seed)

        # True trajectories: (n_frames, n_nodes, 2)
        self.true_positions = np.zeros((self.n_frames, n_nodes, 2))

        # Estimated trajectories
        self.est_positions = np.zeros((self.n_frames, n_nodes, 2))
        self.mds_positions = np.zeros((self.n_frames, n_nodes, 2))

        # Pipeline components
        self.smacof = WeightedSMACOFSolver(n_nodes, dim=2, max_iter=15, tol=1e-5)
        self.aligner = OpportunisticKabschAligner(n_nodes)
        self.ekf = SwarmEKFTracker(n_nodes, dt=self.dt)

        # Motion state per node
        self._motion_state = None

    def generate_trajectories(self):
        """
        Generate Stop-and-Go trajectories for 30 cockroaches.

        Each cockroach alternates between:
        - MOVING: Burst of ~0.5-2 seconds at 0.2-0.8 m/s in a random direction
        - STOPPED: Pause for 0.5-3 seconds

        Cockroaches bounce off arena walls.
        """
        n = self.n
        rng = self.rng

        # Initialize positions randomly in arena
        pos = rng.uniform(0.5, self.arena - 0.5, (n, 2))
        vel = np.zeros((n, 2))

        # Motion state: (is_moving, timer, direction)
        states = []
        for _ in range(n):
            states.append({
                'moving': False,
                'timer': rng.uniform(0.5, 2.0),  # Time until state change
                'speed': 0.0,
                'angle': rng.uniform(0, 2 * np.pi)
            })

        for t in range(self.n_frames):
            for i in range(n):
                s = states[i]
                s['timer'] -= self.dt

                if s['timer'] <= 0:
                    # Switch state
                    s['moving'] = not s['moving']
                    if s['moving']:
                        # Start moving: random speed and direction
                        s['speed'] = rng.uniform(0.2, 0.8)
                        s['angle'] = rng.uniform(0, 2 * np.pi)
                        s['timer'] = rng.uniform(0.5, 2.0)
                    else:
                        # Stop
                        s['speed'] = 0.0
                        s['timer'] = rng.uniform(0.5, 3.0)

                if s['moving']:
                    # Small random perturbation to direction (wobbly cockroach)
                    s['angle'] += rng.normal(0, 0.1)
                    vx = s['speed'] * np.cos(s['angle'])
                    vy = s['speed'] * np.sin(s['angle'])
                    pos[i, 0] += vx * self.dt
                    pos[i, 1] += vy * self.dt

                    # Bounce off walls
                    for dim in range(2):
                        if pos[i, dim] < 0.1:
                            pos[i, dim] = 0.1
                            s['angle'] = rng.uniform(0, np.pi) if dim == 0 else rng.uniform(-np.pi/2, np.pi/2)
                        if pos[i, dim] > self.arena - 0.1:
                            pos[i, dim] = self.arena - 0.1
                            s['angle'] = rng.uniform(np.pi, 2*np.pi) if dim == 0 else rng.uniform(np.pi/2, 3*np.pi/2)

            self.true_positions[t] = pos.copy()

    def simulate_ranging(self, true_pos: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Simulate UWB ranging measurements with noise, NLOS, and packet loss.

        Args:
            true_pos: (n×2) true positions.

        Returns:
            D: (n×n) measured distance matrix (NaN for missing).
            W: (n×n) weight matrix.
        """
        n = self.n
        rng = self.rng

        D = np.full((n, n), np.nan)
        W = np.zeros((n, n))
        np.fill_diagonal(D, 0.0)

        for i in range(n):
            for j in range(i + 1, n):
                true_dist = norm(true_pos[i] - true_pos[j])

                # Range limit
                if true_dist > self.max_range:
                    continue

                # Packet loss (20%)
                if rng.random() < self.packet_loss:
                    continue

                # Base measurement noise (Gaussian)
                noise = rng.normal(0, self.range_noise)
                measured = true_dist + noise

                # NLOS bias (additive positive error)
                nlos_metric = 0.0
                if rng.random() < self.nlos_prob:
                    nlos_bias = rng.uniform(*self.nlos_bias)
                    measured += nlos_bias
                    nlos_metric = rng.uniform(8, 18)  # dB above LOS

                if measured < 0:
                    measured = 0.01

                D[i, j] = measured
                D[j, i] = measured

                # Weight from NLOS metric
                if nlos_metric <= NLOS_THRESHOLD_DB:
                    w = 1.0
                else:
                    w = np.exp(-(nlos_metric - NLOS_THRESHOLD_DB) / 3.0)
                w = max(0.01, min(1.0, w))

                W[i, j] = w
                W[j, i] = w

        return D, W

    def run_pipeline(self):
        """
        Run the full localization pipeline on simulated data.
        """
        print(f"╔══════════════════════════════════════════════════════════════╗")
        print(f"║  Cyborg Swarm Localization — 30 Nodes, {self.n_frames} Frames           ║")
        print(f"║  Arena: {self.arena}m × {self.arena}m | Rate: {self.fps:.1f} Hz | Duration: {self.duration:.1f}s  ║")
        print(f"╚══════════════════════════════════════════════════════════════╝")
        print()

        # Generate ground truth trajectories
        print("[1/5] Generating cockroach trajectories (Stop-and-Go)...")
        self.generate_trajectories()

        # Process each frame through the pipeline
        rmse_per_frame = np.zeros(self.n_frames)
        print("[2/5] Running pipeline...")

        t0 = time.time()
        for t in range(self.n_frames):
            true_pos = self.true_positions[t]

            # Simulate ranging
            D, W = self.simulate_ranging(true_pos)

            # SMACOF (weighted MDS)
            X_mds = self.smacof.solve(D, W)

            # For the first frame, align MDS solution to true coordinates
            # (in real system, the coordinate frame is arbitrary)
            if t == 0:
                # Align to true for evaluation purposes
                X_mds = self._procrustes_align_to_truth(X_mds, true_pos)
                self.smacof.X_prev = X_mds.copy()

            # Kabsch alignment (reference frame stabilization)
            X_aligned = self.aligner.align(X_mds, D, W)

            # EKF predict + update
            self.ekf.predict()
            self.ekf.update(X_aligned)

            X_filtered = self.ekf.get_positions()

            # Store results
            self.mds_positions[t] = X_aligned
            self.est_positions[t] = X_filtered

            # Compute RMSE for this frame
            valid = ~np.isnan(X_filtered[:, 0])
            if np.any(valid):
                errors = norm(true_pos[valid] - X_filtered[valid], axis=1)
                rmse_per_frame[t] = np.sqrt(np.mean(errors ** 2))

            if (t + 1) % 100 == 0 or t == self.n_frames - 1:
                elapsed = time.time() - t0
                avg_rmse = np.mean(rmse_per_frame[max(0,t-99):t+1])
                print(f"  Frame {t+1:4d}/{self.n_frames}: "
                      f"RMSE = {avg_rmse*100:.1f} cm | "
                      f"Rate: {(t+1)/elapsed:.0f} frames/s")

        # Overall statistics
        valid_frames = rmse_per_frame > 0
        if np.any(valid_frames):
            # Skip first 10 frames (convergence period)
            converged = np.zeros(self.n_frames, dtype=bool)
            converged[10:] = valid_frames[10:]
            if np.any(converged):
                mean_rmse = np.mean(rmse_per_frame[converged]) * 100  # cm
                std_rmse = np.std(rmse_per_frame[converged]) * 100
                max_rmse = np.max(rmse_per_frame[converged]) * 100
                p95_rmse = np.percentile(rmse_per_frame[converged], 95) * 100
            else:
                mean_rmse = std_rmse = max_rmse = p95_rmse = float('nan')
        else:
            mean_rmse = std_rmse = max_rmse = p95_rmse = float('nan')

        print()
        print(f"╔══════════════════════════════════════════════════════════════╗")
        print(f"║  RESULTS (after convergence, frames 10+)                    ║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  Mean RMSE:   {mean_rmse:6.1f} cm                                   ║")
        print(f"║  Std  RMSE:   {std_rmse:6.1f} cm                                   ║")
        print(f"║  95th %-ile:  {p95_rmse:6.1f} cm                                   ║")
        print(f"║  Max  RMSE:   {max_rmse:6.1f} cm                                   ║")
        print(f"║  Total time:  {time.time()-t0:6.1f} s                                   ║")
        print(f"╚══════════════════════════════════════════════════════════════╝")

        self.rmse_per_frame = rmse_per_frame
        return mean_rmse

    def _procrustes_align_to_truth(self, X_est: np.ndarray,
                                    X_true: np.ndarray) -> np.ndarray:
        """
        Align estimated coordinates to true coordinates using unweighted
        Procrustes analysis. Used only for evaluation (not in real system).
        """
        valid = ~np.isnan(X_est[:, 0]) & ~np.isnan(X_true[:, 0])
        if np.sum(valid) < 3:
            return X_est

        P = X_est[valid]
        Q = X_true[valid]

        # Centroids
        cP = np.mean(P, axis=0)
        cQ = np.mean(Q, axis=0)

        # Center
        Pc = P - cP
        Qc = Q - cQ

        # SVD
        H = Pc.T @ Qc
        U, S, Vt = svd(H)
        V = Vt.T

        d = np.linalg.det(V @ U.T)
        if d < 0:
            V[:, -1] *= -1

        R = V @ U.T
        t = cQ - R @ cP

        return (R @ X_est.T).T + t

    def visualize(self, save_path: Optional[str] = None):
        """
        Create an animated visualization comparing true vs estimated positions.
        """
        print("\n[5/5] Creating visualization...")

        fig, axes = plt.subplots(1, 3, figsize=(20, 7))
        fig.patch.set_facecolor('#1a1a2e')
        fig.suptitle('Cyborg Swarm Localization — 30 Cockroach Nodes',
                     fontsize=16, fontweight='bold', color='white', y=0.98)

        titles = ['True Positions', 'MDS + Kabsch', 'EKF Filtered']
        colors = plt.cm.tab20(np.linspace(0, 1, self.n))

        for ax, title in zip(axes, titles):
            ax.set_facecolor('#16213e')
            ax.set_xlim(-0.2, self.arena + 0.2)
            ax.set_ylim(-0.2, self.arena + 0.2)
            ax.set_aspect('equal')
            ax.set_title(title, color='white', fontsize=13, fontweight='bold')
            ax.tick_params(colors='#888888')
            ax.grid(True, alpha=0.15, color='white')
            for spine in ax.spines.values():
                spine.set_color('#333333')

        # Scatter plots
        scatters_true = axes[0].scatter([], [], s=40, c=colors, zorder=5, edgecolors='white', linewidth=0.5)
        scatters_mds = axes[1].scatter([], [], s=40, c=colors, zorder=5, edgecolors='white', linewidth=0.5)
        scatters_ekf = axes[2].scatter([], [], s=40, c=colors, zorder=5, edgecolors='white', linewidth=0.5)

        # Trail lines (last 20 frames)
        trail_len = 20
        trail_lines_true = [axes[0].plot([], [], '-', color=colors[i], alpha=0.3, linewidth=1)[0] for i in range(self.n)]
        trail_lines_mds = [axes[1].plot([], [], '-', color=colors[i], alpha=0.3, linewidth=1)[0] for i in range(self.n)]
        trail_lines_ekf = [axes[2].plot([], [], '-', color=colors[i], alpha=0.3, linewidth=1)[0] for i in range(self.n)]

        # Frame counter text
        frame_text = fig.text(0.02, 0.02, '', fontsize=11, color='#cccccc',
                              fontfamily='monospace')
        rmse_text = fig.text(0.50, 0.02, '', fontsize=11, color='#00ff88',
                             fontfamily='monospace', ha='center')

        # Subsample for smooth animation
        skip = max(1, self.n_frames // 500)

        def update(frame_idx):
            t = frame_idx * skip
            if t >= self.n_frames:
                t = self.n_frames - 1

            # True positions
            tp = self.true_positions[t]
            scatters_true.set_offsets(tp)

            # MDS positions
            mp = self.mds_positions[t]
            valid_mds = ~np.isnan(mp[:, 0])
            if np.any(valid_mds):
                scatters_mds.set_offsets(mp[valid_mds])
            else:
                scatters_mds.set_offsets(np.empty((0, 2)))

            # EKF positions
            ep = self.est_positions[t]
            valid_ekf = ~np.isnan(ep[:, 0])
            if np.any(valid_ekf):
                scatters_ekf.set_offsets(ep[valid_ekf])
            else:
                scatters_ekf.set_offsets(np.empty((0, 2)))

            # Update trails
            start = max(0, t - trail_len * skip)
            for i in range(self.n):
                trail_t = self.true_positions[start:t+1:skip, i, :]
                trail_lines_true[i].set_data(trail_t[:, 0], trail_t[:, 1])

                trail_m = self.mds_positions[start:t+1:skip, i, :]
                valid = ~np.isnan(trail_m[:, 0])
                if np.any(valid):
                    trail_lines_mds[i].set_data(trail_m[valid, 0], trail_m[valid, 1])
                else:
                    trail_lines_mds[i].set_data([], [])

                trail_e = self.est_positions[start:t+1:skip, i, :]
                valid = ~np.isnan(trail_e[:, 0])
                if np.any(valid):
                    trail_lines_ekf[i].set_data(trail_e[valid, 0], trail_e[valid, 1])
                else:
                    trail_lines_ekf[i].set_data([], [])

            time_s = t * self.dt
            frame_text.set_text(f'Frame: {t:4d} | Time: {time_s:.1f}s')
            if t < len(self.rmse_per_frame):
                rmse_text.set_text(f'RMSE: {self.rmse_per_frame[t]*100:.1f} cm')

            return (scatters_true, scatters_mds, scatters_ekf,
                    frame_text, rmse_text)

        n_anim_frames = self.n_frames // skip
        anim = animation.FuncAnimation(fig, update, frames=n_anim_frames,
                                       interval=40, blit=False, repeat=True)

        plt.tight_layout(rect=[0, 0.05, 1, 0.95])

        if save_path:
            print(f"  Saving animation to {save_path}...")
            anim.save(save_path, writer='pillow', fps=25)
            print(f"  Saved.")

        plt.show()

    def plot_rmse_timeseries(self, save_path: Optional[str] = None):
        """Plot RMSE over time."""
        fig, ax = plt.subplots(figsize=(12, 5))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#16213e')

        time_axis = np.arange(self.n_frames) * self.dt
        rmse_cm = self.rmse_per_frame * 100

        ax.plot(time_axis, rmse_cm, color='#00ff88', linewidth=1, alpha=0.6,
                label='Instantaneous RMSE')

        # Running average (1 second window)
        window = int(self.fps)
        if window > 1:
            kernel = np.ones(window) / window
            rmse_smooth = np.convolve(rmse_cm, kernel, mode='same')
            ax.plot(time_axis, rmse_smooth, color='#ff6b6b', linewidth=2,
                    label=f'Running average ({1.0:.0f}s window)')

        ax.set_xlabel('Time (s)', color='white', fontsize=12)
        ax.set_ylabel('RMSE (cm)', color='white', fontsize=12)
        ax.set_title('Localization Error Over Time', color='white',
                     fontsize=14, fontweight='bold')
        ax.legend(facecolor='#16213e', edgecolor='#333333',
                  labelcolor='white', fontsize=10)
        ax.tick_params(colors='#888888')
        ax.grid(True, alpha=0.15, color='white')
        for spine in ax.spines.values():
            spine.set_color('#333333')

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, facecolor='#1a1a2e',
                        bbox_inches='tight')
        plt.show()


# ═══════════════════════════════════════════════════════════════════════════════
# UART STREAM PARSER (for real hardware)
# ═══════════════════════════════════════════════════════════════════════════════

class UARTStreamParser:
    """
    Parse the binary stream from the Sniffer node over UART/USB CDC.

    Protocol:
        [0xAA] [len_lo] [len_hi] [payload...] [CRC32]
    """

    def __init__(self):
        self._buf = bytearray()
        self._reports = deque()

    def feed(self, data: bytes):
        """Feed raw UART bytes into the parser."""
        self._buf.extend(data)
        self._try_parse()

    def get_report(self) -> Optional[dict]:
        """Get the next parsed report, or None if none available."""
        if self._reports:
            return self._reports.popleft()
        return None

    def _try_parse(self):
        """Try to parse complete frames from the buffer."""
        while len(self._buf) >= 7:  # Minimum: sync(1) + len(2) + type(1) + crc(4) = 8
            # Find sync byte
            sync_idx = self._buf.find(0xAA)
            if sync_idx < 0:
                self._buf.clear()
                return
            if sync_idx > 0:
                del self._buf[:sync_idx]

            if len(self._buf) < 3:
                return

            payload_len = struct.unpack_from('<H', self._buf, 1)[0]
            total_len = 3 + payload_len + 4  # sync + len + payload + crc

            if total_len > 512:
                # Corrupt — skip this sync byte
                del self._buf[0]
                continue

            if len(self._buf) < total_len:
                return  # Need more data

            # Extract frame
            frame = bytes(self._buf[:total_len])
            del self._buf[:total_len]

            # Verify CRC
            payload_data = frame[3:3 + payload_len]
            expected_crc = struct.unpack_from('<I', frame, 3 + payload_len)[0]
            actual_crc = _crc32(payload_data)

            if actual_crc != expected_crc:
                continue  # CRC mismatch, skip

            # Parse report
            report = self._parse_report(payload_data)
            if report:
                self._reports.append(report)

    def _parse_report(self, data: bytes) -> Optional[dict]:
        """Parse a single report payload."""
        if len(data) < 17 or data[0] != REPORT_TYPE_BROADCAST:
            return None

        source_id = data[1]
        source_tx_ts = int.from_bytes(data[2:7], 'little')
        sniffer_rx_ts = int.from_bytes(data[7:12], 'little')
        sf_counter = struct.unpack_from('<I', data, 12)[0]
        n_replies = data[16]

        replies = []
        offset = 17
        for _ in range(n_replies):
            if offset + 6 > len(data):
                break
            peer_id = data[offset]
            reply_time = struct.unpack_from('<I', data, offset + 1)[0]
            nlos_metric = data[offset + 5]
            replies.append((peer_id, reply_time, nlos_metric))
            offset += 6

        return {
            'source_id': source_id,
            'source_tx_ts': source_tx_ts,
            'sniffer_rx_ts': sniffer_rx_ts,
            'sf_counter': sf_counter,
            'replies': replies
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — Standalone Simulation
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """
    Run the full simulation pipeline:
    1. Generate 30 cockroach trajectories (Stop-and-Go)
    2. Simulate UWB ranging with noise + NLOS + packet loss
    3. Run SMACOF → Kabsch → EKF pipeline
    4. Print RMSE statistics
    5. Visualize results
    """
    sim = SwarmSimulationAndVisualizer(
        n_nodes=30,
        arena_size=3.0,
        duration_s=20.0,
        fps=25.6,
        range_noise_std=0.05,     # 5 cm Gaussian noise
        nlos_probability=0.10,     # 10% NLOS measurements
        nlos_bias_range=(0.1, 0.5),
        packet_loss_rate=0.20,     # 20% packet loss
        max_range=5.0,             # 5 m max range
        seed=42
    )

    mean_rmse = sim.run_pipeline()

    # Plot RMSE time series
    sim.plot_rmse_timeseries()

    # Animated visualization
    sim.visualize()

    return mean_rmse


if __name__ == '__main__':
    main()
