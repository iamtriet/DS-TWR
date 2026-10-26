# Virtual Geofencing

Start `Virtual_Geofencing.cmd` for the current COM9/six-board deployment. It uses
the unchanged tracking algorithm and opens the dedicated Control Center tab.
Keep the sniffer fixed. This is a planar, relative-heading mission workspace,
not a surveyed rubble map or a validated rescue navigation system.

1. Wait for tracking, then choose **Draw fence** and click opposite rectangle
   corners, or enter metre coordinates with **Fence coordinates**.
2. Choose **Start mission**. The task records each tag's observed path separately.
3. Select a tag and configure its **Biological profile**: maximum speed,
   accumulated movement budget, and quiet rest duration. Defaults (1 m/s,
   120 s, 30 s) are editable operator assumptions, not biological recommendations.
4. The table shows inside/outside/boundary-uncertain, BNO motion, activity budget,
   and advisory exploration/return/rest status. No stimulation command is sent.
5. After an external observation, use **Mark suspected victim** or **Mark confirmed
   victim**. Confirmed means operator confirmation, not algorithmic detection.
6. Select the V-number to display the associated tag's observed route up to that
   observation. A route beginning away from the sniffer, or containing an outage,
   is explicitly partial. No unobserved connector to the origin is invented.
7. **Export** writes a mission JSON and point CSV. **Open mission** reviews an
   archived mission without overlaying current live positions on its old frame.
   Autosaves are in `build/control-sessions/<session>/geofencing/` every 5 seconds,
   on pause and on normal application close.

## Data validity

Stale positions, estimated/inconsistent geometry, excessive conditional sigma,
and detected jumps break the trace into separate segments. Fence uncertainty
uses the larger of 20 cm or twice the conditional position sigma. These values
are software thresholds, not guarantees of absolute accuracy. Origin motion,
tracking session changes, board identity changes, and radio epoch changes
invalidate the current recording frame; create a new mission after verification.
Paths are stored at up to the 10 Hz UI sampling rate and normally approximately
5 Hz. Track exports use local metres and host monotonic seconds, with a UTC
mission creation timestamp. They are not geographic latitude/longitude.

## Biological scope and evidence

BNO085 provides a movement indication; it does not measure fatigue, stress,
intent, victim presence or whether the insect is trapped. Quiet periods can be
normal resting. Individual movement/rest profiles and boundary advisories are
the biological-interface portion of this first version. There is no camera,
automatic victim detector, or automatic electrical stimulation in this module.

Relevant primary research (design context, not validation of our defaults):
- https://arxiv.org/abs/2403.17392 — individual variability in swarm navigation.
- https://pmc.ncbi.nlm.nih.gov/articles/PMC10631459/ — habituation/control limits.
- https://pmc.ncbi.nlm.nih.gov/articles/PMC10915385/ — feedback navigation with
  additional sensors; its detection results do not imply this hardware detects people.

An insect traversing a route does not establish that rescuers can traverse it.
The current UWB error and relative-heading limitations remain present in saved
paths. Human passability is always exported as `not assessed`.

## Behavioral passage and obstacle hypotheses

The **Behavior map** overlay uses a 0.25 m grid in the same local XY frame:

- Green: reliable positions observed during resolved movement. Only sampled
  endpoints are marked; neither corridor width nor intervening free space is
  asserted. Repeated visits and contributing tag IDs are retained.
- Amber: an approach of at least 0.30 m followed by a retreat with a direction
  change greater than 120 degrees, above an uncertainty-dependent displacement
  threshold. The candidate is projected beyond the turnaround, not onto the
  point the insect successfully occupied.
- Red `?`: at least three separate turnaround episodes in that candidate cell,
  from two tags or spanning at least 20 seconds for one tag. Episodes from the
  same tag have a 10-second cooldown. This is a **suspected obstruction**, never
  a confirmed physical obstacle or calibrated probability.
- Blank: unknown; no inference is made from absent visits.

A later observed traversal of a candidate cell retracts its obstruction votes.
Stale/gapped tracks and conditional sigma over 0.10 m do not produce mapping
evidence. Quiet/rest or exhausted operator-set activity budget does not produce
obstacle votes. Fresh IMU activity with little resolved progress is labeled
`ACTIVE / LOW PROGRESS`; it does not establish blockage. A natural voluntary
turn, aversion, slope or tracking error can still resemble obstacle avoidance.

