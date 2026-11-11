# Disaster communication using MANET & AODV: simulation

Discrete-event simulator written in Python. It compares **standard AODV** with our
**AODV-DR (Disaster-Ready)** protocol: SOS priority lane + battery-aware routes + store-and-carry.

## Files
| File | What it is |
|---|---|
| `manet_sim.py` | The simulator: mobility, radio, batteries, failures, AODV (RREQ / RREP / RERR) and our 3 enhancements |
| `live_demo.py` | Live animated simulation to run in front of the teacher |
| `run_experiments.py` | Runs all experiments from slide 10 (180 runs) and writes `results.csv` |
| `make_plots.py` | Makes the graphs in `graphs/` and the table `graphs/summary.md` |

Needs Python 3 and matplotlib (`pip install matplotlib`).

## Demo script for tomorrow (about 5 minutes)

**1. Asha's story, live (slides 7 and 8):**
```
python live_demo.py
```
- Watch Asha's first SOS: yellow rings = **RREQ flood**, green arrows = **RREP** coming back, red arrows = **SOS data**.
  The thick red line is the route AODV found (e.g. Asha > N1 > N3 > Base).
- **Click the middle relay on the red line** to cut its power. The next SOS hits the broken link,
  an orange **RERR** goes back to Asha, Asha floods a new RREQ, and the route switches to the other side.
  The event log on the right narrates every step.
- Press `p` to switch to AODV-DR and repeat. Press `space` to pause while explaining, `+` / `-` for speed.

**2. The full disaster zone:**
```
python live_demo.py field
```
40 rescuers walking around a 1 km x 1 km area; phones colour from green to red as batteries drain.
Phones with a red ring are SOS senders. Click several phones to simulate power failures and watch the
"SOS delivered" counter on the right. Press `p` to compare protocols.

**3. The experiments (if she asks "where are the results from?"):**
```
python run_experiments.py
python make_plots.py
```
Takes 10-15 minutes on this laptop (180 simulations), so start it at the beginning of the presentation
or show the graphs already in `graphs/`.

## Setup (slide 10)
| Parameter | Value |
|---|---|
| Area | 1000 m x 1000 m, Base camp fixed at (900, 500) |
| Phones | 20 / 40 / 60 / 80 |
| Mobility | Random waypoint, 0.5-1.5 m/s (walking), pause 0-30 s |
| Radio | 250 m range, 2 Mbit/s shared channel, carrier sense, MAC retries |
| Traffic | 5 SOS senders (128 B text + GPS every 2 s) + 10 background flows (1 KB photos/status, 20 per s) |
| Failures | 0 / 10 / 20 / 30 % of relays lose power at random times between 40 s and 160 s |
| Batteries | Random 20-100 % charge; transmit, receive and idle all drain it |
| Runs | 200 s each, 10 random seeds per point, mean reported |

## Results (40 phones, 10 % failures)
| Metric | Standard AODV | AODV-DR (ours) |
|---|---|---|
| SOS delivery ratio | 91.5 % | **98.8 %** |
| Route recovery time (median) | 257 ms | **185 ms** |
| Routing overhead (control pkts per delivered pkt) | 0.11 | **0.10** |
| SOS delay (median) | **52 ms** | 204 ms |
| Ordinary traffic delivered | **83 %** | 77 % |

Across network sizes AODV-DR delivers more SOS messages every time (20 phones: 44 % to 53 %;
60 and 80 phones: 100 %). Full table: `graphs/summary.md`.

### How to explain the results honestly
- **The headline:** almost every SOS gets through (98.8 % vs 91.5 %), with *less* routing chatter.
- **Why the delay is higher:** store-and-carry *keeps* SOS messages that standard AODV *throws away*
  when no route exists, and delivers them later. A message that arrives 0.2 s later is far better
  than one that never arrives. All delays are still well under a second.
- **The trade-off:** ordinary traffic (photos) drops slightly, because SOS goes first. That is the
  intended design: life-threatening messages beat photo uploads.
- **Ablation (`fig3_ablation.png`):** the SOS priority lane alone gives +3 % delivery and store-and-carry alone halves
  recovery time. **Battery-aware routing on its own did not help** in our model, because listening
  (overhearing neighbours) costs as much battery as relaying. That is an honest finding and future work
  (combine it with sleep cycles, as on the "Honest limits" slide).
- **20 phones** is too sparse: the network is often cut into pieces, so every protocol struggles.

### Simplifications (say these if asked)
Written in Python. It models the AODV logic (RFC 3561: RREQ/RREP/RERR, sequence
numbers, route timeouts, RREQ retries) but uses a simpler radio: no packet collisions, a fixed
250 m range instead of signal fading, and no expanding-ring search.
