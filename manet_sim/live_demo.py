"""
live_demo.py - run the MANET simulation live, in a window, in front of an audience.

    python live_demo.py            Asha's story (6 phones, slides 7 and 8)
    python live_demo.py field      40 rescuers walking around a 1 km x 1 km disaster zone

Controls
    click a phone   cut its power (watch AODV detect the break and repair the route)
    space           pause / resume
    + / -           faster / slower
    p               switch protocol: standard AODV <-> AODV-DR (ours), restarts
    r               restart
"""
import heapq
import sys
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Circle

from manet_sim import BROADCAST, VARIANTS, Config, Sim

SCENE = sys.argv[1] if len(sys.argv) > 1 else "asha"

COL = {"Q": "#e6a700", "P": "#1a9850", "R": "#f46d43", "S": "#d7191c", "D": "#6b8fb3"}
NAME = {"Q": "RREQ", "P": "RREP", "R": "RERR", "S": "SOS", "D": "data"}


def make_config(variant):
    if SCENE == "field":
        cfg = Config(n_nodes=40, fail_frac=0.0, sim_time=900, seed=3)
    else:
        # Base, Asha, N1, N2, N3, N4 laid out like slide 7
        cfg = Config(n_nodes=6, sim_time=1e9, battery_j=1e6,
                     fixed_positions=[(700, 300), (100, 300), (280, 440),
                                      (280, 160), (500, 440), (500, 160)],
                     flows=[(1, 0, 2.0, 128, True, 1.0)], fail_events=[], loss=0.0)
    for k, v in VARIANTS[variant].items():
        setattr(cfg, k, v)
    return cfg


def label(i):
    if SCENE == "field":
        return "Base" if i == 0 else str(i)
    return ["Base", "Asha", "N1", "N2", "N3", "N4"][i]