The model proposes `TRY ANOTHER APPROACH` on turnaround/low-progress evidence,
alongside existing boundary-return and rest advisories. It sends no stimulation
and does not plan a supposedly safe route through unobserved space. Click map
cells for their evidence count and tag provenance. Map cells, hypotheses and
contradictions are saved with the mission and restored in archive review. Older
mission files load with an empty behavior map.

These thresholds are engineering heuristics tested with synthetic trajectories,
not biological constants or validation on physical cockroaches/obstacles. The
tracking algorithm and firmware are unchanged by this feature.

### Automatic fences (2026-10-23)
Automatic fences are enabled for new missions. Start tracking with the stationary
sniffer as origin, open Virtual Geofencing, and press Start mission; no rectangle
is required. Blue dashed grid-union boundaries grow around quality-gated observed
passage cells. Disconnected passages and holes remain separate. Pink dashed
boundaries mark repeated-turnaround obstruction hypotheses, with uncertainty-aware
advisory warnings. They retract when later passage evidence contradicts them.
Unknown space is an exploration frontier, not a detected wall. Quiet stationary
boards do not create passage or obstruction evidence. No stimulation is sent.
Pause before changing mode. Drawing a rectangle selects manual mode. Mission JSON
stores mode, evidence, and generated cells; archive loading rebuilds boundaries
from evidence. Existing manual mission archives remain supported.

### Behavioral evidence model v2
Each accepted turn episode contributes q = 1 / (1 + (sigma / sigma_max)^2).
At time t, S(t) = sum(q_k * 2^(-(t-t_k)/half_life)). Defaults: half-life
300 seconds, activate at 2.4, release below 1.2, sigma_max 0.10 m. These are
experimental engineering parameters, not calibrated probabilities or validated
species constants. Activation additionally requires at least three episodes,
with two distinct nodes or at least 20 seconds between first and last episode.
The existing per-node 10-second turn cooldown prevents frame-rate vote inflation.
A single quiet interval or active low-progress interval is not an obstacle vote.

A historical passage no longer permanently vetoes a new obstruction hypothesis.
A passage within the previous 10 seconds suppresses an immediate contradictory
turn vote. A later accepted passage retracts the obstruction. Both activation and
release are recorded with timestamps. Mission time advances evidence age even
when new positions are unavailable; pausing freezes the displayed research map,
and resuming advances age across the pause. Archive review retains its recorded
age rather than comparing monotonic timestamps across different process sessions.

Inference settings are editable only before recording in a new mission. Evidence
shows per-cell score, contributing tags, episode counts, age, and retracted cells.
Export writes mission JSON, trajectory CSV, .evidence.csv and .fences.csv (local
XY metres, relative heading; not geodetic coordinates). JSON retains individual
turn votes, quality, projected target, tip position, model parameters and events.
Older missions remain readable with default model parameters. Grid boundaries
remain evidence support regions; no wall orientation or sub-cell accuracy is
claimed. Experiments moving PCBs by hand verify software responses only, not the
validity of the cockroach behavior model. That requires ground-truth obstacle
layouts and real animal trajectories, including false-turn control trials.

### Sniffer motion pulse recovery
A single fresh sniffer IMU moving flag temporarily holds mapping and breaks path
segments. Frame invalidation now requires at least four distinct fresh IMU samples
spanning >=1 second, with no intersample gap over 0.5 seconds. A fresh quiet sample
clears a brief hold automatically. Stale IMU cannot clear it or confirm motion.
The sniffer must still remain stationary; this is a debounce, not a displacement
estimator. Regression replay of 16,978 recorded snapshots remained running.

### Regions and motion plausibility guard (v3)
Virtual Geofencing now also shows quiet/rest regions (>=3 s with quiet IMU and
limited position spread), active low-progress regions (>=2 s), unexplored frontier
cells adjacent to observed passages, and a one-cell caution halo around inferred
obstructions. Rest/low-progress annotations expire after 30 s without evidence.
They never independently vote for an obstacle. The caution halo is a visual grid
buffer; advisory collision checks still use position uncertainty and mission margin.
Unexplored regions remain unknown. No polygons are fabricated to fill empty areas.

