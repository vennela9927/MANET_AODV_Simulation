"""
manet_sim.py - discrete-event simulator of a disaster-zone MANET running AODV.

What is modelled
  * Mobility      random-waypoint walkers (rescuers on foot), one static Base camp
  * Radio         802.11-style shared channel: fixed range, carrier sense
                  (a node waits while any neighbour is transmitting), per-node
                  transmit queue, MAC retries on unicast, small broadcast loss
  * Energy        every node has a battery; transmit, receive/overhear and idle
                  all cost energy; a node whose battery hits zero dies
  * Failures      a chosen fraction of relays lose power at random times
  * AODV          RFC 3561 core: on-demand RREQ flooding with duplicate
                  suppression, reverse routes, RREP (destination or
                  intermediate reply), destination sequence numbers, route
                  timeouts, link-break detection, RERR propagation, RREQ retries

Protocol variants
  AODV          standard protocol
  AODV-DR       "Disaster-Ready" AODV (our proposal) = all three enhancements:
     priority       SOS packets jump the queue at every relay
     battery_aware  low-battery nodes delay their RREQ rebroadcast (so routes
                    form through charged phones) and stop relaying when critical
     store_carry    no route -> keep the packet and retry later instead of
                    dropping it; relays repair a broken route locally

Simplifications (say these if asked): no packet collisions / capture effect,
no expanding-ring search, fixed radio range instead of a fading model.
"""
import heapq
import math
import random
from dataclasses import dataclass

BROADCAST = -1
INF = float("inf")

# AODV constants (RFC 3561 defaults)
ACTIVE_ROUTE_TIMEOUT = 3.0
MY_ROUTE_TIMEOUT = 6.0
NET_DIAMETER = 35
NODE_TRAVERSAL_TIME = 0.04
NET_TRAVERSAL_TIME = 2 * NODE_TRAVERSAL_TIME * NET_DIAMETER  # 2.8 s
RREQ_RETRIES = 2

# Our enhancements
BATTERY_CRITICAL = 0.10     # below this a phone stops offering itself as a relay
BATTERY_LOW = 0.60          # below this a phone delays its RREQ rebroadcast
BATTERY_DELAY = 0.25        # seconds of extra delay per unit of missing charge
CARRY_RETRY = 3.0           # store-and-carry: retry discovery every 3 s

CTRL = ("RREQ", "RREP", "RERR")


@dataclass
class Config:
    n_nodes: int = 40                 # includes the Base node (id 0)
    width: float = 1000.0
    height: float = 1000.0
    radio_range: float = 250.0
    sim_time: float = 200.0
    speed: tuple = (0.5, 1.5)         # m/s, walking
    pause: tuple = (0.0, 30.0)        # s
    bitrate: float = 2e6              # 802.11b basic rate
    base_pos: tuple = (900.0, 500.0)
    # traffic
    n_sos: int = 5                    # volunteers sending SOS to Base
    sos_interval: float = 2.0
    sos_size: int = 128               # text + GPS
    n_bg: int = 10                    # ordinary flows (status, photos, chat)
    bg_interval: float = 0.05
    bg_size: int = 1024              # photos / status uploads
    traffic_start: float = 5.0
    # failures
    fail_frac: float = 0.0
    fail_window: tuple = (40.0, 160.0)
    # energy (scaled-down battery so drain is visible in a 200 s run)
    battery_j: float = 400.0
    battery_min_frac: float = 0.2
    p_tx: float = 1.4
    p_rx: float = 0.9
    p_idle: float = 0.05
    queue_limit: int = 50
    buffer_limit: int = 64
    loss: float = 0.02
    hold_time: float = 60.0
    # protocol features
    priority: bool = False
    battery_aware: bool = False
    store_carry: bool = False
    seed: int = 1
    # scripted scenarios (used by the demo)
    fixed_positions: list = None      # [(x, y)] per node, nobody moves
    flows: list = None                # [(src, dst, interval, size, is_sos, start)]
    fail_events: list = None          # [(t, node)]
    initial_battery: list = None      # fraction per node


VARIANTS = {
    "AODV": {},
    "AODV-DR": dict(priority=True, battery_aware=True, store_carry=True),
    "+priority only": dict(priority=True),
    "+battery-aware only": dict(battery_aware=True),
    "+store-carry only": dict(store_carry=True),
}


