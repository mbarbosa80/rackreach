"""Cable route graph, including vertical connections (shafts and risers).

Straight-line geometry is only a screening tool. Once a designer traces real
containment on the drawings, the graph built here becomes the authority for
compliance, and it is the only mechanism through which a NER on one level may
serve a space on another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import networkx as nx
import numpy as np

from .geometry import Point
from .units import Scale


@dataclass
class RouteSegment:
    """A traced piece of horizontal containment on one page."""

    a: Point
    b: Point
    page: int
    kind: str = "tray"           # tray | conduit | j-hook | free-air
    label: str = ""


@dataclass
class VerticalLink:
    """A shaft or riser connecting two pages (levels) at a known location."""

    key: str
    lower: Point
    upper: Point
    length_m: float
    kind: str = "shaft"          # shaft | riser | sleeve
    label: str = ""


@dataclass
class RouteNetwork:
    """A per-project routing graph. Node ids are ``(page, x, y)`` tuples rounded
    to ``snap_units`` so that separately traced segments join up."""

    snap_units: float = 2.0
    graph: nx.Graph = field(default_factory=nx.Graph)
    _vertical_keys: set[str] = field(default_factory=set)
    #: single-source shortest lengths and paths, by ``(node, level_only)``
    #: (4.7.0): an analysis asks hundreds of questions of one network.
    _sssp: dict = field(default_factory=dict)
    #: Vertical links the network refused to use, as ``(key, page, lower,
    #: upper, reason)``: both feet on one sheet. A shaft or riser joins two
    #: levels; one on a single sheet would be a teleport, a "route" shorter
    #: than the straight line. Kept here so the analysis can say so.
    ignored_verticals: list[tuple] = field(default_factory=list)
    #: The scale of each sheet the network was built with (5.5.1-rc4, tester
    #: R3-5): the end of a route on another sheet than the caller's is
    #: measured - lead-in and join reach - with that sheet's own scale.
    page_scales: dict[int, Scale] = field(default_factory=dict)

    #: The vertical edges, listed once per network state (R3-7), and the
    #: same as arrays for the tie-break search (rc5, R4-11): both directions
    #: of every link, in the order ``(u, v), (v, u)`` per link, as foot
    #: indexes and weights.
    _vedges: Optional[list] = None
    _varr: Optional[tuple] = None
    #: node -> its shortest lengths to every foot, as an array (rc5).
    _footdist: dict = field(default_factory=dict)
    #: (target, node) -> the canonical way from node to target (rc5).
    _canon: dict = field(default_factory=dict)
    _levels: Optional[nx.Graph] = None
    #: (page, x, y, reach, level_only) -> the joints of :meth:`_attach_all`
    #: (5.6.0-rc2, tester T1-10: every node and segment of the page was
    #: scanned again for every space x NER, the NER's end included).
    _attach_cache: dict = field(default_factory=dict)

    # -- construction -------------------------------------------------------
    def _invalidate(self) -> None:
        self._sssp.clear()
        self._vedges = None
        self._varr = None
        self._footdist.clear()
        self._canon.clear()
        self._levels = None
        self._attach_cache.clear()

    def _key(self, p: Point) -> tuple[int, float, float]:
        s = self.snap_units
        return (p.page, round(p.x / s) * s, round(p.y / s) * s)

    def _node(self, p: Point) -> tuple[int, float, float]:
        """The graph node of ``p``. The snap grid only decides which traced
        ends join up; every length and every lead-in is measured on the
        coordinates as drawn (``xy`` on a node, ``ends`` on an edge), so the
        grid never shortens or lengthens a route (R7-F4)."""
        n = self._key(p)
        if n not in self.graph:
            self.graph.add_node(n, xy=(float(p.x), float(p.y)))
        return n

    def _xy(self, n: tuple) -> tuple[float, float]:
        return self.graph.nodes[n].get("xy", (n[1], n[2]))

    @property
    def has_verticals(self) -> bool:
        return bool(self._vertical_keys)

    def add_segment(self, seg: RouteSegment, scale: Scale) -> None:
        """Add a horizontal segment; its weight is its real length in metres."""
        if self._key(seg.a) == self._key(seg.b):
            return
        na, nb = self._node(seg.a), self._node(seg.b)
        length_m = scale.to_metres(seg.a.distance_units(seg.b))
        prev = self.graph.get_edge_data(na, nb, default=None)
        self._invalidate()
        if prev is None or length_m < prev["weight"]:
            self.graph.add_edge(na, nb, weight=length_m, kind=seg.kind,
                                label=seg.label, vertical=False,
                                ends={na: (float(seg.a.x), float(seg.a.y)),
                                      nb: (float(seg.b.x), float(seg.b.y))})

    def add_vertical(self, link: VerticalLink) -> bool:
        """Add a shaft/riser. Its length is supplied in metres by the designer.

        Two links on the same feet keep the shorter, as two traced segments
        between the same ends do (R7-F1: the graph used to keep whichever was
        registered last, so a 43.8 m shaft drawn over a 4 m one sent a space
        the long way). A link with both feet on one sheet is not added - it is
        recorded in :attr:`ignored_verticals` and ``False`` comes back (R7-F5).
        """
        if link.length_m <= 0:
            raise ValueError("a vertical link must have a positive length in metres")
        if link.lower.page == link.upper.page:
            self.ignored_verticals.append(
                (link.key, link.lower.page, link.lower, link.upper,
                 "both feet are on the same sheet"))
            return False
        na, nb = self._node(link.lower), self._node(link.upper)
        self._invalidate()
        for n in (na, nb):
            self.graph.nodes[n]["foot"] = True
        prev = self.graph.get_edge_data(na, nb, default=None)
        label = link.label or link.key
        # Two equally long links on the same feet keep the smaller label, so
        # the order of registration never decides which one a note names
        # (5.5.1-rc4, tester R3-6).
        if prev is None or link.length_m < prev["weight"] or (
                link.length_m == prev["weight"] and str(label) < str(prev.get("label", ""))):
            self.graph.add_edge(na, nb, weight=link.length_m, kind=link.kind,
                                label=label, vertical=True)
        self._vertical_keys.add(link.key)
        return True

    # -- T-junctions (4.7.0) ------------------------------------------------
    def _ends(self, u: tuple, v: tuple, data: dict) -> tuple[tuple, tuple]:
        """A horizontal edge's two ends as drawn, ``u``'s first."""
        ends = data.get("ends") or {}
        return ends.get(u, self._xy(u)), ends.get(v, self._xy(v))

    def split_at_nodes(self, tolerance_units: float = 3.0) -> int:
        """Join a route that ends on the middle of another one.

        The viewer snaps a new route's first or last corner onto an existing
        route, which puts it on the *inside* of a traced segment; the graph
        only knew segment ends, so the two routes did not meet and nothing was
        measured along the pair. Every node lying on another horizontal
        segment of its page now splits that segment in two, the weights in
        proportion. Returns how many splits were made.
        """
        splits = 0
        # The horizontal edges by page, in the graph's own order, each with its
        # ends as drawn and a box around both its drawn and its snapped ends
        # (5.5.1-rc2, tester N3: the list was rebuilt, and every edge of the
        # page projected, for every node). Rebuilt only after a split, so the
        # first edge found - and so every split - is the one it always was.
        def by_page() -> dict[int, list]:
            out: dict[int, list] = {}
            for u, v, data in self.graph.edges(data=True):
                if data.get("vertical") or u[0] != v[0]:
                    continue
                (ax, ay), (bx, by) = self._ends(u, v, data)
                xs, ys = (ax, bx, u[1], v[1]), (ay, by, u[2], v[2])
                out.setdefault(u[0], []).append(
                    (u, v, data, ax, ay, bx, by,
                     min(xs) - tolerance_units, max(xs) + tolerance_units,
                     min(ys) - tolerance_units, max(ys) + tolerance_units))
            return out

        edges = by_page()
        for n in list(self.graph.nodes):
            nx_, ny_ = self._xy(n)
            changed = True
            while changed:
                changed = False
                for u, v, data, ax, ay, bx, by, x0, x1, y0, y1 in edges.get(n[0], ()):
                    if n in (u, v):
                        continue
                    if not ((x0 <= nx_ <= x1 and y0 <= ny_ <= y1) or
                            (x0 <= n[1] <= x1 and y0 <= n[2] <= y1)):
                        continue        # farther than the tolerance either way
                    t_snap, d_snap = _project(n[1], n[2], u[1], u[2], v[1], v[2])
                    t, d = _project(nx_, ny_, ax, ay, bx, by)
                    inside = 1e-6 < t < 1 - 1e-6
                    if not ((d <= tolerance_units and inside) or
                            (d_snap <= tolerance_units and 1e-6 < t_snap < 1 - 1e-6)):
                        continue
                    if not inside:
                        t = t_snap
                    w = data["weight"]
                    q = (ax + t * (bx - ax), ay + t * (by - ay))
                    self.graph.remove_edge(u, v)
                    attrs = {k: val for k, val in data.items() if k not in ("weight", "ends")}
                    self.graph.add_edge(u, n, weight=w * t, ends={u: (ax, ay), n: q}, **attrs)
                    self.graph.add_edge(n, v, weight=w * (1 - t), ends={n: q, v: (bx, by)},
                                        **attrs)
                    splits += 1
                    changed = True
                    edges = by_page()
                    break
        self._invalidate()
        return splits

    # -- queries ------------------------------------------------------------
    def has_vertical_between(self, page_a: int, page_b: int) -> bool:
        for u, v, data in self.graph.edges(data=True):
            if data.get("vertical") and {u[0], v[0]} == {page_a, page_b}:
                return True
        return False

    def _on_level(self, n: tuple) -> bool:
        """Does a horizontal (traced) segment end at ``n``? A shaft's bare
        foot does not join a route on its own level."""
        return any(not d.get("vertical") for d in self.graph[n].values())

    def _node_label(self, n: tuple) -> str:
        """The label a joint at node ``n`` goes by in a tie: the smallest of
        the traced segments that end there ("" for a shaft's bare foot)."""
        labels = [str(d.get("label", "")) for d in self.graph[n].values()
                  if not d.get("vertical")]
        return min(labels, key=natural_key) if labels else ""

    def _attach_all(self, p: Point, max_units: float, level_only: bool = False) -> list[dict]:
        """Where a space or a NER joins the traced network on its own page.

        Until 4.7.0 only a segment's *ends* were candidates, so a space beside
        the middle of a long corridor tray never joined it and was measured in
        a straight line as if nothing had been traced. Now the nearest point
        on any traced segment of the page (or a bare node — a shaft's foot)
        within ``max_units`` is the joint; the lead-in from the point to it is
        a straight line, measured and reported apart. Distances are taken on
        the coordinates as drawn, not on the snap grid. ``level_only`` leaves
        out the bare feet of shafts and risers.

        **Every** joint at that nearest distance is returned (5.6.0, tester
        R4-5): a space exactly 3 m from two trays joined whichever tray was
        registered first, so its distance - 23 m or 69 m - followed the order
        the trays were drawn in. The caller measures through each and keeps
        the shortest route; on an exact tie the smallest segment label. The
        joints come in a fixed order (label, then position), never the
        graph's. A segment's end that is itself a tied node is the node.
        Labels are compared as a person reads them, numbers by value: R2
        before R10 (5.6.0-rc2, tester T1-6).

        The answer is kept per point for the network's life (T1-10); the
        caller must not change the list it gets.
        """
        ck = (p.page, p.x, p.y, max_units, level_only)
        hit = self._attach_cache.get(ck)
        if hit is not None:
            return hit
        cands: list[dict] = []
        for n in self.graph.nodes:
            if n[0] != p.page or (level_only and not self._on_level(n)):
                continue
            x, y = self._xy(n)
            d = ((x - p.x) ** 2 + (y - p.y) ** 2) ** 0.5
            if d <= max_units:
                cands.append({"d": d, "x": x, "y": y, "via": [(n, 0.0)], "edge": None,
                              "t": 0.0, "label": self._node_label(n)})
        for u, v, data in self.graph.edges(data=True):
            if data.get("vertical") or not (u[0] == v[0] == p.page):
                continue
            (ax, ay), (bx, by) = self._ends(u, v, data)
            t, d = _project(p.x, p.y, ax, ay, bx, by)
            if d > max_units:
                continue
            w = data["weight"]
            cands.append({"d": d, "x": ax + t * (bx - ax), "y": ay + t * (by - ay),
                          "via": [(u, w * t), (v, w * (1 - t))], "edge": (u, v), "t": t,
                          "label": str(data.get("label", ""))})
        if not cands:
            self._attach_cache[ck] = []
            return []
        near = min(c["d"] for c in cands)
        tol = 1e-9 * max(1.0, near)
        tied = [c for c in cands if c["d"] - near <= tol]
        nodes = {c["via"][0][0] for c in tied if c["edge"] is None}
        tied = [c for c in tied if c["edge"] is None
                or not ((c["t"] <= 0.0 and c["edge"][0] in nodes)
                        or (c["t"] >= 1.0 and c["edge"][1] in nodes))]
        tied.sort(key=lambda c: (natural_key(c["label"]), round(c["x"], 6), round(c["y"], 6),
                                 c["edge"] is not None, str(c["edge"] or c["via"][0][0])))
        self._attach_cache[ck] = tied
        return tied

    def _attach(self, p: Point, max_units: float, level_only: bool = False) -> Optional[dict]:
        """The first of :meth:`_attach_all`, or ``None``."""
        tied = self._attach_all(p, max_units, level_only)
        return tied[0] if tied else None

    def _joints(self, p: Point, max_units: float, level_only: bool) -> list[dict]:
        """Every place ``p`` may join the network: the nearest ones (above,
        every one in a tie) and, unless ``level_only``, the foot of every
        shaft or riser of its page within reach (R7-F1). Only the nearest
        joint was tried before, so a shaft foot 1 m away hid a shorter
        connection through another shaft 2 m away, and a space went the long
        way."""
        out = list(self._attach_all(p, max_units, level_only))
        if level_only or not self._vertical_keys:
            return out
        taken = {j["via"][0][0] for j in out if j["edge"] is None}
        for n, data in self.graph.nodes(data=True):
            if n[0] != p.page or not data.get("foot") or n in taken:
                continue
            x, y = self._xy(n)
            d = ((x - p.x) ** 2 + (y - p.y) ** 2) ** 0.5
            if d <= max_units:
                out.append({"d": d, "x": x, "y": y, "via": [(n, 0.0)], "edge": None, "t": 0.0,
                            "label": self._node_label(n)})
        return out

    def _from(self, node: tuple, level_only: bool = False) -> tuple[dict, dict]:
        """Shortest lengths and paths from ``node``, computed once per node."""
        if not self._vertical_keys:
            level_only = False          # the same graph: one cache
        hit = self._sssp.get((node, level_only))
        if hit is None:
            g = self._level_graph() if level_only else self.graph
            hit = nx.single_source_dijkstra(g, node, weight="weight")
            self._sssp[(node, level_only)] = hit
        return hit

    def _level_graph(self) -> nx.Graph:
        """The graph without its vertical links, kept per network state (rc5,
        R4-11: a filtered view was rebuilt and filtered edge by edge on every
        search). Each node keeps its neighbours in the graph's own order, as
        the view gave them, so every search finds the paths it found."""
        if self._levels is None:
            h = nx.Graph()
            h.add_nodes_from(self.graph.nodes(data=True))
            for u, nbrs in self.graph._adj.items():
                h._adj[u] = {v: d for v, d in nbrs.items() if not d.get("vertical")}
            self._levels = h
        return self._levels

    def route_length_m(self, origin: Point, destination: Point, scale: Scale,
                       *, connect_tolerance_units: float = 60.0,
                       connect_tolerance_m: float = 15.0,
                       level_only: bool = False,
                       destination_scale: Optional[Scale] = None) -> Optional[dict]:
        """Shortest real route length in metres, or ``None`` when unreachable:
        the first of :meth:`route_options`.

        Each end joins the network at the nearest point of a traced segment
        (or a node) on its own page, or at the foot of any shaft or riser of
        that page, within the larger of ``connect_tolerance_units`` and
        ``connect_tolerance_m`` (at ``scale``); the shortest total over those
        joints is the route. The two lead-ins are straight lines, measured
        with ``scale``, included in the total and reported separately so a
        reviewer can see how much of the length is assumed rather than traced.

        ``level_only`` measures on the page's own traced segments, never
        through a shaft or riser: what a NER on the space's own floor is
        measured along (R7-F2).

        ``scale`` is the origin sheet's. The destination end, when it is on
        another sheet, is measured with ``destination_scale``, else with the
        scale the network was built with for that sheet, else with ``scale``
        (5.5.1-rc4, tester R3-5).
        """
        opts = self.route_options(origin, destination, scale,
                                  connect_tolerance_units=connect_tolerance_units,
                                  connect_tolerance_m=connect_tolerance_m,
                                  level_only=level_only,
                                  destination_scale=destination_scale)
        return opts[0] if opts else None

    def route_options(self, origin: Point, destination: Point, scale: Scale,
                      *, connect_tolerance_units: float = 60.0,
                      connect_tolerance_m: float = 15.0,
                      level_only: bool = False,
                      destination_scale: Optional[Scale] = None) -> list[dict]:
        """Every connection between the two points, shortest first: one per
        pair of joints (see :meth:`route_length_m`), each the shortest path
        between its two joints. The caller decides which one counts
        (5.5.1-rc2, tester N1): a 1 m sleeve next to an 8 m riser is the
        shortest connection and does not count, and the riser's, which does,
        must not be lost behind it.

        Each end's lead-in and join reach are converted with its own sheet's
        scale (5.5.1-rc4, tester R3-5: a NER on a 1:20 sheet above a 1:200
        one had its lead-in measured ten times too long). Each option also
        carries ``joint_a``, where the origin joins the network as drawn, and
        ``pages``, the sheets its path runs on."""
        scale_a = scale
        scale_b = scale
        if destination.page != origin.page:
            scale_b = destination_scale or self.page_scales.get(destination.page) or scale
        tol_a = max(connect_tolerance_units, scale_a.to_pdf_units(connect_tolerance_m))
        tol_b = tol_a if scale_b is scale_a else \
            max(connect_tolerance_units, scale_b.to_pdf_units(connect_tolerance_m))
        joints_a = self._joints(origin, tol_a, level_only)
        joints_b = self._joints(destination, tol_b, level_only)
        # How many joints tie for the nearest at each end (5.6.0-rc2): what
        # the release gate reads to tell a tie's effect (item 4) from any
        # other change of a route.
        ties = (len(self._attach_all(origin, tol_a, level_only)),
                len(self._attach_all(destination, tol_b, level_only)))
        out: list[dict] = []
        for ja in joints_a:
            stub_a = scale_a.to_metres(ja["d"])
            for jb in joints_b:
                stub_b = scale_b.to_metres(jb["d"])
                found = self._between(ja, jb, level_only)
                if found is None:
                    continue
                path_len, path = found
                if path_len <= 0.0 and len(path) == 1 and not self._on_level(path[0]):
                    # Two straight lead-ins meeting at a bare shaft foot:
                    # nothing traced, no route (5.5.1-rc2, tester N2). It hid
                    # the way round the floor above in the F2 case.
                    continue
                verticals = [
                    self.graph[u][v].get("label", "")
                    for u, v in zip(path, path[1:]) if self.graph[u][v].get("vertical")
                ]
                # Equal lengths: the links by label (R2-1), then the
                # segments the two ends join by label (5.6.0, R4-5), then
                # where they join and the path itself - never the order
                # anything was registered in.
                order = (round(path_len + stub_a + stub_b, 9), tuple(str(v) for v in verticals),
                         natural_key(ja.get("label", "")), natural_key(jb.get("label", "")),
                         round(ja["x"], 6), round(ja["y"], 6), round(jb["x"], 6), round(jb["y"], 6),
                         tuple(path))
                out.append({
                    "_order": order,
                    "length_m": path_len + stub_a + stub_b,
                    "traced_m": path_len,
                    "lead_in_m": stub_a + stub_b,
                    "node_count": len(path),
                    "vertical_links": verticals,
                    "crosses_levels": len({n[0] for n in path}) > 1,
                    "pages": sorted({n[0] for n in path} | {origin.page, destination.page}),
                    "joint_a": (ja["x"], ja["y"]),
                    "joint_ties": ties,
                })
        # Of two equally long connections, the one whose links come first by
        # label, then by where its joints are (5.5.1-rc3, tester R2-1: the
        # order of registration decided, so the link a note names could
        # change after a project package import).
        out.sort(key=lambda o: o["_order"])
        for o in out:
            del o["_order"]
        return out

    def _between(self, ja: dict, jb: dict, level_only: bool
                 ) -> Optional[tuple[float, list]]:
        """The shortest traced length and path between two joints.

        With shafts or risers in the network (and not ``level_only``), two
        equally long paths are told apart by the labels of the links they
        use, then by where those links stand, then by the nodes they pass
        (5.5.1-rc4, tester R3-7): the path used to be whichever the graph
        met first, so the link a note named followed the order the links
        were registered in. Without vertical links nothing changes."""
        canonical = bool(self._vertical_keys) and not level_only
        best: Optional[tuple[float, list]] = None
        best_key: Optional[tuple] = None
        if ja["edge"] is not None and jb["edge"] is not None and \
                {ja["edge"][0], ja["edge"][1]} == {jb["edge"][0], jb["edge"][1]}:
            # Both join the same segment: along it, directly.
            u, v = ja["edge"]
            w = self.graph[u][v]["weight"]
            tb = jb["t"] if jb["edge"] == (u, v) else 1 - jb["t"]
            best = (abs(ja["t"] - tb) * w, [u])
            if canonical:
                best_key = self._path_key(best[0], best[1])
        for a, da in ja["via"]:
            lengths, paths = self._from(a, level_only)
            for b, db in jb["via"]:
                if b not in lengths:
                    continue
                total = da + lengths[b] + db
                if not canonical:
                    if best is None or total < best[0]:
                        best = (total, paths[b])
                    continue
                path = paths[b]
                if not self._unique(lengths, path):
                    path = self._canonical_path(a, b, lengths, path)
                key = self._path_key(total, path)
                if best_key is None or key < best_key:
                    best, best_key = (total, path), key
        return best

    def _path_key(self, total: float, path: list) -> tuple:
        links = tuple(str(self.graph[u][v].get("label", "")) for u, v in zip(path, path[1:])
                      if self.graph[u][v].get("vertical"))
        return (round(total, 9), links, tuple(path))

    def _unique(self, la: dict, path: list) -> bool:
        """Is ``path`` the only shortest path to its last node (to the same
        tolerance as :meth:`_canonical_path`)? Then the canonical search
        would return it unchanged, and is not run (5.5.1-rc5, tester R4-11:
        it ran for every connection and made a project with 2,205 shafts
        several times slower).

        Any other shortest path leaves this one for the last time at some
        node ``u`` of it, which it enters from another neighbour ``v`` with
        ``la[v] + w(v, u) == la[u]``: so the path is the only one when every
        node on it has exactly one such neighbour."""
        if len(path) < 2:
            return True
        eps = 1e-9 * max(1.0, la[path[-1]])
        g = self.graph
        for u in path[1:]:
            lim = la[u] + eps
            tight = 0
            for v, data in g[u].items():
                lv = la.get(v)
                if lv is not None and lv + data["weight"] <= lim:
                    tight += 1
                    if tight > 1:
                        return False
        return True

    def _vertical_arrays(self) -> tuple:
        """Both directions of every vertical link as arrays: (from-foot
        index, to-foot index, weight), the label of each, the feet, and the
        link directions leaving each sheet (rc5, R4-11)."""
        if self._varr is None:
            if self._vedges is None:
                self._vedges = [(u, v, data) for u, v, data in self.graph.edges(data=True)
                                if data.get("vertical")]
            feet: list = []
            index: dict = {}
            xs, ys, ws, labels = [], [], [], []
            leaving: dict[int, list[int]] = {}
            for u, v, data in self._vedges:
                for n in (u, v):
                    if n not in index:
                        index[n] = len(feet)
                        feet.append(n)
                for x, y in ((u, v), (v, u)):
                    leaving.setdefault(x[0], []).append(len(xs))
                    xs.append(index[x])
                    ys.append(index[y])
                    ws.append(float(data["weight"]))
                    labels.append(str(data.get("label", "")))
            self._varr = (np.array(xs, dtype=np.int64), np.array(ys, dtype=np.int64),
                          np.array(ws, dtype=np.float64), labels, feet,
                          {p: np.array(ix, dtype=np.int64) for p, ix in leaving.items()})
        return self._varr

    def _foot_lengths(self, node: tuple, level_only: bool = False) -> "np.ndarray":
        """Shortest lengths from ``node`` to every foot (inf: unreachable)."""
        hit = self._footdist.get((node, level_only))
        if hit is None:
            lengths, _ = self._from(node, level_only)
            feet = self._vertical_arrays()[4]
            inf = float("inf")
            hit = np.array([lengths.get(n, inf) for n in feet], dtype=np.float64)
            self._footdist[(node, level_only)] = hit
        return hit

    def _canonical_path(self, a: tuple, b: tuple, la: dict, found: list) -> list:
        """Of the shortest paths from ``a`` to ``b``, the one whose vertical
        links come first by label, then by their feet - whatever order the
        graph was built in (R3-7).

        A shortest path is level-only stretches joined by vertical links.
        A link ``u -> v`` lies on one when ``la[u] + w + lb[v]`` is the
        shortest length; the stretches between links are checked on the
        level-only distances. Labels are chosen from ``b`` backwards, where
        putting a label in front keeps the order of two sequences, so the
        result is the smallest sequence of labels (then of feet)."""
        total = la[b]
        eps = 1e-9 * max(1.0, total)
        xs, ys, ws, _labels, _feet, _leaving = self._vertical_arrays()
        if not len(ws):
            return found
        da, db = self._foot_lengths(a), self._foot_lengths(b)
        if not np.any(np.abs(da[xs] + ws + db[ys] - total) <= eps):
            return found                 # no link on any shortest path
        got = self._best_to(b, a)
        return got[2] if got is not None else found

    def _best_to(self, b: tuple, x: tuple):
        """(labels, feet, path) of the best shortest way from ``x`` to ``b``.

        The ways on from ``x`` are the shortest ``x -> b`` paths, whichever
        node the search started from: a node on a shortest ``a -> b`` path
        continues along a shortest ``x -> b`` path. So the answer is kept per
        ``(b, x)`` for the network's life (rc5, R4-11: it was searched again
        for every connection). A link ``u -> v`` leaving ``x``'s sheet is on
        one when (level-only length ``x -> u``) + w + lb[v] == lb[x]."""
        key = (b, x)
        if key in self._canon:
            return self._canon[key]
        xs, ys, ws, labels, feet, leaving = self._vertical_arrays()
        lb, _ = self._from(b)
        lbx = lb[x]
        eps = 1e-9 * max(1.0, lbx)
        hl, hp = self._from(x, True)
        options = []
        if b in hl and abs(hl[b] - lbx) <= eps:
            options.append(((), (), list(hp[b])))
        idx = leaving.get(x[0])
        if idx is not None:
            hlx = self._foot_lengths(x, True)
            db = self._foot_lengths(b)
            sel = idx[np.abs(hlx[xs[idx]] + ws[idx] + db[ys[idx]] - lbx) <= eps]
            for i in sel.tolist():
                u, v = feet[xs[i]], feet[ys[i]]
                rest = self._best_to(b, v)
                if rest is None:
                    continue
                options.append(((labels[i],) + rest[0], (u, v) + rest[1],
                                list(hp[u]) + rest[2]))
        got = min(options, key=lambda o: (o[0], o[1])) if options else None
        self._canon[key] = got
        return got


def natural_key(label: str) -> tuple:
    """A label as a person orders it: the numbers in it by value, so R2
    comes before R10 (5.6.0-rc2, tester T1-6); the label itself last, so
    "R02" and "R2" still have one order."""
    s = str(label or "")
    parts = re.split(r"(\d+)", s)
    return (tuple((0, int(x)) if x.isdigit() else (1, x) for x in parts if x), s)


def _project(px: float, py: float, ax: float, ay: float, bx: float, by: float
             ) -> tuple[float, float]:
    """Where (px, py) falls on segment a–b: the fraction along it (0–1) and
    the distance from the point to that spot."""
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    qx, qy = ax + t * dx, ay + t * dy
    return t, ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def build_network(segments: Iterable[RouteSegment],
                  verticals: Iterable[VerticalLink],
                  scale_for_page: dict[int, Scale],
                  snap_units: float = 2.0) -> RouteNetwork:
    net = RouteNetwork(snap_units=snap_units)
    net.page_scales = dict(scale_for_page)
    for seg in segments:
        sc = scale_for_page.get(seg.page)
        if sc is None:
            continue
        net.add_segment(seg, sc)
    for link in verticals:
        # Both feet on one sheet: not used, and recorded (R7-F5).
        net.add_vertical(link)
    net.split_at_nodes()
    return net