The mission-only TrajectoryGuard uses the configured individual BioProfile maximum
speed, in m/s. For accepted samples over a one-second window, the conservative
speed estimate is max(0, distance - 2*hypot(sigma_now,sigma_old)) / dt. Excess speed
quarantines the observation instead of adopting it as the next trusted baseline.
A coherent moving trajectory can be reacquired after >=1 s, opening a new segment.
With fresh quiet IMU, displacement from the quiet anchor over >=0.5 s exceeding
max(0.08 m, 3*hypot(sigmas)) is flagged as suspected drift. Jitter within uncertainty
is retained. Stale/missing data breaks continuity. Rejected points cannot expand
passages, create zones/obstacles, or be marked as victim positions. They remain
visible as orange outlined raw positions; this guard does not change UWB tracking.
The Speed table column is conservative speed / configured speed limit. Default
1 m/s is an operator setting, not a biological claim; set it per animal through
Biological profile. Speed alone cannot detect drift below the configured limit;
quiet IMU is corroboration, not a guarantee of immobility.

### Live visibility and IMU-only hold correction (2026-10-24)
Archive review is now an explicit flag independent of generation invalidation.
Paused/invalidated live missions continue to display live nodes; only an explicitly
opened archive hides live positions. Sniffer IMU moving alone holds mapping even
when longer than one second, but no longer permanently invalidates the frame.
Fresh quiet IMU resumes recording with path gaps. This relies on the operator's
stationary-sniffer configuration: if the sniffer is actually relocated, start a
new mission. Radio epoch/identity/frame changes still pause recording for review.
Replay of the observed failure recovered from the IMU hold; a later radio epoch
change correctly remained a separate pause reason.

### Finding view and always-visible paths
Virtual Geofencing Finding uses stronger region fills and a hatched caution halo,
matching the synthetic region trial's categories. All accepted node trajectories
remain visible, including when selecting a victim route (selection highlights its
node instead of hiding the other paths). Each node keeps a consistent trail color.
A separate display-only, dotted live history is updated even with mission recording
paused/held. It never contributes to regions, markers, or exported accepted paths.
History is bounded to 6000 points per node (~10 minutes at 10 Hz), does not bridge
gaps over 0.5 s or position jumps over 0.5 m, and resets on tracking generation,
origin or radio epoch changes. Accepted mission paths remain in the mission JSON.
Fit includes paths and regions. Start/New mission exits rectangle-drawing mode.

### Biological model disabled by request (2026-10-24)
New missions default to biological_enabled=false. Activity/rest budgets, fatigue
advice and the biological maximum-speed/quiet-drift TrajectoryGuard are bypassed.
The original UWB + IMU localization, quality gates, region inference from motion,
and visible trajectories remain unchanged. The profile editing control is hidden;
the table shows motion and measured position-change speed without biological
limits. Stationary observations are labeled STATIONARY ZONE, not inferred rest.
Existing archives retain their saved setting for historical review only.

### Region presentation redesign (2026-10-25)
The Finding view now composites cached translucent region overlays instead of
individual colored rectangles. Adjacent support cells merge visually, boundaries
round inward, and connected components receive concise labels. Soft gray denotes
unexplored frontier and coral denotes caution. Paths remain separate overlays.
Rendering clips smoothing to the original support, preserving holes and gaps;
inference, hit-testing, exports and tracking are unchanged. The display is an
approximation, not a claim of sub-cell obstacle geometry. Biological mode stays off.

### Scan-style map (2026-10-25)
Observed regions use overlapping circular sample footprints with white fill and
black outlines. Footprints draw only in supported observed cells; frontier and
caution predictions are no longer filled as discovered space. Suspected obstacles
receive red outlines, with no claimed solid interior. Fixed top-right legend
replaces region labels. Live/accepted trails and biological-off mode are unchanged.
This is a LiDAR-like presentation of UWB/behavior observations, not range sensing
of walls. Display footprint radius is 0.42 times grid resolution, not an animal
body dimension or a measured clearance. Unknown holes are not globally filled.

### Post-UWB rejection and additional obstacle evidence
PositionGate operates only on a deep-copied Control Center snapshot; UWB ranging,
solver coordinates, firmware and raw source logs are untouched. A 0.60 m/s trial
limit with 2-sigma allowance (sigma capped at 0.05 m for this gate) rejects abrupt
jumps from displayed trails and mission evidence. Rejected nodes show the last
accepted point, explicitly labeled. A coherent one-second candidate can be
reacquired with a path gap; actual displacement during an outage cannot be known.
Biological activity/rest modeling remains disabled. Filter configuration and
rejection transitions are saved in the mission for audit.