class Packet:
    sos = False
    hops = 0
    src = dst = None

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def copy(self, **kw):
        d = dict(self.__dict__)
        d.update(kw)
        return Packet(**d)


class Route:
    __slots__ = ("next", "hops", "seq", "valid", "expiry")

    def __init__(self, nxt, hops, seq, expiry):
        self.next, self.hops, self.seq, self.valid, self.expiry = nxt, hops, seq, True, expiry


class Node:
    def __init__(self, i):
        self.id = i
        self.alive = True
        self.infinite = False         # Base camp runs on a generator
        self.cap = 1.0
        self.energy = 1.0
        self.seq = 0
        self.rreq_id = 0
        self.routes = {}
        self.seen = set()
        self.queue = []
        self.busy = False
        self.medium_until = 0.0
        self.buffer = {}              # dst -> [packets waiting for a route]
        self.discovery = {}           # dst -> [tries, rreq_id]
        self.pending_break = {}       # dst -> time the route broke
        self.flows_to = set()
        self.segs = []
        self.cur = 0

    @property
    def frac(self):
        return 1.0 if self.infinite else max(self.energy, 0.0) / self.cap


class Sim:
    def __init__(self, cfg, trace=None):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.now = 0.0
        self.ev = []
        self.cnt = 0
        self.r2 = cfg.radio_range ** 2
        self.trace = trace
        self._pc_t = -1.0
        self._pc = {}
        self.uid = 0
        self.st = dict(sent=[0, 0], delivered=[0, 0], delay=[[], []], hops=[0, 0],
                       ctrl_tx=0, ctrl_bytes=0, data_tx=0, recovery=[],
                       deaths=[], drops={})
        self.nodes = [Node(i) for i in range(cfg.n_nodes)]
        self._setup()

    # ---------- scheduling ----------
    def at(self, t, fn, *a):
        heapq.heappush(self.ev, (t, self.cnt, fn, a))
        self.cnt += 1

    def run(self):
        end = self.cfg.sim_time
        while self.ev:
            t, _, fn, a = heapq.heappop(self.ev)
            if t > end:
                break
            self.now = t
            fn(*a)
        self.now = end
        return self.results()

    # ---------- setup ----------
    def _setup(self):
        c, rng = self.cfg, self.rng
        for n in self.nodes:
            self._mobility(n)
            n.cap = c.battery_j
            if c.initial_battery:
                n.energy = c.battery_j * c.initial_battery[n.id]
            else:
                n.energy = c.battery_j * rng.uniform(c.battery_min_frac, 1.0)
        self.nodes[0].infinite = True

        others = list(range(1, c.n_nodes))
        if c.flows is not None:
            flows = c.flows
            sos_src = [f[0] for f in flows if f[4]]
        else:
            sos_src = rng.sample(others, min(c.n_sos, len(others)))
            flows = [(s, 0, c.sos_interval, c.sos_size, True, c.traffic_start) for s in sos_src]
            for k in range(c.n_bg):
                s = rng.choice(others)
                d = 0 if k % 2 == 0 else rng.choice([x for x in others if x != s])
                flows.append((s, d, c.bg_interval, c.bg_size, False, c.traffic_start))
        self.sos_src = sos_src
        for f in flows:
            self.nodes[f[0]].flows_to.add(f[1])
            self.at(f[5] + rng.uniform(0, f[2]), self._flow_tick, f)

        if c.fail_events is not None:
            for t, i in c.fail_events:
                self.at(t, self.kill, self.nodes[i], "failure")
        elif c.fail_frac > 0:
            cand = [i for i in others if i not in sos_src]
            k = min(len(cand), round(c.fail_frac * (c.n_nodes - 1)))
            for i in rng.sample(cand, k):
                self.at(rng.uniform(*c.fail_window), self.kill, self.nodes[i], "failure")

        self.at(1.0, self._tick)
        if self.trace is not None:
            self.trace.update(n=c.n_nodes, w=c.width, h=c.height, range=c.radio_range,
                              sos=sos_src, frames=[], tx=[], deaths=[], deliveries=[])
            self.at(0.0, self._frame)

    def _mobility(self, n):
        c, rng = self.cfg, self.rng
        if c.fixed_positions is not None:
            x, y = c.fixed_positions[n.id]
            n.segs = [(0.0, INF, x, y, x, y)]
            return
        if n.id == 0:
            x, y = c.base_pos
            n.segs = [(0.0, INF, x, y, x, y)]
            return
        x, y, t = rng.uniform(0, c.width), rng.uniform(0, c.height), 0.0
        segs = []
        while t < c.sim_time:
            nx, ny = rng.uniform(0, c.width), rng.uniform(0, c.height)
            dt = math.hypot(nx - x, ny - y) / rng.uniform(*c.speed)
            segs.append((t, t + dt, x, y, nx, ny))
            t += dt
            x, y = nx, ny
            p = rng.uniform(*c.pause)
            segs.append((t, t + p, x, y, x, y))
            t += p
        segs.append((t, INF, x, y, x, y))
        n.segs = segs

    # ---------- geometry ----------
    def pos(self, n):
        if self._pc_t != self.now:
            self._pc_t = self.now
            self._pc = {}
        p = self._pc.get(n.id)
        if p is None:
            segs, i = n.segs, n.cur
            while self.now >= segs[i][1]:
                i += 1
            n.cur = i
            t0, t1, x0, y0, x1, y1 = segs[i]
            if x0 == x1 and y0 == y1:
                p = (x0, y0)
            else:
                f = (self.now - t0) / (t1 - t0)
                p = (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
            self._pc[n.id] = p
        return p

    def in_range(self, a, b):
        (x1, y1), (x2, y2) = self.pos(a), self.pos(b)
        return (x1 - x2) ** 2 + (y1 - y2) ** 2 <= self.r2

    def neighbors(self, n):
        return [m for m in self.nodes if m is not n and m.alive and self.in_range(n, m)]

    # ---------- energy & failures ----------
    def spend(self, n, joules):
        if n.infinite or not n.alive:
            return
        n.energy -= joules
        if n.energy <= 0:
            self.kill(n, "battery")

    def kill(self, n, why):
        if not n.alive:
            return
        n.alive = False
        n.energy = min(n.energy, 0.0) if why == "battery" else n.energy
        n.queue.clear()
        n.buffer.clear()
        n.discovery.clear()
        self.st["deaths"].append((self.now, n.id, why))
        if self.trace is not None:
            self.trace["deaths"].append([round(self.now, 3), n.id, why])

    def _tick(self):
        for n in self.nodes:
            if n.alive:
                self.spend(n, self.cfg.p_idle)
        self.at(self.now + 1.0, self._tick)

    def _frame(self):
        pts = []
        for n in self.nodes:
            x, y = self.pos(n)
            pts.append([round(x), round(y), round(n.frac * 100) if n.alive else -1])
        self.trace["frames"].append(pts)
        self.at(self.now + 0.25, self._frame)

    def drop(self, why, pkt=None):
        self.st["drops"][why] = self.st["drops"].get(why, 0) + 1

    # ---------- link layer ----------
    def _cls(self, pkt):
        if not self.cfg.priority:
            return 0
        if pkt.kind == "DATA":
            return 0 if pkt.sos else 2
        return 1

    def enqueue(self, n, pkt, nxt):
        if not n.alive:
            return
        cls = self._cls(pkt)
        if len(n.queue) >= self.cfg.queue_limit:
            worst = max(range(len(n.queue)), key=lambda k: (n.queue[k][0], n.queue[k][1]))
            if self.cfg.priority and n.queue[worst][0] > cls:
                n.queue.pop(worst)
                heapq.heapify(n.queue)
                self.drop("queue_full")
            else:
                self.drop("queue_full")
                return
        heapq.heappush(n.queue, (cls, self.cnt, pkt, nxt))
        self.cnt += 1
        if not n.busy:
            n.busy = True
            self.at(self.now, self.try_send, n)

    def try_send(self, n):
        if not n.alive or not n.queue:
            n.busy = False
            return
        if self.now < n.medium_until:   # carrier sense + random backoff
            self.at(n.medium_until + self.rng.uniform(0, 6.2e-4), self.try_send, n)
            return
        _, _, pkt, nxt = heapq.heappop(n.queue)
        dur = pkt.size * 8 / self.cfg.bitrate + 3e-4
        nbrs = self.neighbors(n)
        end = self.now + dur
        n.medium_until = end
        for m in nbrs:
            if m.medium_until < end:
                m.medium_until = end
        if pkt.kind in CTRL:
            self.st["ctrl_tx"] += 1
            self.st["ctrl_bytes"] += pkt.size
        else:
            self.st["data_tx"] += 1
        if self.trace is not None and (pkt.kind in CTRL or pkt.sos or self.rng.random() < 0.15):
            self.trace["tx"].append([round(self.now, 4), n.id, pkt.kind[3] if pkt.kind in CTRL else
                                     ("S" if pkt.sos else "D"), nxt])
        self.spend(n, self.cfg.p_tx * dur)
        for m in nbrs:
            self.spend(m, self.cfg.p_rx * dur)
        if not n.alive:
            return
        self.at(end, self.tx_done, n, pkt, nxt, nbrs, dur)

    def tx_done(self, n, pkt, nxt, nbrs, dur):
        if not n.alive:
            return
        if nxt == BROADCAST:
            for m in nbrs:
                if m.alive and self.rng.random() > self.cfg.loss:
                    self.at(self.now, self.receive, m, pkt, n.id)
        else:
            m = self.nodes[nxt]
            if m.alive and self.in_range(n, m):
                self.at(self.now, self.receive, m, pkt, n.id)
            else:
                self.spend(n, self.cfg.p_tx * dur * 3)   # failed MAC retries
                if n.alive:
                    self.link_break(n, nxt, pkt)
        self.try_send(n)

    # ---------- routing table ----------
    def lookup(self, n, dst):
        r = n.routes.get(dst)
        if r and r.valid and r.expiry > self.now:
            return r
        return None

    def update_route(self, n, dst, nxt, hops, seq, life):
        now = self.now
        r = n.routes.get(dst)
        if r is None:
            n.routes[dst] = Route(nxt, hops, seq, now + life)
            return True
        fresh = r.valid and r.expiry > now
        newer = seq is not None and (r.seq is None or seq > r.seq)
        better = (seq == r.seq or seq is None) and (not fresh or hops < r.hops)
        if newer or better:
            r.next, r.hops, r.valid = nxt, hops, True
            if seq is not None:
                r.seq = seq
            r.expiry = max(r.expiry if fresh else 0.0, now + life)
            return True
        if fresh and r.next == nxt:
            r.expiry = max(r.expiry, now + life)
        return False

    def route_ready(self, n, dst):
        """A valid route to dst now exists at n: finish discovery and flush the buffer."""
        if not self.lookup(n, dst):
            return
        n.discovery.pop(dst, None)
        if dst in n.pending_break:
            self.st["recovery"].append(self.now - n.pending_break.pop(dst))
        for p in n.buffer.pop(dst, []):
            self.route_data(n, p)

    # ---------- data ----------
    def _flow_tick(self, f):
        src, dst, interval, size, sos, _ = f
        n = self.nodes[src]
        if not n.alive:
            return
        self.uid += 1
        pkt = Packet(kind="DATA", src=src, dst=dst, size=size, sos=sos, born=self.now,
                     uid=self.uid, hops=0)
        self.st["sent"][sos] += 1
        self.route_data(n, pkt)
        self.at(self.now + interval * self.rng.uniform(0.9, 1.1), self._flow_tick, f)

    def route_data(self, n, pkt):
        if self.now - pkt.born > self.cfg.hold_time:
            self.drop("expired")
            return
        r = self.lookup(n, pkt.dst)
        if r:
            r.expiry = max(r.expiry, self.now + ACTIVE_ROUTE_TIMEOUT)
            back = self.lookup(n, pkt.src)
            if back:
                back.expiry = max(back.expiry, self.now + ACTIVE_ROUTE_TIMEOUT)
            self.enqueue(n, pkt, r.next)
        elif n.id == pkt.src or self.cfg.store_carry:
            self.buffer_pkt(n, pkt)
            self.discover(n, pkt.dst)
        else:
            self.drop("no_route")
            r = n.routes.get(pkt.dst)
            self.send_rerr(n, [(pkt.dst, r.seq if r else None)], self.now)

    def buffer_pkt(self, n, pkt):
        buf = n.buffer.setdefault(pkt.dst, [])
        buf.append(pkt)
        if sum(len(b) for b in n.buffer.values()) > self.cfg.buffer_limit:
            victim = buf
            if self.cfg.priority:
                for b in n.buffer.values():
                    if any(not p.sos for p in b):
                        victim = b
                        break
            idx = next((k for k, p in enumerate(victim) if not (self.cfg.priority and p.sos)), 0)
            victim.pop(idx)
            self.drop("buffer_full")

    def rx_DATA(self, n, pkt, prev):
        pkt.hops += 1
        if n.id == pkt.dst:
            s = pkt.sos
            self.st["delivered"][s] += 1
            self.st["delay"][s].append(self.now - pkt.born)
            self.st["hops"][s] += pkt.hops
            if self.trace is not None and s:
                self.trace["deliveries"].append([round(self.now, 3), pkt.src,
                                                 round((self.now - pkt.born) * 1000, 1), pkt.hops])
            return
        self.route_data(n, pkt)

    # ---------- AODV control ----------
    def receive(self, n, pkt, prev):
        if not n.alive:
            return
        self.update_route(n, prev, prev, 1, None, ACTIVE_ROUTE_TIMEOUT)
        getattr(self, "rx_" + pkt.kind)(n, pkt, prev)

    def discover(self, n, dst):
        if dst in n.discovery:
            return
        n.discovery[dst] = [0, 0]
        self.send_rreq(n, dst)

    def send_rreq(self, n, dst):
        n.seq += 1
        n.rreq_id += 1
        r = n.routes.get(dst)
        pkt = Packet(kind="RREQ", orig=n.id, seq=n.seq, dst=dst, dst_seq=r.seq if r else None,
                     rreq_id=n.rreq_id, hops=0, ttl=NET_DIAMETER, size=64)
        n.seen.add((n.id, n.rreq_id))
        n.discovery[dst][1] = n.rreq_id
        self.enqueue(n, pkt, BROADCAST)
        wait = NET_TRAVERSAL_TIME * (2 ** n.discovery[dst][0])
        self.at(self.now + wait, self.rreq_timeout, n, dst, n.rreq_id)

    def rreq_timeout(self, n, dst, rid):
        d = n.discovery.get(dst)
        if not n.alive or d is None or d[1] != rid:
            return
        if d[0] < RREQ_RETRIES:
            d[0] += 1
            self.send_rreq(n, dst)
            return
        del n.discovery[dst]
        if self.cfg.store_carry:
            self.at(self.now + CARRY_RETRY, self.carry_retry, n, dst)
        else:
            for _ in n.buffer.pop(dst, []):
                self.drop("no_route")

    def carry_retry(self, n, dst):
        if not n.alive:
            return
        buf = [p for p in n.buffer.get(dst, []) if self.now - p.born <= self.cfg.hold_time]
        for _ in range(len(n.buffer.get(dst, [])) - len(buf)):
            self.drop("expired")
        if buf:
            n.buffer[dst] = buf
            if self.lookup(n, dst):
                self.route_ready(n, dst)
            else:
                self.discover(n, dst)
        else:
            n.buffer.pop(dst, None)

    def rx_RREQ(self, n, pkt, prev):
        key = (pkt.orig, pkt.rreq_id)
        if key in n.seen:
            return
        n.seen.add(key)
        hops = pkt.hops + 1
        if self.update_route(n, pkt.orig, prev, hops, pkt.seq, 2 * ACTIVE_ROUTE_TIMEOUT):
            self.route_ready(n, pkt.orig)

        if n.id == pkt.dst:
            n.seq = max(n.seq, pkt.dst_seq or 0) + 1
            rrep = Packet(kind="RREP", orig=pkt.orig, dst=n.id, dst_seq=n.seq, hops=0, size=60)
            self.enqueue(n, rrep, prev)
            return

        low_battery = self.cfg.battery_aware and n.frac < BATTERY_CRITICAL
        if low_battery:
            return   # a dying phone does not volunteer as a relay

        r = self.lookup(n, pkt.dst)
        if r and r.seq is not None and (pkt.dst_seq is None or r.seq >= pkt.dst_seq):
            rrep = Packet(kind="RREP", orig=pkt.orig, dst=pkt.dst, dst_seq=r.seq, hops=r.hops, size=60)
            self.enqueue(n, rrep, prev)
            return

        if pkt.ttl <= 1:
            return
        dseq = pkt.dst_seq
        if r and r.seq is not None and (dseq is None or r.seq > dseq):
            dseq = r.seq
        fwd = pkt.copy(hops=hops, ttl=pkt.ttl - 1, dst_seq=dseq)
        delay = self.rng.uniform(0, 0.01)            # RFC broadcast jitter
        if self.cfg.battery_aware and n.frac < BATTERY_LOW:
            delay += (BATTERY_LOW - n.frac) * BATTERY_DELAY
        self.at(self.now + delay, self.enqueue, n, fwd, BROADCAST)

    def rx_RREP(self, n, pkt, prev):
        hops = pkt.hops + 1
        self.update_route(n, pkt.dst, prev, hops, pkt.dst_seq, MY_ROUTE_TIMEOUT)
        self.route_ready(n, pkt.dst)
        if n.id == pkt.orig:
            return
        back = self.lookup(n, pkt.orig)
        if back:
            self.enqueue(n, pkt.copy(hops=hops), back.next)

    def link_break(self, n, nxt, pkt):
        lost = []
        for d, r in n.routes.items():
            if r.valid and r.next == nxt:
                r.valid = False
                if r.seq is not None:
                    r.seq += 1
                lost.append((d, r.seq))
        # packets still queued for the dead link fail the same way
        failed = [pkt] + [q[2] for q in n.queue if q[3] == nxt]
        n.queue = [q for q in n.queue if q[3] != nxt]
        heapq.heapify(n.queue)
        repairing = set()
        for p in failed:
            if p.kind != "DATA":
                self.drop("ctrl_link_break")
            elif n.id == p.src or self.cfg.store_carry:
                n.pending_break.setdefault(p.dst, self.now)
                self.buffer_pkt(n, p)
                self.discover(n, p.dst)
                if n.id != p.src:
                    repairing.add(p.dst)
            else:
                self.drop("link_break")
        lost = [(d, s) for d, s in lost if d not in repairing]
        if lost:
            self.send_rerr(n, lost, self.now)

    def send_rerr(self, n, lost, break_t):
        pkt = Packet(kind="RERR", unreach=lost, break_t=break_t, size=20 + 8 * len(lost))
        self.enqueue(n, pkt, BROADCAST)

    def rx_RERR(self, n, pkt, prev):
        lost = []
        for d, s in pkt.unreach:
            r = n.routes.get(d)
            if r and r.valid and r.next == prev:
                r.valid = False
                if s is not None:
                    r.seq = s
                lost.append((d, r.seq))
                if d in n.flows_to:
                    n.pending_break.setdefault(d, pkt.break_t)
        if lost:
            self.send_rerr(n, lost, pkt.break_t)

    # ---------- results ----------
    def results(self):
        st, c = self.st, self.cfg

        def mean(v):
            return sum(v) / len(v) if v else float("nan")

        def median(v):
            v = sorted(v)
            return v[len(v) // 2] if v else float("nan")

        delivered_all = st["delivered"][0] + st["delivered"][1]
        sent_all = st["sent"][0] + st["sent"][1]
        relays = self.nodes[1:]
        battery_deaths = [d for d in st["deaths"] if d[2] == "battery"]
        return dict(
            sos_pdr=st["delivered"][1] / max(st["sent"][1], 1) * 100,
            sos_delay_ms=mean(st["delay"][1]) * 1000,
            sos_delay_med_ms=median(st["delay"][1]) * 1000,
            recovery_med_ms=median(st["recovery"]) * 1000,
            all_pdr=delivered_all / max(sent_all, 1) * 100,
            all_delay_ms=mean(st["delay"][0] + st["delay"][1]) * 1000,
            recovery_ms=mean(st["recovery"]) * 1000,
            recoveries=len(st["recovery"]),
            nrl=st["ctrl_tx"] / max(delivered_all, 1),
            ctrl_tx=st["ctrl_tx"],
            data_tx=st["data_tx"],
            energy_j=sum(n.cap - max(n.energy, 0.0) for n in relays),
            battery_deaths=len(battery_deaths),
            alive_end=sum(n.alive for n in relays),
            sos_sent=st["sent"][1],
            sos_delivered=st["delivered"][1],
            drops=dict(st["drops"]),
        )


def simulate(variant="AODV", trace=None, **kw):
    cfg = Config(**{**kw, **VARIANTS[variant]})
    return Sim(cfg, trace).run()


if __name__ == "__main__":
    import time
    for v in ("AODV", "AODV-DR"):
        t = time.time()
        r = simulate(v, n_nodes=40, fail_frac=0.1, seed=1)
        print(v, f"{time.time() - t:.1f}s", {k: (round(x, 2) if isinstance(x, float) else x) for k, x in r.items()})