class Demo:
    def __init__(self):
        self.variant = "AODV"
        self.speed = 0.5 if SCENE == "asha" else 2.0
        self.paused = False
        self.fig = plt.figure(figsize=(13, 7.5))
        self.fig.canvas.manager.set_window_title("Disaster communication: MANET + AODV, live simulation")
        self.ax = self.fig.add_axes([0.02, 0.04, 0.62, 0.9])
        self.side = self.fig.add_axes([0.66, 0.04, 0.33, 0.9])
        self.fig.canvas.mpl_connect("button_press_event", self.on_click)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.restart()

    def restart(self):
        self.trace = {}
        self.sim = Sim(make_config(self.variant), self.trace)
        self.t = 0.0
        self.tx_i = self.death_i = self.del_i = 0
        self.recent = deque()
        self.log = deque(maxlen=14)
        self.routes = {}
        self.say(f"Started {self.variant}" + ("  (our enhanced protocol)" if self.variant != "AODV" else ""))

    def say(self, msg):
        self.log.append(f"{self.t:6.1f}s  {msg}")

    # ---------- input ----------
    def on_click(self, ev):
        if ev.inaxes is not self.ax or ev.xdata is None:
            return
        best, bd = None, 60.0 ** 2
        for n in self.sim.nodes:
            if n.id == 0 or not n.alive:
                continue
            x, y = self.sim.pos(n)
            d = (x - ev.xdata) ** 2 + (y - ev.ydata) ** 2
            if d < bd:
                best, bd = n, d
        if best:
            self.sim.kill(best, "failure")

    def on_key(self, ev):
        if ev.key == " ":
            self.paused = not self.paused
        elif ev.key in ("+", "="):
            self.speed *= 1.5
        elif ev.key == "-":
            self.speed /= 1.5
        elif ev.key == "p":
            self.variant = "AODV-DR" if self.variant == "AODV" else "AODV"
            self.restart()
        elif ev.key == "r":
            self.restart()

    # ---------- simulation ----------
    def advance(self, dt):
        sim, end = self.sim, self.t + dt
        while sim.ev and sim.ev[0][0] <= end:
            t, _, fn, a = heapq.heappop(sim.ev)
            sim.now = t
            fn(*a)
        sim.now = self.t = end
        tr = self.trace
        for e in tr["tx"][self.tx_i:]:
            self.recent.append(e)
            if e[2] == "Q" and SCENE == "asha":
                self.say(f"{label(e[1])} floods RREQ (who knows the way to Base?)" if e[1] in self.sim.sos_src
                         else f"{label(e[1])} re-broadcasts the RREQ")
            elif e[2] == "P" and SCENE == "asha":
                self.say(f"{label(e[1])} sends RREP toward {label(e[3])}")
            elif e[2] == "R" and SCENE == "asha":
                self.say(f"{label(e[1])} broadcasts RERR (route broken)")
        self.tx_i = len(tr["tx"])
        for t, i, why in tr["deaths"][self.death_i:]:
            self.say(f"Phone {label(i)} died ({'battery empty' if why == 'battery' else 'power lost'})")
        self.death_i = len(tr["deaths"])
        for t, src, ms, hops in tr["deliveries"][self.del_i:]:
            if SCENE == "asha":
                self.say(f"SOS from {label(src)} reached Base: {hops} hops, {ms:.0f} ms")
        self.del_i = len(tr["deliveries"])
        while self.recent and self.recent[0][0] < self.t - (0.6 if SCENE == "asha" else 0.25):
            self.recent.popleft()
        for s in sim.sos_src:
            path = self.route(s)
            if path and path != self.routes.get(s) and (SCENE == "asha" or s == sim.sos_src[0]):
                self.say("Route: " + " > ".join(label(i) for i in path))
            self.routes[s] = path

    def route(self, src):
        path, n = [src], self.sim.nodes[src]
        while n.id != 0 and len(path) < 25:
            r = self.sim.lookup(n, 0) if n.alive else None
            if not r or not self.sim.nodes[r.next].alive:
                return None
            n = self.sim.nodes[r.next]
            path.append(n.id)
        return path if n.id == 0 else None

    # ---------- drawing ----------
    def draw(self, _):
        if not self.paused:
            self.advance(self.speed / 30)
        sim, ax = self.sim, self.ax
        ax.clear()
        c = sim.cfg
        if SCENE == "asha":
            ax.set_xlim(20, 780), ax.set_ylim(60, 540)
        else:
            ax.set_xlim(-20, c.width + 20), ax.set_ylim(-20, c.height + 20)
        ax.set_aspect("equal")
        ax.set_xticks([]), ax.set_yticks([])
        ax.set_title(f"{self.variant}   t = {self.t:5.1f} s   speed x{self.speed:.2f}"
                     + ("   [PAUSED]" if self.paused else ""), fontsize=12, loc="left")
        P = {n.id: sim.pos(n) for n in sim.nodes}
        alive = [n for n in sim.nodes if n.alive]

        # radio links
        for i, a in enumerate(alive):
            for b in alive[i + 1:]:
                if sim.in_range(a, b):
                    ax.plot(*zip(P[a.id], P[b.id]), color="#d0d0d0", lw=0.6, zorder=1)
        # active SOS routes
        for s, path in self.routes.items():
            if path:
                ax.plot([P[i][0] for i in path], [P[i][1] for i in path], color="#d7191c",
                        lw=5, alpha=0.35, zorder=2, solid_capstyle="round")
        # packets in flight (fading)
        for t, i, k, nxt in self.recent:
            age = (self.t - t) / (0.6 if SCENE == "asha" else 0.25)
            alpha = max(0.05, 1 - age)
            if nxt == BROADCAST:
                ax.add_patch(Circle(P[i], c.radio_range * (0.3 + 0.7 * min(age, 1)), fill=False,
                                    color=COL[k], lw=1.5, alpha=alpha * 0.6, zorder=3))
            else:
                ax.annotate("", xy=P[nxt], xytext=P[i], zorder=4,
                            arrowprops=dict(arrowstyle="-|>", color=COL[k], lw=2.2 if k != "D" else 0.8,
                                            alpha=alpha, shrinkA=10, shrinkB=10))
        # phones
        for n in sim.nodes:
            x, y = P[n.id]
            if not n.alive:
                ax.plot(x, y, "x", color="#555", ms=12, mew=2.5, zorder=5)
            elif n.id == 0:
                ax.plot(x, y, "s", color="#2c3e50", ms=18, zorder=5)
            else:
                f = n.frac
                col = (min(1, 2 * (1 - f)), min(1, 2 * f) * 0.75, 0.15)
                ax.plot(x, y, "o", color=col, ms=14 if SCENE == "asha" else 9,
                        mec="#d7191c" if n.id in sim.sos_src else "white", mew=2.5, zorder=5)
            if SCENE == "asha" or n.id == 0 or n.id in sim.sos_src:
                ax.text(x, y + (28 if SCENE == "asha" else 22), label(n.id), ha="center",
                        fontsize=11 if SCENE == "asha" else 8, weight="bold", zorder=6)
        self.draw_side()

    def draw_side(self):
        s, ax = self.sim.st, self.side
        ax.clear()
        ax.axis("off")
        sent, got = s["sent"][1], s["delivered"][1]
        sent_all, got_all = sum(s["sent"]), sum(s["delivered"])
        rec = s["recovery"]
        lines = [
            ("LIVE METRICS", "bold", 13),
            (f"SOS delivered     {got}/{sent}   ({100 * got / max(sent, 1):.0f} %)", None, 11),
            (f"All traffic       {got_all}/{sent_all}   ({100 * got_all / max(sent_all, 1):.0f} %)", None, 11),
            (f"SOS avg delay     {1000 * sum(s['delay'][1]) / max(len(s['delay'][1]), 1):.0f} ms", None, 11),
            (f"Route repairs     {len(rec)}" + (f"   (median {1000 * sorted(rec)[len(rec) // 2]:.0f} ms)" if rec else ""), None, 11),
            (f"Control packets   {s['ctrl_tx']}", None, 11),
            (f"Phones alive      {sum(n.alive for n in self.sim.nodes[1:])}/{len(self.sim.nodes) - 1}", None, 11),
            ("", None, 6),
            ("EVENTS", "bold", 13),
        ]
        y = 0.98
        for text, w, size in lines:
            ax.text(0, y, text, family="monospace", weight=w or "normal", fontsize=size, va="top",
                    transform=ax.transAxes)
            y -= 0.045 if size > 6 else 0.02
        for msg in self.log:
            ax.text(0, y, msg, family="monospace", fontsize=8.5, va="top", transform=ax.transAxes)
            y -= 0.032
        ax.text(0, 0.11, "Colours:", fontsize=9, transform=ax.transAxes, weight="bold")
        for j, k in enumerate("QPRS"):
            ax.text(0.2 + 0.2 * j, 0.11, NAME[k], color=COL[k], fontsize=10, weight="bold",
                    transform=ax.transAxes)
        ax.text(0, 0.01, "click phone = cut power   space = pause   +/- = speed\n"
                         "p = switch AODV / AODV-DR   r = restart\n"
                         "phone colour = battery (green full, red low), red ring = SOS sender",
                fontsize=8.5, transform=ax.transAxes, color="#444")

    def show(self):
        self.anim = FuncAnimation(self.fig, self.draw, interval=33, cache_frame_data=False)
        plt.show()


if __name__ == "__main__":
    Demo().show()