Closed orbits around an unvisited center contribute circling evidence (weight
0.9 before uncertainty scaling); approaching >=0.30 m then remaining active in a
confined area >=2 s contributes blocked-approach evidence (weight 0.65). Quiet rest,
straight passage, arbitrary partial curves, gaps and rejected samples do not add
these votes. Three or more independent episodes and the existing weighted score
and diversity requirements still govern red suspected-obstacle status. Preliminary
votes now appear as amber outlines instead of being invisible. Amber/red are
hypotheses, not proof of a physical wall. Sustained straight travel alone does not
imply an obstacle. Per-node 10 s cooldown prevents one episode counting repeatedly.

### Strict display speed gate (2026-10-26)
Supersedes the uncertainty allowance and timed reacquisition described above.
Tracking and Virtual Geofencing now share one post-localization gate, with a
0.60 m/s limit. Displacement is measured from the last accepted position using
the latest observed sample interval, capped at 0.20 s. No additive uncertainty
allowance, master-election reset, outage reset, or timed remote reacquisition
is allowed. Rejected samples hold the last accepted marker (orange HELD), do
not enter mission paths or mapping evidence, and do not enter live trails.
The UWB solver and raw capture are unchanged. latest-display.json records the
filtered snapshot separately from raw latest.json. This is rejection, not
speed-clamping or a guarantee of physical accuracy. A sustained offset may
remain held until a plausible sample returns; starting a new tracking session
establishes a new baseline. The configured limit is an operational threshold,
not a measured biological speed limit.
At swarm startup the display waits for all configured positions and one second after full-fleet availability before establishing the initial speed baseline. This does not alter the raw solver output.

### Reconnection, reacquisition and trajectory clearing (2026-10-26)
The 0.60 m/s gate still rejects instantaneous jumps. A new location may now be
acquired from at least ten fresh measured-supported samples over 1.5 s with
sigma <= 0.08 m, consistent geometry, gaps <= 0.25 s, and either an 0.08 m
compact cluster or a speed-consistent slow trajectory. The acquisition sample
is labeled reacquired and excluded from mapping/trails; subsequent samples
start a new segment. This does not establish the path taken during the gap,
and a stable biased solution can still be wrong. Missing nodes retain their
last accepted marker rather than disappearing.
BLE recovery probes stale UWB nodes after 5 s, retries no more often than
10 s per node, and restarts even a ready/running radio on repeated silence.
Each attempt is bounded to 20 s. BLE recovery waits while UART is disconnected.
Clear trajectories archives the mission before clearing paths and route
markers in both views. Existing map evidence and node positions remain.
Behavior evidence v5 observes up to 20 s of continuous accepted movement for
slow loops and combines targets within 0.1625 m at the default map resolution.
The repeated-evidence and traversal-contradiction requirements still apply;
red contours remain suspected obstacles, not confirmed physical occupancy.


### Quiet hold and manual UV (2026-10-26)
A fresh IMU quiet flag (<300 ms) sustained for 400 ms locks a recently accepted
position. It is a zero-velocity display/mapping constraint, not a changed UWB
solver or improved range measurement. Motion releases the hold immediately;
stale/unknown IMU also releases it. A quiet classification cannot prove zero
velocity in every situation (e.g. very smooth movement). Invalid UWB samples
still cannot become map evidence. Raw UWB logs remain available.
Click node markers to toggle a multi-selection in either tracking canvas.
The same selection feeds UV left, right, both, off and status controls.
Commands use BLE, verify hardware identity, serialize with recovery, and report
each board's response. They do not stop UWB tracking. Firmware 1.2.7 adds
led aux status/off, led aux uv LEFT RIGHT and led aux rgb R1 G1 B1 R2 G2 B2
(binary on/off levels). Firmware defaults to no auxiliary output at boot;
UV mapping follows HARDWARE.md P0.30/P0.02. The RGB daughterboard mapping is
G1=P0.30, B1=P0.31, R2=P0.27, G2=P0.07, B2=P0.23. R1 is unsupported because
JP1 pin 3 is DW3110 GPIO3; requests to enable R1 return ENOTSUP.
