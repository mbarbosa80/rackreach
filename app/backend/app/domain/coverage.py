"""Coverage engine: which NER serves which space, and does it comply.

Assignment order is deterministic (spaces sorted by demand then by key, NERs by
effective distance then by key) so that two runs on the same inputs always give
byte-identical results — a hard requirement for an auditable design tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

from .distance import (ALERT_CRITICAL_NER_IN_LIMIT, ALERT_LABEL,
                       ALERT_NO_CRITICAL_NER_IN_LIMIT, DemandType, DistanceEvaluation,
                       DistanceMethod, DistanceRules, Status, evaluate_area,
                       evaluate_cross_level, figure_vs_limit, group_of)
from .geometry import Area, Point
from .sheetnames import page_label
from .pdf_ingest import ElementKind
from .routes import RouteNetwork
from .units import Scale

CRITICAL_KINDS = {ElementKind.NER_CRITICAL}

#: The only rooms that may be assigned to serve a functional area.
#:
#: **A NER, and nothing else.** COW was in this set until 2 Sep 2026 and should
#: not have been: a COW is a temporary cabin, and letting the engine hand it a
#: hundred permanent ports because it happened to be the nearest thing on the
#: sheet is a design decision the geometry has no business making. TER, IDF and
#: SHAFT were never here.
#:
#: Everything outside this set is recorded, drawn, moved and deleted exactly as
#: before — it simply never appears as an option to serve a space, never gets a
#: coverage radius, and never turns up in the port load. It is on the plan
#: because the plan should show what is in the building.
SERVING_KINDS = {ElementKind.NER_CRITICAL, ElementKind.NER_NONCRITICAL}


@dataclass
class NER:
    """An equipment room that can serve functional spaces.

    Only a NER is in :data:`SERVING_KINDS`. A TER, an IDF and a COW are recorded
    and drawn, but none of them is ever assigned as the serving room for a
    functional area — the TER↔NER relationship is a separate design rule that
    this tool does not invent, and a COW is a temporary cabin rather than a
    place a hundred permanent ports terminate.
    """

    key: str
    label: str
    kind: str
    point: Point
    critical: bool = False
    total_ports: Optional[int] = None
    used_ports: int = 0
    reserved_ports: int = 0
    locked: bool = False
    removable: bool = True
    movable: bool = True
    level: Optional[str] = None
    confidence: float = 1.0
    proposed: bool = False
    notes: list[str] = field(default_factory=list)
    #: "indoor" / "outdoor" / None (2.14.0): where the rack stands — it decides
    #: the kind of rack the venue is sent. Carried into the workbook, never
    #: used in a measurement.
    environment: Optional[str] = None
    #: The same room drawn on other sheets (3.0.0): one point per marker that
    #: is not the canonical one. A room drawn on four overlapping plans is one
    #: NER standing in one place; each sheet shows it where that sheet draws
    #: it. Distances are measured from the marker on the space's own sheet,
    #: and the marked-up PDF draws every marker where it is.
    other_points: list[Point] = field(default_factory=list)

    @property
    def points(self) -> list[Point]:
        """Every place this room is drawn — the canonical marker first."""
        return [self.point, *self.other_points]

    def point_on(self, page: int, near: Optional[Point] = None) -> Optional[Point]:
        """Where this room is drawn on ``page`` (the marker nearest ``near``
        when the sheet draws it twice), or ``None``."""
        here = [q for q in self.points if q.page == page]
        if not here:
            return None
        if near is None or len(here) == 1:
            return here[0]
        return min(here, key=lambda q: (q.x - near.x) ** 2 + (q.y - near.y) ** 2)

    @property
    def capacity_known(self) -> bool:
        return self.total_ports is not None

    @property
    def available_ports(self) -> Optional[float]:
        if self.total_ports is None:
            return None
        return max(0.0, self.total_ports - self.used_ports - self.reserved_ports)

    @property
    def can_serve(self) -> bool:
        return self.kind in SERVING_KINDS


@dataclass
class Assignment:
    space_key: str
    space_name: str
    area: Area
    demand_type: DemandType
    ports_required: float
    ner_key: Optional[str]
    ner_label: Optional[str]
    ner_critical: Optional[bool]
    evaluation: Optional[DistanceEvaluation]
    status: Status
    match_confirmed: bool
    reasons: list[str] = field(default_factory=list)
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    #: True when a person chose this room rather than the engine.
    pinned: bool = False
    #: When nothing serves the space, the closest NER and how far it is — so a
    #: row can say "nothing within 90 m; nearest is NER 5 at 120 m" instead of
    #: a blank. Empty when a room was assigned, or when nothing is measurable.
    nearest_label: Optional[str] = None
    nearest_distance_m: Optional[float] = None
    #: The page the serving room sits on. Usually the space's own page; when it
    #: is not, ``cross_level`` is true and the distance was either measured
    #: along a registered vertical route or not measured at all.
    ner_page: Optional[int] = None
    cross_level: bool = False
    #: The page the distance was measured on, when that is **not** the page the
    #: TEAP line was tied to: the same space is labelled on more than one sheet
    #: and the serving room is on one of the others. ``None`` otherwise.
    measured_page: Optional[int] = None
    #: A question, never an answer: the space is also labelled — unconfirmed —
    #: on another page, and from there a NER would reach it. Set only when the
    #: result as it stands is not "served", so it is asked only where the
    #: answer would change something.
    occurrence_hint: Optional[dict] = None
    #: "space" for a TEAP space, "wireless_point" for a point placed by hand.
    kind: str = "space"
    #: The space's criticality as the TEAP states it (``None``: not stated),
    #: and the criticality alert (5.6.0, owner's decision of 6 Nov 2026,
    #: "only alert"): ``distance.ALERT_*`` when a space marked critical is
    #: served by a NER that is not critical-rated and nobody chose that NER.
    #: The NER and the status stay what they are. ``critical_suggestion`` is
    #: the nearest critical NER, offered in Served by: ``ner_key``,
    #: ``ner_label``, ``distance_m``, ``method``, ``within_limit``, ``full``,
    #: ``free_ports``; ``None`` when no critical NER is measurable.
    space_critical: Optional[bool] = None
    criticality_alert: Optional[str] = None
    critical_suggestion: Optional[dict] = None

    @property
    def unplaced(self) -> bool:
        """A TEAP space with no room on the drawing (page 0, no position)."""
        return bool(self.area.meta.get("unplaced"))

    def to_dict(self) -> dict:
        return {
            "space_key": self.space_key,
            "space_name": self.space_name,
            # An unplaced space has no page: ``None``, never 0, so a screen
            # does not print "page 0".
            "page": self.area.page or None,
            "unplaced": self.unplaced,
            "level": self.area.level,
            "building": self.area.building,
            "space_code": self.area.space_code,
            "demand_type": self.demand_type.value,
            "ports_required": self.ports_required,
            "ner_key": self.ner_key,
            "ner_label": self.ner_label,
            "ner_critical": self.ner_critical,
            "ner_page": self.ner_page,
            "cross_level": self.cross_level,
            "measured_page": self.measured_page,
            "occurrence_hint": self.occurrence_hint,
            "kind": self.kind,
            # Where the space sits on its sheet, and which drawing room it is
            # (2.8.0) — what the viewer paints the verdict on. None for an
            # unplaced space, which has nowhere to be painted.
            "x": None if self.unplaced else self.area.label_point.x,
            "y": None if self.unplaced else self.area.label_point.y,
            "drawing_space_id": self.area.meta.get("drawing_space_id"),
            "status": self.status.value,
            "match_confirmed": self.match_confirmed,
            "pinned": self.pinned,
            "nearest_label": self.nearest_label,
            "nearest_distance_m": (None if self.nearest_distance_m is None
                                   else round(self.nearest_distance_m, 2)),
            "reasons": self.reasons,
            "alternatives": [{"ner_key": k, "distance_m": round(d, 2)} for k, d in self.alternatives],
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "space_critical": self.space_critical,
            "criticality_alert": self.criticality_alert,
            "criticality_alert_label": ALERT_LABEL.get(self.criticality_alert or ""),
            "critical_suggestion": self.critical_suggestion,
            # The TEAP space a Wi-Fi point belongs to (5.6.0-rc2; read by the
            # release gate: a point follows its space's NER).
            "of_space": (self.area.meta.get("space_key")
                         if self.area.meta.get("kind") == "wireless_point" else None),
        }


def _wireless_notes(area: Area) -> list[str]:
    """What the row says about the wireless the TEAP asked for.

    Since 4.0.0 the TEAP is the only source of ports (Marcelo, 16 Oct 2026:
    *"AP não é um produto do TEAP. Mantenha somente a informação que já existe
    que é o produto DWISA"*). The space's ``DWISA`` line is counted on the
    space's own row, on the NER that serves the space, whether or not access
    points are drawn for it; a drawn access point is a position on the sheet,
    never a port of its own.
    """
    m = area.meta
    if m.get("kind") == "wireless_point":
        if m.get("space_key"):
            return [f"Wi-Fi point of {m.get('space_name') or m['space_key']} "
                    f"({'tied by you' if m.get('tie_source') == 'manual' else 'placed from the TEAP' if m.get('tie_source') == 'teap' else 'nearest matched room, proposed'})"
                    f" — its port is the space's DWISA line, counted on the space's row"]
        return ["tied to no TEAP space: not a TEAP product, no port counted"]
    w = m.get("teap_wireless_ports")
    if w:
        n = m.get("wireless_points_tied") or 0
        drawn = ("Wi-Fi point drawn for it" if n
                 else "no Wi-Fi point drawn for it yet")
        return [f"wireless requested in the TEAP (DWISA, {w:g} port(s)) counted on this "
                f"row — {drawn}"]
    return []


@dataclass
class CoverageResult:
    assignments: list[Assignment]
    ner_load: dict[str, dict]
    exceptions: list[dict]
    uncovered: list[str]
    #: What the analysis has to say about the project as a whole rather than
    #: about one space: today, vertical links it ignored (R7-F5).
    notes: list[str] = field(default_factory=list)
    #: The criticality alerts (5.6.0): one per space marked critical that a
    #: NER not critical-rated serves - ``space_key``, ``space_name``,
    #: ``page``, ``alert``, ``ner_label``, ``detail``. Not exceptions: the
    #: space is served; the Exceptions step lists them apart.
    alerts: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        """The headline figures. **Spaces and wireless points are counted apart**:
        a wireless point is demand a person drew, not a TEAP space, and folding
        the two together would change what "spaces within limits" means."""
        ok = (Status.SERVED, Status.NEAR_LIMIT)
        spaces = [a for a in self.assignments if a.kind != "wireless_point"]
        points = [a for a in self.assignments if a.kind == "wireless_point"]
        point_keys = {a.space_key for a in points}
        by_status: dict[str, int] = {}
        by_group: dict[str, int] = {}
        for a in spaces:
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
            g = group_of(a.status.value).value
            by_group[g] = by_group.get(g, 0) + 1
        return {
            "spaces": len(spaces),
            "served": sum(1 for a in spaces if a.status in ok),
            "uncovered": sum(1 for k in self.uncovered if k not in point_keys),
            "exceptions": len(self.exceptions),
            "by_status": by_status,
            # The four piles a person reads the list in — see ``distance.Group``.
            "by_group": by_group,
            # TEAP spaces with no room on the drawing: served by a NER a person
            # chose, or still pending.
            "unplaced": sum(1 for a in spaces if a.unplaced),
            "unplaced_assigned": sum(1 for a in spaces if a.unplaced and a.ner_key),
            "ner_count": len(self.ner_load),
            # Every port of the study is a TEAP port (4.0.0): the spaces' lines,
            # the DWISA wireless line included. An access point drawn on a sheet
            # adds none.
            "total_ports": round(sum(a.ports_required for a in spaces), 1),
            "ports_on_ner": round(sum(a.ports_required for a in spaces if a.ner_key), 1),
            "ports_pending": round(sum(a.ports_required for a in spaces if not a.ner_key), 1),
            "wireless_ports": round(sum(
                float(a.area.meta.get("teap_wireless_ports") or 0) for a in spaces), 1),
            "wireless_points": len(points),
            "wireless_points_tied": sum(1 for a in points if a.area.meta.get("space_key")),
            "wireless_served": sum(1 for a in points if a.status in ok),
            "wireless_not_served": sum(1 for a in points if a.ner_key is None),
            "measured_from_another_page": sum(1 for a in spaces if a.measured_page),
            # Served spaces with a criticality alert, counted apart (5.6.0):
            # they stay in ``served``.
            "criticality_alerts": sum(1 for a in spaces if a.criticality_alert),
            "criticality_alerts_by_kind": {
                k: sum(1 for a in spaces if a.criticality_alert == k)
                for k in ALERT_LABEL if any(a.criticality_alert == k for a in spaces)},
        }


def _criticality_ok(space_critical: Optional[bool], ner: NER) -> bool:
    """A critical space must not be served by a non-critical room."""
    if space_critical is None:
        return True
    if space_critical and not ner.critical:
        return False
    return True


def free_ports_text(free: float, noun: str = "free") -> str:
    """Free ports as the notes print them, never a negative count (5.6.0):
    "12 free", and for a NER already over capacity "0 free (over by 9)"
    where 5.5 printed "-9 free". ``noun`` is the words after the number
    ("free", "port(s) free")."""
    if free < 0:
        over = round(-free, 1)
        if over > 0:
            n = f"{over:.0f}" if over == int(over) else f"{over:.1f}"
            return f"0 {noun} (over by {n})"
        free = 0.0
    return f"{free:.0f} {noun}"


def _occurrence_hint(area: Area, demand_type: DemandType, serving: Sequence[NER],
                     scale_for_page: dict[int, Scale], rules: DistanceRules
                     ) -> Optional[dict]:
    """The best *unconfirmed* occurrence of this space, if one would be served.

    Never used to assign anything. It turns "no NER on this level" into the
    question it really is — "page 32 shows the same code 34 m from NER 20; is
    that this space?" — and only when the answer would change the result.
    """
    best: Optional[dict] = None
    for occ in area.meta.get("proposed_occurrences") or []:
        scale = scale_for_page.get(occ.page)
        for ner in serving:
            npt = ner.point_on(occ.page, occ.representative_point())
            if npt is None:
                continue
            ev = evaluate_area(area=occ, ner_point=npt, scale=scale, rules=rules,
                               demand_type=demand_type, route_length_m=None,
                               match_confirmed=True)
            if ev.effective_distance_m is None or not ev.compliant:
                continue
            if best is None or ev.effective_distance_m < best["distance_m"]:
                best = {"link_id": occ.meta.get("link_id"), "page": occ.page,
                        "method": occ.meta.get("link_method") or "exact_code",
                        "ner_label": ner.label, "ner_key": ner.key,
                        "distance_m": round(ev.effective_distance_m, 2)}
    return best


#: What the row says when a TEAP space has no room on the drawing and nobody
#: has chosen a NER for it. Quoted by the Demand screen and the workbook.
UNPLACED_PENDING_REASON = (
    "no room on the drawing for this TEAP space — tie it to a room on "
    "TEAP × drawing, or choose the NER that serves it here")


def _assign_unplaced(area: Area, ports: float, demand_type: DemandType,
                     pin_key: Optional[str], who: str, serving: Sequence[NER],
                     load: dict[str, dict], rules: DistanceRules,
                     assignments: list[Assignment], exceptions: list[dict],
                     uncovered: list[str]) -> None:
    """A TEAP space with no room on the drawing: served by decision, or pending.

    Marcelo, 22 Sep 2026: *"todo espaço que precisa de serviço de rede no TEAP
    vai ter que encontrar um lugar no desenho ou, mesmo que eu não ache um
    lugar, eu tenho que amarrar com uma NER existente ou criada, pq aí é o meu
    conhecimento pessoal da venue que me permite dizer qual NER vai atender
    aquele espaço."* Until 2.7.0 such a space simply never reached the engine:
    not counted on any room, absent from the summary, invisible in the
    workbook except as "not analysed".

    With a room chosen: the ports count against it, the status is
    :attr:`Status.ASSIGNED_NOT_PLACED`, no distance is invented, and the
    exception list carries the reminder — the same treatment as a room on
    another level with no vertical route. Without one: pending, on the list,
    with the sentence that says what would resolve it.
    """
    room = next((n for n in serving if n.key == pin_key), None) if pin_key else None
    if room is None:
        reasons = [UNPLACED_PENDING_REASON]
        if pin_key:
            reasons.insert(0, f"the room {'the TEAP names' if who == 'the TEAP' else 'you assigned'} "
                              f"({pin_key}) is no longer available to this space")
        assignments.append(Assignment(
            space_key=area.key, space_name=area.name, area=area,
            demand_type=demand_type, ports_required=ports,
            ner_key=None, ner_label=None, ner_critical=None,
            evaluation=None, status=Status.NOT_ANALYSABLE,
            match_confirmed=False, reasons=reasons))
        uncovered.append(area.key)
        # Not an exception row: the Exceptions step already lists every TEAP
        # space with no room on the drawing, from the match rows, and the
        # workbook's TEAP sheet says "pending — no room on the drawing" on
        # the line itself. A third copy of the same list would be noise.
        return
    by = "the TEAP names it" if who == "the TEAP" else "you assigned it"
    ev = DistanceEvaluation(
        status=Status.ASSIGNED_NOT_PLACED, demand_type=demand_type,
        method=DistanceMethod.STRAIGHT_LINE, limit_m=rules.limit_for(demand_type),
        geometric_distance_m=None, effective_distance_m=None, margin_m=None,
        scale_label=None, scale_confirmed=False,
        routing_slack_factor=rules.routing_slack_factor,
        assumptions=["The TEAP space is not on the drawing; its position is unknown "
                     f"and no distance was measured against the "
                     f"{rules.limit_for(demand_type):g} m limit."],
        warnings=["Served by the room a person chose, from their knowledge of the "
                  "venue. Validate the cable route on site."],
        requires_human_validation=True)
    reasons = [
        f"no room on the drawing for this TEAP space — served by {room.label} "
        f"because {by}; its {ports:.0f} port(s) count against {room.label}'s "
        f"capacity and no distance was measured"]
    if not room.capacity_known:
        reasons.append("capacity unknown for this NER")
    status = Status.ASSIGNED_NOT_PLACED
    if room.capacity_known:
        free = (room.total_ports - room.used_ports - room.reserved_ports
                - load[room.key]["assigned_ports"])
        if free < ports:
            status = Status.INSUFFICIENT_CAPACITY
            reasons.append(f"and it has {free_ports_text(free, 'port(s) free')} against "
                           f"{ports:.0f} required — over capacity")
    load[room.key]["assigned_ports"] += ports
    load[room.key]["spaces"].append(area.key)
    load[room.key]["wireless_ports"] += float(area.meta.get("teap_wireless_ports") or 0)
    assignments.append(Assignment(
        space_key=area.key, space_name=area.name, area=area,
        demand_type=demand_type, ports_required=ports,
        ner_key=room.key, ner_label=room.label, ner_critical=room.critical,
        evaluation=ev, status=status, match_confirmed=True,
        reasons=reasons, pinned=True, ner_page=room.point.page))
    if status is Status.INSUFFICIENT_CAPACITY:
        uncovered.append(area.key)
        exceptions.append({
            "space_key": area.key, "space_name": area.name,
            "status": status.value, "page": None,
            "detail": f"assigned to {room.label} {'by the TEAP' if who == 'the TEAP' else 'by you'}; "
                      f"the room is out of ports",
        })
    else:
        exceptions.append({
            "space_key": area.key, "space_name": area.name,
            "status": status.value, "page": None,
            "detail": f"assigned to {room.label} {'by the TEAP' if who == 'the TEAP' else 'by you'} "
                      f"with no room on the drawing; distance not measured — "
                      f"validate the route on site, or tie the space to a room "
                      f"on TEAP × drawing when the sheet shows it",
        })


#: A traced route counts only when enough of it is actually traced (5.3.0
#: rc5). Marcelo, 4 Nov 2026, after the independent tester showed a tray
#: drawn for one room "catching" its neighbours through the 15 m join, even
#: with 0.0 m traced: a route counts from MIN_TRACED_M metres traced AND more
#: traced than the two straight lead-ins together. Below that the space is
#: measured in a straight line, as if no route were drawn. On his 15 projects
#: this releases 3 measurements, all with 0.0 m traced.
MIN_TRACED_M = 5.0

#: The 5 m is compared with the traced length as the notes print it, to the
#: centimetre (R7-F4; 5.5.1-rc2, tester N4): a route whose note says "5.00 m"
#: traced counts and one that says "4.99 m" does not, and the same goes for
#: "more traced than lead-in". The traced length itself is measured on the
#: coordinates as drawn, never on the routing graph's snap grid, which used to
#: take up to 0.1 m off a tray at 1 unit = 0.1 m (5.01 m drawn did not count).
#: Every traced and lead-in figure in the notes has two decimals.
ROUTE_DECIMALS = 2


def _m(x: float) -> float:
    """A route length as the notes print it."""
    return float(f"{float(x or 0.0):.{ROUTE_DECIMALS}f}")


def route_counts(r: Optional[dict]) -> bool:
    """Is this route mostly traced, or mostly straight lead-in?"""
    if not r:
        return False
    traced = _m(r.get("traced_m"))
    return traced >= MIN_TRACED_M and traced > _m(r.get("lead_in_m"))


def _not_counting_note(ner_label: str, r: dict, where: str) -> str:
    """The sentence for a route that reaches a NER and does not count."""
    via = r.get("vertical_links") or []
    return (f"a {'vertical ' if via else ''}route reaches {ner_label} {where}"
            + (f" (via {', '.join(str(v) for v in via)})" if via else "")
            + f" but only {float(r['traced_m']):.2f} m of it is traced against "
            f"{float(r['lead_in_m']):.2f} m of straight lead-in - it does not count "
            f"(a route counts from {MIN_TRACED_M:g} m traced and more traced than lead-in)")

def _scales_confirmed(r: dict, scale_for_page: dict[int, Scale]) -> bool:
    """Is the scale of every sheet a route runs on known and confirmed?
    (5.5.1-rc4, tester R3-2: a route to another level, its lead-ins measured
    on a scale nobody had confirmed, came back definitively served.)"""
    for page in r.get("pages") or ():
        sc = scale_for_page.get(page)
        if sc is None or not sc.confirmed:
            return False
    return True


def _route_far_m(occ: Area, here_pt: Point, r: dict, scale: Scale) -> Optional[float]:
    """The room's farthest vertex measured along a route: from that vertex,
    straight, to where the route joins the space's sheet, then the rest of
    the route (5.5.1-rc4, tester R3-3, across levels). Since 5.6.0 the
    same measure is used for every traced route, on the space's own sheet
    too and on every project (tester R4-1): the straight line from the
    vertex to the NER is not the cable that serves the corner, and a NER on
    the floor and one upstairs are judged alike."""
    if not occ.polygon or r.get("joint_a") is None:
        return None
    jx, jy = r["joint_a"]
    joint = Point(jx, jy, occ.page)
    far = occ.farthest_vertex_from(joint)
    if far is None:
        return None
    lead_a = scale.to_metres(here_pt.distance_units(joint))
    return r["length_m"] - lead_a + scale.to_metres(far[1])


def _unmeasured(area: Area, serving: Sequence[NER], scale_for_page: dict[int, Scale],
                has_verticals: bool) -> tuple[Optional[str], Optional[str]]:
    """The row and exception sentences when no NER is measurable because the
    space's sheet has no scale (5.5.1-rc5, tester R4-9): the row used to say
    "no NER on this level and no vertical route registered" while a NER was
    on that very sheet, or a shaft was registered. ``(None, None)`` when the
    sheet has a scale, or when that older sentence is true."""
    pages = {o.page for o in (area, *area.occurrences)}
    unscaled = sorted(p for p in pages if scale_for_page.get(p) is None)
    if not unscaled:
        return None, None
    on_level = sorted({n.label for n in serving
                       if any(n.point_on(p) is not None for p in unscaled)})
    elsewhere = has_verticals and any(all(q.page not in pages for q in n.points)
                                      for n in serving)
    parts = []
    if on_level:
        parts.append(", ".join(on_level[:3])
                     + (f" and {len(on_level) - 3} more" if len(on_level) > 3 else "")
                     + " on this level")
    if elsewhere:
        parts.append("a route through a shaft or riser to another level")
    if not parts:
        return None, None
    where = " and ".join(page_label(p) for p in unscaled)
    has = "has" if len(unscaled) == 1 else "have"
    return (f"no NER is measurable from this space: {where} {has} no scale, so "
            + " and ".join(parts) + " could not be measured - set the sheet's scale",
            f"{where} {has} no scale: no NER can be measured from this space")


def compute_coverage(
    *,
    areas: Sequence[tuple[Area, float, DemandType, Optional[bool], bool]],
    ners: Sequence[NER],
    scale_for_page: dict[int, Scale],
    rules: DistanceRules,
    network: Optional[RouteNetwork] = None,
    respect_capacity: bool = True,
    pinned: Optional[dict[str, str]] = None,
    pin_sources: Optional[dict[str, str]] = None,
    cross_level: Optional[set[str]] = None,
    enforce_criticality: bool = True,
) -> CoverageResult:
    """Assign every area to the most suitable NER.

    ``enforce_criticality`` False (5.6.0, the default of every project, owner's
    decision of 6 Nov 2026 "only alert"): ``is_critical`` never changes which
    NER serves a space - the space is assigned exactly as with ``None`` - and
    a space marked critical that ends up on a NER not critical-rated, not
    chosen by a person, gets a criticality alert (``Assignment.criticality_alert``)
    with the nearest critical NER as the suggestion. True (an engine caller,
    or a project with ``Project.criticality_enforced``): a critical space is
    never served by a non-critical NER, as before.

    ``areas`` items are ``(area, ports_required, demand_type, is_critical,
    match_confirmed)``. ``is_critical`` may be ``None`` when the TEAP does not say,
    in which case the engine proposes the nearest compatible NER but flags the
    assignment for validation instead of inventing a classification.

    ``pinned`` maps a space key to the NER key a **person** chose for it, and
    that choice wins over the nearest-compatible answer. The reason it has to
    exist: putting a second NER beside an existing one is a statement that
    geometry is no longer the right criterion here — the old room is full, or
    critical and non-critical services must not share it. Distance can see
    neither, so the decision has to be sayable.

    A pin settles **which** room and nothing else. Distance, limit, criticality
    and capacity are still evaluated and still reported exactly as they come
    out: a pinned room 200 m away comes back out of limit; one whose ports are
    exhausted comes back over capacity. Letting the pin silence those would
    make it a way of laundering a violation — and would defeat the case it is
    for, since the whole point is that you pin *because* the other room is
    overloaded and you need the tool to keep saying so.

    ``cross_level`` is the set of space keys whose person has **declared** that
    the pinned room sits on another level — the ``other level`` box on the
    Demand row. The engine never assumes a room reaches another floor (that
    rule is older than this parameter and unchanged): a pinned room on another
    page with no vertical route registered is honoured only when this says
    so, and then with no distance and the status
    :attr:`Status.CROSS_LEVEL_NOT_MEASURED`. Without the declaration the space
    is left unassigned and the row asks for it — the load is not quietly moved
    to a room nobody chose. A room the TEAP names is a declaration in itself.
    """
    # Copies: the access points below are pinned to their space's NER as the
    # run goes (4.2.0), and the caller's maps must not change under it.
    pinned = dict(pinned or {})
    # Who fixed each pin — "you" unless told otherwise. A TEAP that names the
    # room is the other source, and the row has to say which it was.
    pin_sources = dict(pin_sources or {})
    cross_level = cross_level or set()
    serving = [n for n in ners if n.can_serve]
    load: dict[str, dict] = {
        n.key: {
            "label": n.label, "critical": n.critical, "page": n.point.page,
            "total_ports": n.total_ports, "assigned_ports": 0.0, "spaces": [],
            "capacity_known": n.capacity_known, "utilisation_pct": None,
            "proposed": n.proposed, "environment": n.environment,
            # Wireless points drawn by hand that this room carries, and the
            # ports they take — part of ``assigned_ports``, shown apart.
            # Access points drawn that this room reaches (a count of markers,
            # no ports), and the TEAP wireless ports (DWISA) of the spaces it
            # serves — part of ``assigned_ports``, shown apart (4.0.0).
            "wireless_points": 0, "wireless_ports": 0.0,
        } for n in serving
    }

    assignments: list[Assignment] = []
    exceptions: list[dict] = []
    uncovered: list[str] = []
    alerts: list[dict] = []
    #: space key -> its criticality as the TEAP states it, said on every row.
    crit_of: dict[str, Optional[bool]] = {}
    label_of = {n.key: n.label for n in serving}
    cross_note_len: dict[str, float] = {}

    def _cross_order(keys) -> list[str]:
        """NERs reached only by a route that does not count, nearest first,
        then by label (R4-6)."""
        return sorted(keys, key=lambda k: (round(cross_note_len.get(k, 0.0), 9),
                                           str(label_of.get(k, k)), k))

    # The access points come after every TEAP space (4.2.0): a point follows
    # the NER that serves its space, whatever rule chose that NER, so the
    # spaces have to be answered first. Until 4.1.0 a point followed its
    # space only when a person or the TEAP had fixed the space's NER, and
    # otherwise went to the NER nearest to where it is drawn — its port (the
    # space's DWISA line) on one NER and the access point on another. On the
    # VBB workbook: SEC.005-01..07's seven ports on NER_23.1, their six
    # points on NER_25.1 and NER_27.1.
    def _is_point(t) -> bool:
        return t[0].meta.get("kind") == "wireless_point"

    ordered = sorted(areas, key=lambda t: (_is_point(t), -t[1], t[0].key))
    space_ner: Optional[dict[str, Optional[str]]] = None

    for area, ports, demand_type, is_critical, match_confirmed in ordered:
        # What the TEAP says, kept for the row and the alert; what the
        # assignment obeys, which is nothing unless criticality is enforced.
        teap_critical = is_critical
        crit_of[area.key] = teap_critical
        if not enforce_criticality:
            is_critical = None
        if _is_point((area,)) and area.key not in pinned:
            if space_ner is None:
                space_ner = {a.space_key: a.ner_key for a in assignments
                             if a.area.meta.get("kind") != "wireless_point"}
            sk = area.meta.get("space_key")
            if sk and sk in space_ner:
                if space_ner[sk]:
                    pinned[area.key] = space_ner[sk]
                    pin_sources[area.key] = "the space"
                else:
                    # Its space has no NER yet: the point waits with it. It
                    # used to be counted on whichever NER was nearest, a room
                    # that carries none of the space's ports.
                    reason = (f"its TEAP space ({area.meta.get('space_name') or sk}) has no "
                              f"NER yet — a Wi-Fi point follows the NER that serves its "
                              f"space; choose that NER on Demand per space")
                    assignments.append(Assignment(
                        space_key=area.key, space_name=area.name, area=area,
                        demand_type=demand_type, ports_required=ports,
                        ner_key=None, ner_label=None, ner_critical=None,
                        evaluation=None, status=Status.NOT_ANALYSABLE,
                        match_confirmed=match_confirmed,
                        reasons=_wireless_notes(area) + [reason]))
                    uncovered.append(area.key)
                    exceptions.append({"space_key": area.key, "space_name": area.name,
                                       "status": Status.NOT_ANALYSABLE.value,
                                       "page": area.page, "detail": reason})
                    continue
        if area.meta.get("unplaced"):
            # A TEAP space the drawing does not show. Nothing can be measured,
            # so the only way it gets a room is a person choosing one — and
            # when they do, the ports count against that room exactly as they
            # would for a room on another level: counted, not measured, and
            # said so. Without a choice the space is pending, listed as such,
            # never quietly dropped from the study.
            _assign_unplaced(area, ports, demand_type, pinned.get(area.key),
                             pin_sources.get(area.key, "you"), serving, load,
                             rules, assignments, exceptions, uncovered)
            continue
        scale = scale_for_page.get(area.page)
        scored: list[tuple[float, NER, DistanceEvaluation]] = []
        #: NER key -> the occurrence of this space its distance was measured
        #: from. The tied occurrence first, so it wins a tie; then every other
        #: confirmed label of the same space, each on its own sheet and scale.
        via: dict[str, Area] = {}

        #: NERs a traced route reaches from this space (5.3.0). Marcelo,
        #: 4 Nov 2026, after a route on VKK made a room *lose* to one with no
        #: route: "sempre medir pela rota desenhada". A route length is never
        #: shorter than the straight line, so comparing one room's route with
        #: another room's straight line punished the room somebody had traced.
        #: When a route reaches the space, only the rooms it reaches compete,
        #: and a route measured on any sheet the space appears on beats a
        #: straight line measured on another.
        routed: set[str] = set()
        #: NER key -> the sentence for a route that reaches it from another
        #: level and does not count (R7-F3): that NER is no candidate, and
        #: the row says why instead of "no vertical route registered".
        cross_notes: dict[str, str] = {}
        #: ... and the length of that route, which orders the notes (R4-6).
        cross_note_len.clear()
        ignored_cross: set[str] = set()
        has_verticals = network is not None and network.has_verticals
        #: NER key -> (the counting route, its page) for a NER measured only
        #: through a shaft or riser from another level ("same floor first",
        #: 5.5.1-rc3): a candidate only when no NER on the space's own level
        #: serves it within the limit.
        #: (Named ``cross_level`` until rc3, which hid the parameter of the
        #: same name - tester R3-10.)
        cross_routes: dict[str, tuple[dict, int]] = {}
        same_routed: set[str] = set()
        for ner in serving:
            best: Optional[tuple[float, DistanceEvaluation, Area]] = None
            best_route: Optional[tuple[float, DistanceEvaluation, Area]] = None
            best_cross: Optional[tuple[float, DistanceEvaluation, Area, dict, int]] = None
            for occ in (area, *area.occurrences):
                occ_scale = scale_for_page.get(occ.page)
                # The room's marker on this sheet when it has one (3.0.0):
                # a room drawn on two overlapping plans is measured where the
                # space's own sheet draws it, not from the other sheet's
                # coordinates.
                for npt in ner.points:
                    route_len = None
                    r = None
                    ignored = None
                    detour = None
                    shorter = None
                    #: For a route through a shaft or riser: are the scales
                    #: of every sheet it runs on confirmed (R3-2), and the
                    #: room's farthest vertex measured along it (R3-3).
                    scales_ok = None
                    far_m = None
                    same_level = occ.page == npt.page
                    if network is not None:
                        here_pt = occ.representative_point()
                        # A NER on the space's own floor is measured on that
                        # floor (R7-F2): along its traced segments, never
                        # through a shaft or riser. Two risers and a tray on
                        # the floor above made a NER 14.9 m away 104.9 m
                        # "by route". The way round another level is looked
                        # at apart, below, and used only when it is shorter.
                        # A route that is mostly straight lead-in does not
                        # count (rc5), and that holds through a shaft to
                        # another level too (rc7, Marcelo, 4 Nov 2026: a 4 m
                        # shaft with 24 m of lead-in took a space from a
                        # NER 14.9 m away on its own floor). Across levels
                        # there is no straight line to fall back on, so a
                        # NER no counting connection reaches is simply not a
                        # candidate for the space. Across levels every
                        # connection is looked at (each shaft or riser foot
                        # within reach) and the shortest one that counts is
                        # the measurement (5.5.1-rc2, tester N1: a 1 m sleeve
                        # beside an 8 m riser took the space's only valid
                        # service away); a shorter one that does not count
                        # is named, never obeyed. Two links on the same feet
                        # are one connection, the shorter link (R7-F1).
                        if occ_scale is None:
                            r = None
                        elif not same_level:
                            # Each end is measured on its own sheet's scale
                            # (R3-5): the NER's lead-in and join reach with
                            # the NER sheet's, not the space's.
                            opts = network.route_options(
                                here_pt, npt, occ_scale,
                                destination_scale=scale_for_page.get(npt.page))
                            r = next((o for o in opts if route_counts(o)), None)
                            if opts and r is None:
                                r = opts[0]
                            elif opts and opts[0] is not r:
                                shorter = opts[0]
                        else:
                            r = network.route_length_m(here_pt, npt, occ_scale,
                                                       level_only=has_verticals)
                            if has_verticals:
                                crossing = [o for o in network.route_options(
                                    here_pt, npt, occ_scale) if o["crosses_levels"]]
                                detour = next((o for o in crossing if route_counts(o)),
                                              crossing[0] if crossing else None)
                        if r and not route_counts(r):
                            ignored, r = r, None
                        if r:
                            route_len = r["length_m"]
                            # Every traced route, on every project (5.6.0,
                            # tester R4-1/R4-2; 5.5.1-rc5 did it only where
                            # the project has shafts or risers): no
                            # definitive result while a sheet it runs on has
                            # an unconfirmed scale, and the room's far
                            # corner measured along it - from the corner,
                            # straight, to where the route joins the sheet,
                            # then along the route.
                            scales_ok = _scales_confirmed(r, scale_for_page)
                            far_m = _route_far_m(occ, here_pt, r, occ_scale)
                    if not same_level and route_len is None:
                        if ignored and ner.key not in cross_notes:
                            cross_notes[ner.key] = (
                                _not_counting_note(ner.label, ignored,
                                                   f"on {page_label(npt.page)}")
                                + f", so {ner.label}, on another level, is not a "
                                  f"candidate for this space")
                            cross_note_len[ner.key] = float(ignored["length_m"])
                            ignored_cross.add(ner.key)
                        continue
                    ev = evaluate_area(area=occ, ner_point=npt, scale=occ_scale,
                                       rules=rules, demand_type=demand_type,
                                       route_length_m=route_len,
                                       match_confirmed=match_confirmed,
                                       route_far_m=far_m,
                                       route_scales_confirmed=scales_ok)
                    if shorter is not None and r:
                        ev.assumptions.append(
                            _not_counting_note(ner.label, shorter, "by a shorter connection")
                            + f"; measured through "
                              f"{', '.join(str(v) for v in r['vertical_links']) or 'the route'}"
                              f" instead, which counts.")
                    if detour is not None:
                        links = ", ".join(str(v) for v in detour["vertical_links"])
                        here_m = ev.effective_distance_m
                        # The way round another level is judged as the route
                        # on this one is, far corner included (R4-1), and the
                        # two judged figures are compared.
                        dev = None
                        if route_counts(detour) and here_m is not None:
                            dev = evaluate_area(area=occ, ner_point=npt, scale=occ_scale,
                                                rules=rules, demand_type=demand_type,
                                                route_length_m=detour["length_m"],
                                                match_confirmed=match_confirmed,
                                                route_far_m=_route_far_m(
                                                    occ, here_pt, detour, occ_scale),
                                                route_scales_confirmed=_scales_confirmed(
                                                    detour, scale_for_page))
                        if dev is not None and dev.effective_distance_m is not None \
                                and dev.effective_distance_m < here_m - 1e-9:
                            # Shorter round another level than on this one:
                            # the traced route stands.
                            ev = dev
                            ev.assumptions.append(
                                f"measured through another level (via {links}): "
                                f"{dev.effective_distance_m:.1f} m, shorter than the "
                                f"{here_m:.1f} m measured on this level.")
                            r, ignored = detour, None
                        elif here_m is not None:
                            there_m = (dev.effective_distance_m if dev is not None
                                       and dev.effective_distance_m is not None
                                       else detour["length_m"])
                            ev.assumptions.append(
                                f"a route through another level (via {links}) reaches "
                                f"{ner.label} in {there_m:.1f} m"
                                + ("" if route_counts(detour) else
                                   f" (and does not count: {detour['traced_m']:.2f} m traced "
                                   f"against {detour['lead_in_m']:.2f} m of lead-in)")
                                + f" - not used: a NER on the space's own floor is "
                                  f"measured on that floor ({here_m:.1f} m) unless the "
                                  f"way round another level is shorter.")
                    if ignored:
                        if ignored.get("joint_ties"):
                            ev.detail["joint_ties"] = list(ignored["joint_ties"])
                        ev.assumptions.append(
                            f"a route reaches {ner.label} but only "
                            f"{ignored['traced_m']:.2f} m of it is traced against "
                            f"{ignored['lead_in_m']:.2f} m of straight lead-in - it does "
                            f"not count (a route counts from {MIN_TRACED_M:g} m traced and "
                            f"more traced than lead-in); measured in a straight line.")
                    if r:
                        # Joints tied for the nearest at the two ends (rc2):
                        # read by the release gate, never by a verdict.
                        if r.get("joint_ties"):
                            ev.detail["joint_ties"] = list(r["joint_ties"])
                        # How much of the length was traced and how much is
                        # the straight lead-in at the two ends (4.7.0).
                        ev.assumptions.append(
                            f"{r['traced_m']:.2f} m along the traced route + "
                            f"{r['lead_in_m']:.2f} m straight lead-in at the two ends.")
                    if ev.effective_distance_m is None:
                        continue
                    cand = (ev.effective_distance_m, ev, occ)
                    if not same_level:
                        if best_cross is None or cand[0] < best_cross[0]:
                            best_cross = (*cand, r, npt.page)
                    elif r:
                        if best_route is None or cand[0] < best_route[0]:
                            best_route = cand
                    elif best is None or cand[0] < best[0]:
                        best = cand
            # The space's own level first: a route on it, else the straight
            # line on it; another level only for a NER with no marker on any
            # sheet the space is on.
            if best_route is not None:
                best = best_route
                routed.add(ner.key)
                same_routed.add(ner.key)
            elif best is None and best_cross is not None:
                best = best_cross[:3]
                routed.add(ner.key)
                cross_routes[ner.key] = (best_cross[3], best_cross[4])
            if best is not None:
                scored.append((best[0], ner, best[1]))
                via[ner.key] = best[2]

        scored.sort(key=lambda t: (t[0], t[1].key))
        # Same floor first (owner decision on N11, Marcelo, 6 Nov 2026: "same
        # floor first"). A NER on another level, reached through a shaft or
        # riser, is a candidate only when no NER on the space's own level
        # serves it within the limit - measured as the same-level rules
        # measure it: when a counting route on this level reaches the space,
        # only the NERs it reaches; otherwise the straight line. Until rc2 a
        # counting shaft route set aside every straight-line NER on the
        # floor, however near (tester D1: UP 45.8 m by a 43.8 m shaft over
        # HERE 14.9 m away). A person's pin stays a candidate.
        #
        # "Serves" (5.5.1-rc4, tester R3-1): within the limit, allowed by
        # criticality, and with the free ports the space needs when the
        # room's capacity is known. In rc3 a full room on the floor kept the
        # space and the capacity fallback said "every NER within the distance
        # limit is out of ports" while the NER upstairs had 48 free. A floor
        # NER within the limit on a sheet whose scale is not confirmed keeps
        # the space provisionally: nothing is definitive until the scale is,
        # and the NER is the one the confirmed scale would give (R3-2).
        keep = pinned.get(area.key)
        cross_aside: list[tuple[float, NER, DistanceEvaluation]] = []
        floor: Optional[tuple[float, NER, DistanceEvaluation]] = None
        #: Floor NERs that were compared and did not serve, with the reason
        #: (R3-4: they used to be listed as "not compared (no route to them)").
        floor_failed: list[tuple[tuple[float, NER, DistanceEvaluation], str]] = []

        def _floor_fails(t) -> Optional[str]:
            _d, n, e = t
            eff = e.effective_distance_m
            # Never printed onto the limit (R4-7).
            fig = (f"{figure_vs_limit(eff, e.limit_m)} "
                   + ("by the traced route" if e.method is DistanceMethod.ROUTE
                      else "in a straight line"))
            if e.status is Status.SCALE_NOT_CONFIRMED:
                if eff > e.limit_m:
                    return (f"{fig}, over the {e.limit_m:g} m limit (on a scale not "
                            f"yet confirmed)")
            elif e.status is Status.OUT_OF_LIMIT:
                return f"{fig}, over the {e.limit_m:g} m limit"
            elif not e.compliant:
                return f"{fig}: {e.status.value.replace('_', ' ')}"
            if not _criticality_ok(is_critical, n):
                return f"{fig}, not critical-rated for a space marked critical"
            if respect_capacity and n.capacity_known:
                free = (n.total_ports - n.used_ports - n.reserved_ports
                        - load[n.key]["assigned_ports"])
                if free < ports:
                    return (f"{fig}, out of ports ({free_ports_text(free)} against "
                            f"{ports:.0f} required)")
            return None

        def _out_of_ports(n: NER) -> str:
            """", out of ports (0 free against 2 required)" for a NER a row
            offers that has no room for this space (5.6.0-rc2, tester T1-8),
            "" otherwise."""
            if not (respect_capacity and n.capacity_known):
                return ""
            free = (n.total_ports - n.used_ports - n.reserved_ports
                    - load[n.key]["assigned_ports"])
            if free >= ports:
                return ""
            return f", out of ports ({free_ports_text(free)} against {ports:.0f} required)"

        if cross_routes:
            here_level = [t for t in scored if t[1].key not in cross_routes]
            if same_routed:
                here_level = [t for t in here_level
                              if t[1].key in same_routed or t[1].key == keep]
            for t in here_level:
                why = _floor_fails(t)
                if why is None:
                    floor = t
                    break
                floor_failed.append((t, why))
            if floor is not None:
                floor_failed = []
                cross_aside = [t for t in scored
                               if t[1].key in cross_routes and t[1].key != keep]
                aside_keys = {t[1].key for t in cross_aside}
                scored = [t for t in scored if t[1].key not in aside_keys]
                routed -= aside_keys
                # A NER on another level chosen in Served by is used, and
                # does not set the floor's NERs aside as "not compared": the
                # one that serves stays among the alternatives (R3-8).
                if keep in cross_routes:
                    routed.discard(keep)
        failed_keys = {t[1].key for t, _ in floor_failed}
        # A route reaches the space: the rooms it does not reach step aside
        # (a person's pin always stays a candidate - the pin rule decides it).
        set_aside: list[tuple[float, NER, DistanceEvaluation]] = []
        #: A critical space (criticality enforced) that the route reaches
        #: only at NERs not critical-rated, or not within the limit: a
        #: critical NER within the limit in a straight line stays compared
        #: (5.6.0-rc2, tester T1-4) - it used to be set aside and the space
        #: got "No critical NER in limit" with that NER 24 m away.
        crit_kept: list[tuple[float, NER, DistanceEvaluation]] = []
        if routed:
            if is_critical and not any(
                    t[1].key in routed and _criticality_ok(is_critical, t[1])
                    and t[2].effective_distance_m is not None
                    and t[2].effective_distance_m <= t[2].limit_m for t in scored):
                crit_kept = [t for t in scored
                             if t[1].key not in routed and t[1].key != keep
                             and _criticality_ok(is_critical, t[1])
                             and t[2].effective_distance_m is not None
                             and t[2].effective_distance_m <= t[2].limit_m]
            kept = {t[1].key for t in crit_kept}
            set_aside = [t for t in scored
                         if t[1].key not in routed and t[1].key != keep and t[1].key not in kept]
            scored = [t for t in scored
                      if t[1].key in routed or t[1].key == keep or t[1].key in kept]

        chosen: Optional[tuple[Optional[float], NER, DistanceEvaluation]] = None
        reasons: list[str] = _wireless_notes(area)
        not_compared = [t for t in set_aside if t[1].key not in failed_keys]
        if not_compared:
            reasons.append(
                "a traced route reaches this space, so only the NER(s) it reaches "
                "were compared; not compared (no route to them): "
                + ", ".join(f"{n.label} at {d:.1f} m in a straight line"
                            for d, n, _ in not_compared[:3])
                + (f" and {len(not_compared) - 3} more" if len(not_compared) > 3 else ""))
        if crit_kept:
            reasons.append(
                "no critical-rated NER the traced route reaches is within the limit, so "
                "the critical-rated NER(s) within the limit in a straight line were "
                "compared too: "
                + ", ".join(f"{n.label} at {figure_vs_limit(d, e.limit_m, 1)} in a straight line"
                            for d, n, e in crit_kept[:3]))
        if floor_failed:
            reasons.append(
                "no NER on this level serves this space within the limit, so the "
                "NER(s) on other levels a shaft or riser route reaches were compared: "
                + "; ".join(f"{t[1].label} at {why}" for t, why in floor_failed[:3])
                + (f"; and {len(floor_failed) - 3} more on this level"
                   if len(floor_failed) > 3 else ""))
        for d, n, ev in cross_aside[:3]:
            r, page = cross_routes[n.key]
            links = ", ".join(str(v) for v in r.get("vertical_links") or [])
            fev = floor[2]
            ffig = figure_vs_limit(floor[0], fev.limit_m)
            serves = (f"is within {fev.limit_m:g} m ({ffig}, on a scale not "
                      f"yet confirmed)" if fev.status is Status.SCALE_NOT_CONFIRMED
                      else f"serves within {fev.limit_m:g} m ({ffig})")
            reasons.append(
                f"a vertical route{f' (via {links})' if links else ''} reaches {n.label} on "
                f"{page_label(page)} in {r['length_m']:.2f} m - not used: {floor[1].label} "
                f"on this level {serves}; a NER on another level is a candidate only when "
                f"none on the space's own level serves within the limit")
        if len(cross_aside) > 3:
            reasons.append(f"and {len(cross_aside) - 3} more NER(s) on other levels not "
                           f"used, for the same reason")
        # Routes from another level that do not count, for the NERs nothing
        # else measured (R7-F1/F3): the row says they were seen and why they
        # were not used.
        # Nearest first, then by label: never the order the NERs were
        # registered in (5.5.1-rc5, tester R4-6).
        unused = [cross_notes[k] for k in _cross_order(k for k in cross_notes if k not in via)]
        reasons.extend(unused[:3])
        if len(unused) > 3:
            reasons.append(f"and {len(unused) - 3} more NER(s) on other levels reached "
                           f"only by routes that do not count")
        #: Every NER compared for this space, nearest first: those the rules
        #: left, and the floor NERs the floor gate compared and found not to
        #: serve (5.5.1-rc5, tester R4-3/R4-8). They stay in sight: in the
        #: alternatives, in the capacity fallback ("same floor first"), and
        #: as the nearest NER when nothing serves. Without a NER reached
        #: through a shaft or riser this is ``scored`` itself.
        _left = {t[1].key for t in scored}
        considered = sorted(scored + [t for t, _w in floor_failed if t[1].key not in _left],
                            key=lambda t: (t[0], t[1].key))
        rejected_for_capacity = False
        rejected_for_criticality = False
        # A pin to a room on another page that nobody has declared cross-level:
        # the space stays unassigned and the row says what it is waiting for.
        awaiting_declaration: Optional[NER] = None

        # A person's choice, honoured first and reported honestly.
        pin_key = pinned.get(area.key)
        if pin_key:
            pin = next((t for t in scored if t[1].key == pin_key), None)
            who = pin_sources.get(area.key, "you")
            by = ("the TEAP names it" if who == "the TEAP"
                  else "the TEAP space it stands in for is served by it" if who == "the space"
                  else "you assigned it")
            if pin is None:
                # Not measurable from here — but is it still a room? A pinned
                # NER on another page with no vertical route is exactly the
                # case Marcelo raised on 10 Sep 2026: the room one floor down,
                # in the same spot. It is honoured when he has said so.
                elsewhere = next((n for n in serving if n.key == pin_key
                                  and n.point_on(area.page) is None), None)
                # Since 2.10.0 the choice itself is the declaration (Marcelo,
                # 24 Sep 2026: "se eu escolhi, deveria ser served"): a room a
                # person picked on another page is counted from there, not
                # measured. ``cross_level`` is still accepted and still means
                # the same thing; it is no longer required.
                if elsewhere is not None:
                    ev = evaluate_cross_level(area=area, ner_point=elsewhere.point,
                                              scale=scale, rules=rules,
                                              demand_type=demand_type,
                                              match_confirmed=match_confirmed)
                    pin = (None, elsewhere, ev)
                elif elsewhere is not None:
                    awaiting_declaration = elsewhere
            if pin is not None:
                dist, ner, ev = pin
                if ev.status is Status.CROSS_LEVEL_NOT_MEASURED or (
                        dist is None and ner.point_on(area.page) is None):
                    between = ("and the vertical route that reaches it does not count "
                               "(above)" if ner.key in ignored_cross else
                               "with no shaft, riser or vertical route registered "
                               "between them")
                    reasons.append(
                        f"served by {ner.label} because {by}; it is on {page_label(ner.point.page)} "
                        f"and this space on {page_label(area.page)} — "
                        f"another level — {between}, so the distance was not "
                        f"measured. The {ports:.0f} port(s) count against "
                        f"{ner.label}'s capacity")
                else:
                    reasons.append(f"served by {ner.label} because {by}, "
                                   f"not because it is the nearest")
                    if ner.key in cross_routes and floor is not None:
                        # The floor's NER is named, not hidden (R3-8).
                        fev = floor[2]
                        ffig = figure_vs_limit(floor[0], fev.limit_m)
                        reasons.append(
                            f"{floor[1].label} on this level "
                            + (f"is within {fev.limit_m:g} m ({ffig}, on a "
                               f"scale not yet confirmed)"
                               if fev.status is Status.SCALE_NOT_CONFIRMED else
                               f"serves within {fev.limit_m:g} m ({ffig})")
                            + f" and would be used without this choice: a NER on "
                              f"another level is a candidate only when none on the "
                              f"space's own level serves within the limit")
                if not ev.compliant and ev.effective_distance_m is not None:
                    reasons.append(
                        f"and it is {figure_vs_limit(ev.effective_distance_m, ev.limit_m, 1)} "
                        f"away against a {ev.limit_m:g} m limit — the assignment stands, "
                        f"the distance does not")
                if not _criticality_ok(is_critical, ner):
                    reasons.append("and it is not critical-rated for a space "
                                   "marked critical")
                if respect_capacity and ner.capacity_known:
                    remaining = (ner.total_ports - ner.used_ports - ner.reserved_ports
                                 - load[ner.key]["assigned_ports"])
                    if remaining < ports:
                        reasons.append(
                            f"and it has {free_ports_text(remaining, 'port(s) free')} "
                            f"against {ports:.0f} required — over capacity")
                chosen = pin
            elif awaiting_declaration is not None:
                room = awaiting_declaration
                reasons.append(
                    f"you assigned {room.label}, which is on {page_label(room.point.page)} "
                    f"while this space is on {page_label(area.page)}, but 'other level' is "
                    f"unticked for this space and no vertical route is registered "
                    f"between the two pages — so {room.label} is not counted. Tick "
                    f"'other level' to count it from there, or choose a room on "
                    f"{page_label(area.page)}; until then its {ports:.0f} port(s) are not "
                    f"counted against any room")
            else:
                # The room is gone, rejected, or not measurable from here. Say
                # so rather than quietly falling back to the nearest one.
                reasons.append(f"the room {'the TEAP names' if who == 'the TEAP' else 'you assigned'} "
                               f"({pin_key}) is no longer available to this space — "
                               f"the nearest compatible room was used instead")

        if awaiting_declaration is not None:
            # Not the nearest room instead: he chose one, and the tool has no
            # business substituting another while the question is open.
            nearest = scored[0] if scored else None
            assignments.append(Assignment(
                space_key=area.key, space_name=area.name, area=area,
                demand_type=demand_type, ports_required=ports,
                ner_key=None, ner_label=None, ner_critical=None,
                evaluation=None, status=Status.NOT_ANALYSABLE,
                match_confirmed=match_confirmed, reasons=reasons,
                alternatives=[(n.key, d) for d, n, _ in scored[:3]],
                nearest_label=nearest[1].label if nearest else None,
                nearest_distance_m=nearest[2].effective_distance_m if nearest else None,
            ))
            uncovered.append(area.key)
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": Status.NOT_ANALYSABLE.value, "page": area.page,
                "detail": reasons[-1],
            })
            continue

        for dist, ner, ev in scored:
            if chosen is not None:
                break
            if not _criticality_ok(is_critical, ner):
                rejected_for_criticality = True
                continue
            if respect_capacity and ner.capacity_known:
                remaining = (ner.total_ports - ner.used_ports - ner.reserved_ports
                             - load[ner.key]["assigned_ports"])
                if remaining < ports:
                    rejected_for_capacity = True
                    continue
            if not ev.compliant:
                continue
            chosen = (dist, ner, ev)
            break

        # Every room within the limit is out of ports. The rule Marcelo set on
        # 3 Sep 2026: take the nearest one anyway and say so. Leaving the
        # space with no room — what this did before — hid the demand from the
        # port load of the very room that will end up carrying it, and the
        # report is where he decides whether to draw a second NER there. The
        # overload is not softened: the status is *insufficient capacity*, the
        # row is an exception, and the utilisation goes past 100 %.
        #
        # Criticality is not relaxed the same way. When the project enforces
        # it, a critical space still never falls back onto a non-critical
        # room; that is a design rule, not a shortage.
        #
        # "Same floor first" holds here too (5.5.1-rc5, tester R4-3): when a
        # NER on another level was compared, the nearest NER on the space's
        # own level within the limit is taken if there is one, full like the
        # others; only otherwise the nearest one overall. rc4 took a full NER
        # upstairs 50 m away over a full one 14.9 m away on the floor, and
        # called it the nearest.
        within = [t for t in considered
                  if t[2].compliant and _criticality_ok(is_critical, t[1])]
        if chosen is None and scored and within:
            if within:
                on_floor = [t for t in within if t[1].key not in cross_routes]
                dist, ner, ev = on_floor[0] if (cross_routes and on_floor) else within[0]
                nearest_one = (f"the nearest one on this level, {ner.label},"
                               if cross_routes and ner.key not in cross_routes
                               else f"the nearest one, {ner.label},")
                # The sentence is written only when it is true, on every
                # project (5.6.0, tester R4-4; R3-1 on projects with shafts):
                # a NER within the limit with free ports that the route rule
                # left out of the comparison, or one that is not
                # critical-rated for this critical space, is named, and the
                # sentence says what "every" covers.
                def _has_room(n: NER) -> bool:
                    return not (respect_capacity and n.capacity_known
                                and n.total_ports - n.used_ports - n.reserved_ports
                                - load[n.key]["assigned_ports"] < ports)

                free_aside = [n.label for _d, n, e in set_aside
                              if e.compliant and _criticality_ok(is_critical, n)
                              and _has_room(n)]
                free_noncrit = [n.label for _d, n, e in sorted(
                                    considered + set_aside, key=lambda t: (t[0], t[1].key))
                                if e.compliant and not _criticality_ok(is_critical, n)
                                and _has_room(n)]
                extra = []
                if free_aside:
                    extra.append(f"{', '.join(free_aside[:3])}, within the limit with free "
                                 f"ports, was not compared: see above")
                if free_noncrit:
                    extra.append(f"{', '.join(free_noncrit[:3])}, within the limit with free "
                                 f"ports, is not critical-rated")
                reasons.append(
                    f"every {'critical-rated ' if free_noncrit else ''}NER "
                    f"{'compared ' if free_aside else ''}within the distance limit is out "
                    f"of ports — assigned to {nearest_one} anyway; the overload is "
                    f"reported, not hidden" + (f" ({'; '.join(extra)})" if extra else ""))
                chosen = (dist, ner, ev)

        # A critical space whose only NERs within the limit are not
        # critical-rated (5.6.0). It came back with no NER and the status of
        # the nearest NER - *served* - and was counted as served. It is not:
        # the row offers both ways out, the nearest critical NER (with its
        # distance, beyond the limit or not) and the nearest NER within the
        # limit, and a choice in Served by settles it. A critical NER within
        # the limit on a sheet whose scale is not confirmed still answers
        # *scale not confirmed* (below): it would serve once the scale is.
        crit_refused = [t for t in considered
                        if t[2].compliant and not _criticality_ok(is_critical, t[1])]
        provisional = [t for t in considered if _criticality_ok(is_critical, t[1])
                       and t[2].status is Status.SCALE_NOT_CONFIRMED
                       and t[2].effective_distance_m is not None
                       and t[2].effective_distance_m <= t[2].limit_m]
        if chosen is None and scored and crit_refused and not provisional:
            nd, nn, ne = crit_refused[0]
            aside_keys = {t[1].key for t in set_aside}
            crit = sorted([t for t in considered + set_aside
                           if _criticality_ok(is_critical, t[1])],
                          key=lambda t: (t[0], t[1].key))
            limit = ne.limit_m

            def _how(e: DistanceEvaluation) -> str:
                return ("by the traced route" if e.method is DistanceMethod.ROUTE
                        else "in a straight line")

            if crit:
                cd, cn, ce = crit[0]
                ceff = ce.effective_distance_m
                cfig = f"{figure_vs_limit(ceff, limit, 1)} {_how(ce)}"
                if ceff > limit:
                    cfig += f", over the {limit:g} m limit"
                elif not ce.compliant:
                    cfig += f": {ce.status.value.replace('_', ' ')}"
                if cn.key in aside_keys:
                    cfig += " (not compared: no traced route to it)"
                cfig += _out_of_ports(cn)
                first = f"the nearest critical NER is {cn.label} at {cfig}"
                choose = "choose one of the two in Served by"
                report = crit[0]
            else:
                first = "no critical NER is measurable from this space"
                choose = "choose the NER in Served by"
                report = crit_refused[0]
            sentence = (f"no critical NER within the {limit:g} m limit for a space "
                        f"marked critical: {first}; the nearest NER within the limit is "
                        f"{nn.label} at {figure_vs_limit(ne.effective_distance_m, limit, 1)} "
                        f"{_how(ne)}, not critical-rated{_out_of_ports(nn)} - {choose}")
            rd, rn, re_ = report
            alternatives = sorted({(t[1].key, t[0]) for t in (crit[:1] + crit_refused[:1])},
                                  key=lambda kv: (kv[1], kv[0]))
            status = Status.NO_CRITICAL_NER_IN_LIMIT
            assignments.append(Assignment(
                space_key=area.key, space_name=area.name, area=area,
                demand_type=demand_type, ports_required=ports,
                ner_key=None, ner_label=None, ner_critical=None,
                # The NER reported, and a person still has to decide.
                evaluation=replace(re_, requires_human_validation=True),
                status=status, match_confirmed=match_confirmed,
                reasons=reasons + [sentence],
                alternatives=alternatives,
                nearest_label=rn.label, nearest_distance_m=re_.effective_distance_m,
                measured_page=(via[rn.key].page if via.get(rn.key, area) is not area
                               else None),
            ))
            uncovered.append(area.key)
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": status.value, "page": area.page, "detail": sentence,
            })
            continue

        if chosen is None and scored:
            # nothing compliant: report the nearest option and why it fails.
            # The nearest of every NER compared, on this level or another, by
            # the measure used (5.5.1-rc5, tester R4-8: rc4 named the floor
            # NER at 95.00 m and then "nearest NER is UP at 120.0 m").
            # A floor NER refused only for its criticality is within the
            # limit: it is named above with its reason and kept among the
            # alternatives, but its result is no reason the space is not
            # served, so it is not the one reported (rc4 reported the NER the
            # rules left for this too). Without a NER reached through a
            # shaft or riser ``pool`` is ``scored``.
            # Never a compliant NER (5.6.0): one left here was refused for
            # its criticality while a critical NER waits on its scale, and
            # its status - served - is no reason the space is not served.
            pool = [t for t in considered if not t[2].compliant] or \
                [t for t in considered if t[1].key in _left]
            dist, ner, ev = pool[0]
            if any(t[2].compliant and (t[0], t[1].key) < (dist, ner.key)
                   for t, _w in floor_failed):
                rejected_for_criticality = True
            status = ev.status
            alternatives = [(n.key, d) for d, n, _ in considered[:3]]
            if floor_failed and all(k != floor_failed[0][0][1].key for k, _ in alternatives):
                # The nearest NER on the space's own level stays in sight
                # (R3-9); the row names it, with its reason, above.
                fd, fn, _fe = floor_failed[0][0]
                alternatives = sorted(alternatives[:2] + [(fn.key, fd)],
                                      key=lambda kv: (kv[1], kv[0]))
            if rejected_for_criticality:
                reasons.append("nearer NERs were skipped because they are not critical-rated")
            assignments.append(Assignment(
                space_key=area.key, space_name=area.name, area=area,
                demand_type=demand_type, ports_required=ports,
                ner_key=None, ner_label=None, ner_critical=None,
                evaluation=ev, status=status, match_confirmed=match_confirmed,
                reasons=reasons + [f"nearest NER is {ner.label} at "
                                   f"{figure_vs_limit(ev.effective_distance_m, ev.limit_m, 1)} "
                                   f"(limit {ev.limit_m:g} m)"],
                alternatives=alternatives,
                nearest_label=ner.label, nearest_distance_m=ev.effective_distance_m,
                measured_page=(via[ner.key].page if via.get(ner.key, area) is not area
                               else None),
            ))
            uncovered.append(area.key)
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": status.value, "page": area.page,
                "detail": reasons[-1] if reasons else ev.summary(),
            })
            continue

        if chosen is None:
            last, detail = _unmeasured(area, serving, scale_for_page, has_verticals)
            if last is None:
                last = ("no NER is measurable from this space (no NER on this level, and "
                        "the vertical route(s) registered do not count - see above)"
                        if cross_notes else
                        "no NER is measurable from this space "
                        "(no NER on this level and no vertical route registered)")
                detail = ("no NER on this level; the registered shaft/riser route "
                          "does not count (mostly straight lead-in)" if cross_notes
                          else "no NER on this level and no registered shaft/riser")
            assignments.append(Assignment(
                space_key=area.key, space_name=area.name, area=area,
                demand_type=demand_type, ports_required=ports,
                ner_key=None, ner_label=None, ner_critical=None,
                evaluation=None, status=Status.NOT_ANALYSABLE,
                match_confirmed=match_confirmed,
                reasons=_wireless_notes(area)
                + [cross_notes[k] for k in _cross_order(cross_notes)] + [last],
            ))
            uncovered.append(area.key)
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": Status.NOT_ANALYSABLE.value, "page": area.page,
                "detail": detail,
            })
            continue

        dist, ner, ev = chosen
        was_pinned = bool(pin_key) and ner.key == pin_key

        # A room whose ports are exhausted must come back as exactly that,
        # whether a person pinned it or the capacity fallback above picked
        # it. ``ev.status`` only knows about distance, and the loop that
        # normally catches capacity was skipped for this space.
        status = ev.status
        if respect_capacity and ner.capacity_known:
            free = (ner.total_ports - ner.used_ports - ner.reserved_ports
                    - load[ner.key]["assigned_ports"])
            if free < ports and status is not Status.OUT_OF_LIMIT:
                status = Status.INSUFFICIENT_CAPACITY

        if teap_critical is None:
            reasons.append("criticality not stated in the TEAP — nearest compatible NER "
                           "proposed, human validation required")
        if not ner.capacity_known:
            reasons.append("capacity unknown for this NER")
        if ev.method is DistanceMethod.STRAIGHT_LINE and ev.effective_distance_m is not None:
            reasons.append("straight-line screening distance; validate against the cable route")

        used = via.get(ner.key, area)
        if used is not area:
            if ner.point_on(used.page) is None and ner.key in cross_routes:
                # Reached from that page through a shaft or riser, not on it
                # (5.5.1-rc5, tester R4-10).
                cr, cpage = cross_routes[ner.key]
                links = ", ".join(str(v) for v in cr.get("vertical_links") or [])
                where = (f"{ner.label}, on {page_label(cpage)}, is reached from that page "
                         f"through a shaft or riser" + (f" ({links})" if links else ""))
            else:
                where = f"{ner.label} is on that page"
            reasons.append(
                f"measured from {page_label(used.page)}, where the same space is also "
                f"labelled ({used.meta.get('link_basis') or 'confirmed as the same space'}) "
                f"— {where}; the TEAP line itself is tied to "
                f"{page_label(area.page)}")
            if used.estimated:
                reasons.append("the label on that page may sit at the edge of the sheet: "
                               "the distance is an estimate from the label position")

        # The criticality alert (5.6.0, owner's decision of 6 Nov 2026: "only
        # alert"). A space marked critical on a NER that is not critical-rated,
        # which nobody chose for it, keeps that NER and that status - it is
        # served - and says so: the nearest critical NER is the suggestion,
        # with its distance even beyond the limit. A critical NER within the
        # limit in a straight line that a traced route left out of the
        # comparison counts as within the limit here (tester T1-4).
        alternatives = [(n.key, d) for d, n, _ in considered if n.key != ner.key][:3]
        alert = suggestion = None
        if teap_critical is True and not ner.critical and not was_pinned \
                and area.meta.get("kind") != "wireless_point":
            crit_pool: dict[str, tuple[float, NER, DistanceEvaluation]] = {}
            for t in considered + set_aside + cross_aside:
                if t[1].critical and t[2].effective_distance_m is not None and \
                        (t[1].key not in crit_pool or t[0] < crit_pool[t[1].key][0]):
                    crit_pool[t[1].key] = t
            near = min(crit_pool.values(), key=lambda t: (t[0], t[1].key), default=None)
            alert = ALERT_NO_CRITICAL_NER_IN_LIMIT
            head = (f"{ALERT_LABEL[ALERT_NO_CRITICAL_NER_IN_LIMIT]}: marked critical, served "
                    f"by {ner.label}, not critical-rated; ")
            if near is None:
                tail = (f"no critical NER is measurable from this space - choosing "
                        f"{ner.label} in Served by settles it")
            else:
                cd, cn, ce = near
                within = cd <= ce.limit_m
                if within:
                    alert = ALERT_CRITICAL_NER_IN_LIMIT
                    head = (f"{ALERT_LABEL[ALERT_CRITICAL_NER_IN_LIMIT]}: marked critical, "
                            f"served by {ner.label}, not critical-rated; ")
                how = ("by the traced route" if ce.method is DistanceMethod.ROUTE
                       else "in a straight line")
                full = _out_of_ports(cn)
                tail = (f"the nearest critical NER is {cn.label} at "
                        f"{figure_vs_limit(cd, ce.limit_m, 1)} {how}, "
                        + (f"within the {ce.limit_m:g} m limit" if within
                           else f"over the {ce.limit_m:g} m limit")
                        + full + " - choosing one in Served by settles it")
                free = None
                if cn.capacity_known:
                    free = (cn.total_ports - cn.used_ports - cn.reserved_ports
                            - load[cn.key]["assigned_ports"])
                suggestion = {"ner_key": cn.key, "ner_label": cn.label,
                              "distance_m": round(cd, 2), "method": ce.method.value,
                              "within_limit": within, "full": bool(full),
                              "free_ports": None if free is None else max(0.0, free)}
                if all(k != cn.key for k, _ in alternatives):
                    alternatives = sorted(alternatives + [(cn.key, cd)],
                                          key=lambda kv: (kv[1], kv[0]))
            reasons.append(head + tail)
            alerts.append({"space_key": area.key, "space_name": area.name,
                           "page": area.page, "alert": alert, "ner_label": ner.label,
                           "detail": head + tail})

        load[ner.key]["assigned_ports"] += ports
        if area.meta.get("kind") == "wireless_point":
            load[ner.key]["wireless_points"] += 1
        else:
            load[ner.key]["spaces"].append(area.key)
            load[ner.key]["wireless_ports"] += float(area.meta.get("teap_wireless_ports") or 0)
        assignments.append(Assignment(
            space_key=area.key, space_name=area.name, area=area,
            demand_type=demand_type, ports_required=ports,
            ner_key=ner.key, ner_label=ner.label, ner_critical=ner.critical,
            evaluation=ev, status=status, match_confirmed=match_confirmed,
            reasons=reasons, pinned=was_pinned,
            # The next rooms by distance, the serving one left out — which is
            # not always ``scored[0]``: a pin or a full room moves it.
            alternatives=alternatives,
            ner_page=(ner.point_on(used.page) or ner.point).page,
            cross_level=(ner.point_on(used.page) is None),
            measured_page=(used.page if used is not area else None),
            kind=str(area.meta.get("kind") or "space"),
            space_critical=teap_critical, criticality_alert=alert,
            critical_suggestion=suggestion,
        ))
        # A room that does not comply is an exception like any other, whether
        # a person chose it or the capacity fallback did. Hiding it because
        # somebody chose it would be the one thing this feature must never do.
        if status in (Status.OUT_OF_LIMIT, Status.INSUFFICIENT_CAPACITY):
            who = ("by the TEAP" if was_pinned and pin_sources.get(area.key) == "the TEAP"
                   else "as its TEAP space" if was_pinned and pin_sources.get(area.key) == "the space"
                   else "by you" if was_pinned
                   else "as the nearest room, although it is full")
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": status.value, "page": area.page,
                "detail": f"assigned to {ner.label} {who}; " + (
                    f"{figure_vs_limit(ev.effective_distance_m, ev.limit_m, 1)} of "
                    f"{ev.limit_m:g} m"
                    if status is Status.OUT_OF_LIMIT else "the room is out of ports"),
            })
            # Not served, whoever chose the room: "not covered" on the summary
            # has to count it, or a full room could be hidden behind a pin.
            uncovered.append(area.key)
        if status is Status.CROSS_LEVEL_NOT_MEASURED:
            # Served, counted, and still on the to-do list: the vertical route
            # is what turns "not measured" into a distance.
            who = ("by the TEAP" if pin_sources.get(area.key) == "the TEAP"
                   else "as its TEAP space" if pin_sources.get(area.key) == "the space"
                   else "by you")
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": status.value, "page": area.page,
                "detail": f"assigned to {ner.label} {who} from {page_label(ner.point.page)} "
                          f"(another level); distance not measured — " + (
                              "the registered vertical route does not count (mostly "
                              "straight lead-in)" if ner.key in ignored_cross else
                              "register the shaft, riser or vertical route to measure it"),
            })
        if status is Status.NEAR_LIMIT:
            exceptions.append({
                "space_key": area.key, "space_name": area.name,
                "status": Status.NEAR_LIMIT.value, "page": area.page,
                "detail": f"{figure_vs_limit(ev.effective_distance_m, ev.limit_m, 1)} of "
                          f"{ev.limit_m:g} m "
                          f"({figure_vs_limit(ev.margin_m, 0.0, 1)} margin)",
            })

    # Asked only where the answer would change the row: a space nothing
    # reaches. A row waiting on a match or a scale confirmation is waiting on
    # that, and a second question there is noise.
    ask_when = (Status.NOT_ANALYSABLE, Status.OUT_OF_LIMIT)
    ignored_links = list(network.ignored_verticals) if network is not None else []
    notes = [f"vertical link {key} has both feet on {page_label(page)} and is ignored: "
             f"a shaft or riser joins two levels - delete it and register it between "
             f"the two sheets it joins" for key, page, *_ in ignored_links]
    for a in assignments:
        a.kind = str(a.area.meta.get("kind") or "space")
        a.space_critical = crit_of.get(a.space_key)
        for key, page, lower, upper, _why in ignored_links:
            # Said on the rows it could have changed: a space within the
            # route join reach of either foot (R7-F5).
            for occ in (a.area, *a.area.occurrences):
                sc = scale_for_page.get(occ.page)
                if occ.page != page or sc is None or a.unplaced:
                    continue
                reach = max(60.0, sc.to_pdf_units(15.0))
                p0 = occ.representative_point()
                if min(p0.distance_units(lower), p0.distance_units(upper)) <= reach:
                    a.reasons.append(
                        f"the vertical link {key} near this space has both feet on "
                        f"{page_label(page)}, so the analysis ignored it - a shaft or "
                        f"riser joins two levels")
                    break
        if a.status not in ask_when or a.ner_key is not None:
            continue
        a.occurrence_hint = _occurrence_hint(a.area, a.demand_type, serving,
                                             scale_for_page, rules)
        if a.occurrence_hint:
            h = a.occurrence_hint
            a.reasons.append(
                f"the same {'code' if h['method'] == 'exact_code' else 'name'} is also "
                f"on {page_label(h['page'])}, where {h['ner_label']} is {h['distance_m']:.1f} m "
                f"away — confirm on this row that it is the same space and the next "
                f"run measures from there")

    for key, info in load.items():
        if info["total_ports"]:
            info["utilisation_pct"] = round(
                100.0 * info["assigned_ports"] / info["total_ports"], 1)
        else:
            info["capacity_note"] = "Capacity unknown."

    return CoverageResult(assignments=assignments, ner_load=load,
                          exceptions=exceptions, uncovered=uncovered, notes=notes,
                          alerts=alerts)


def redundancy_candidates(result: CoverageResult, ners: Sequence[NER],
                          rules: DistanceRules) -> list[dict]:
    """NERs that carry no load, phrased strictly as spatial-redundancy candidates.

    Never a recommendation to remove: capacity, power, cooling, security and
    operational continuity are not modelled from the drawings.
    """
    out: list[dict] = []
    by_key = {n.key: n for n in ners}
    for key, info in result.ner_load.items():
        ner = by_key.get(key)
        if ner is None or ner.proposed:
            continue
        if info["spaces"] or info.get("wireless_points"):
            continue
        blockers: list[str] = []
        if ner.locked:
            blockers.append("element is locked by the user")
        if not ner.removable:
            blockers.append("flagged as not removable")
        if not ner.capacity_known:
            blockers.append("capacity unknown")
        if ner.critical:
            blockers.append("critical-rated room — redundancy must be preserved")
        out.append({
            "ner_key": key,
            "label": info["label"],
            "page": info["page"],
            "classification": "Spatial redundancy candidate — capacity and technical "
                              "requirements still to be validated.",
            "blockers": blockers,
            "eligible_for_removal_analysis": not blockers,
        })
    return out
