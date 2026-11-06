"""Distance rules and compliance evaluation.

This is the regulatory core of the application. Two things matter here:

1. **It is deterministic.** No language model participates in any calculation.
2. **It is auditable.** Every evaluation carries the scale used, the method, the
   rule applied, the assumptions made and whether a human still has to validate
   the result.

Engineering note on the 90 m limit
----------------------------------
ANSI/TIA-568 and ISO/IEC 11801 define a 90 m *permanent link* — the fixed
horizontal cable between the equipment room patch panel and the outlet — inside a
100 m *channel* that reserves the remaining 10 m for patch cords at both ends.
90 m is therefore **cable length along its real route**, not a straight line on a
plan. A straight-line measurement systematically under-estimates the installed
length because cable rises into a shaft, runs in tray around structure and drops
back down.

The application models this explicitly with ``routing_slack_factor``. It defaults
to ``1.0`` so that a straight-line study reproduces the raw geometric result the
specification asks for, but a project should raise it (1.25–1.40 is typical for
a stadium/arena bowl) before any result is treated as a design decision. The
evaluation always reports both the raw geometric distance and the effective
distance the verdict was based on.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from .geometry import Area, Point
from .units import Scale, format_length, metres_to

STRAIGHT_LINE_WARNING = (
    "Preliminary straight-line distance. The result must be validated against the "
    "real cable route."
)


def figure_vs_limit(value: float, limit: float, decimals: int = 2, unit: str = "m") -> str:
    """A figure the notes compare with a limit, never printed onto it.

    The rule (5.5.1-rc5, tester R4-7): the figure is printed with the
    sentence's own precision (``decimals``); when that would print the limit
    itself, or the wrong side of it, it gets as many more decimals as it
    takes, up to six - 90.004 m over a 90 m limit prints "90.004 m", never
    "90.00 m"; 89.996 m prints "89.996 m", never "90.00 m". A figure that is
    still on the limit at six decimals reads "just over 90 m" / "just under
    90 m". A figure exactly on the limit prints as it is.
    """
    if value == limit:
        return f"{value:.{decimals}f} {unit}"
    for d in range(decimals, 7):
        s = f"{value:.{d}f}"
        f = float(s)
        if (f > limit) if value > limit else (f < limit):
            return f"{s} {unit}"
    return f"just {'over' if value > limit else 'under'} {limit:g} {unit}"


class DemandType(str, Enum):
    GENERAL = "general"
    PHOTOGRAPHER = "photographer"


class DistanceMethod(str, Enum):
    STRAIGHT_LINE = "straight_line"
    ROUTE = "route"


class Status(str, Enum):
    SERVED = "served"
    NEAR_LIMIT = "near_limit"
    OUT_OF_LIMIT = "out_of_limit"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"
    SCALE_NOT_CONFIRMED = "scale_not_confirmed"
    ROUTE_NOT_VALIDATED = "route_not_validated"
    MATCH_NOT_CONFIRMED = "match_not_confirmed"
    NOT_ANALYSABLE = "not_analysable"
    #: A room a person (or the TEAP) chose for a space on another page, with
    #: no shaft, riser or vertical route registered between the two. The
    #: assignment stands and the ports count against the room; the distance
    #: was not measured, and the row says so instead of pretending to a
    #: number. Added 10 Sep 2026 — see ``evaluate_cross_level``.
    CROSS_LEVEL_NOT_MEASURED = "cross_level_not_measured"
    #: A room a person chose for a TEAP space that has **no room on the
    #: drawing** — the space was not found on any sheet, and the person tied
    #: it to a NER from their own knowledge of the venue (Marcelo, 22 Sep 2026:
    #: *"mesmo que eu não ache um lugar no desenho eu tenho que amarrar com uma
    #: NER existente ou criada"*). The assignment stands and the ports count
    #: against the room; there is no position to measure from, so no distance
    #: is invented. Added in 2.7.0.
    ASSIGNED_NOT_PLACED = "assigned_not_placed"
    #: A space the TEAP marks critical whose only NERs within the limit are
    #: non-critical (5.6.0). It used to come back with no NER and the status
    #: of the nearest non-critical NER - *served* - and was counted as
    #: served. It is not served: the row offers the nearest critical NER
    #: (with its distance, beyond the limit or not) and the nearest
    #: non-critical NER within the limit, and a choice in *Served by*
    #: settles it.
    NO_CRITICAL_NER_IN_LIMIT = "no_critical_ner_in_limit"


class Group(str, Enum):
    """The four states a TEAP space can be in, as a person reads the list.

    The engine's :class:`Status` is precise — *match not confirmed*, *scale not
    confirmed*, *not analysable* — and every one of those values is a real,
    different reason. But from the desk they are one situation (Marcelo,
    22 Sep 2026: *"os que não foram cruzados caem todos no mesmo grupo, no que
    precisa ser cruzado com algum espaço ou marcado"*): the space is not
    resolved yet, and the person has to give it a room on the drawing or a
    NER. So the screens and the workbook show the **group**, and keep the
    precise status beside it as the reason. Nothing is lost; the list reads
    as four piles instead of ten.
    """

    #: Measured, within the limit (near the limit included).
    SERVED = "served"
    #: Measured or counted, and failing: out of limit, or the room is full.
    PROBLEM = "problem"
    #: A room was chosen and its ports are counted, but no distance could be
    #: measured — another level with no vertical route, or no room on the
    #: drawing at all.
    NOT_MEASURED = "not_measured"
    #: Not resolved: needs a room on the drawing or a NER.
    PENDING = "pending"


GROUP_OF: dict[Status, Group] = {
    Status.SERVED: Group.SERVED,
    Status.NEAR_LIMIT: Group.SERVED,
    Status.OUT_OF_LIMIT: Group.PROBLEM,
    Status.INSUFFICIENT_CAPACITY: Group.PROBLEM,
    Status.CROSS_LEVEL_NOT_MEASURED: Group.NOT_MEASURED,
    Status.ASSIGNED_NOT_PLACED: Group.NOT_MEASURED,
    Status.MATCH_NOT_CONFIRMED: Group.PENDING,
    Status.SCALE_NOT_CONFIRMED: Group.PENDING,
    Status.ROUTE_NOT_VALIDATED: Group.PENDING,
    Status.NOT_ANALYSABLE: Group.PENDING,
    # Not resolved: a person chooses between the critical NER beyond the
    # limit and the non-critical one within it, in Served by (5.6.0).
    Status.NO_CRITICAL_NER_IN_LIMIT: Group.PENDING,
}

GROUP_LABEL: dict[Group, str] = {
    Group.SERVED: "Served",
    Group.PROBLEM: "Out of limit / over capacity",
    Group.NOT_MEASURED: "Served — not measured",
    Group.PENDING: "Pending — needs a room or a NER",
}

#: The precise reason, in words, for the line under the group.
STATUS_LABEL: dict[Status, str] = {
    Status.SERVED: "within the limit",
    Status.NEAR_LIMIT: "near the limit",
    Status.OUT_OF_LIMIT: "out of limit",
    Status.INSUFFICIENT_CAPACITY: "insufficient capacity",
    Status.CROSS_LEVEL_NOT_MEASURED: "other level — not measured",
    Status.ASSIGNED_NOT_PLACED: "no room on the drawing — NER chosen by you",
    Status.MATCH_NOT_CONFIRMED: "match not confirmed",
    Status.SCALE_NOT_CONFIRMED: "scale not confirmed",
    Status.ROUTE_NOT_VALIDATED: "route not validated",
    Status.NOT_ANALYSABLE: "no NER reaches it",
    Status.NO_CRITICAL_NER_IN_LIMIT: "no critical NER in limit",
}

#: The criticality alert (5.6.0, owner's decision of 6 Nov 2026: "only
#: alert"). A space marked critical served by a NER that is not
#: critical-rated keeps that NER and its status - the serving NER is the
#: nearest one, as for every space - and the row carries one of these two
#: alerts instead. Never a status: the space still counts as served.
ALERT_NO_CRITICAL_NER_IN_LIMIT = "no_critical_ner_in_limit"
ALERT_CRITICAL_NER_IN_LIMIT = "critical_ner_in_limit"
#: The alert in words: the workbook's *Criticality alert* column, the CSV and
#: the screens (the interface translates them; PT "Sem NER crítica no
#: limite" / "NER crítica no limite").
ALERT_LABEL: dict[str, str] = {
    ALERT_NO_CRITICAL_NER_IN_LIMIT: "No critical NER in limit",
    ALERT_CRITICAL_NER_IN_LIMIT: "Critical NER in limit",
}

#: The reason a space that never reached the engine is pending.
PENDING_NO_ROOM = "no room on the drawing"
PENDING_NOT_ANALYSED = "not analysed yet"


def group_of(status: Optional[str]) -> Group:
    """The group for a status value — or, with no status at all (the space
    never reached the engine), :attr:`Group.PENDING`."""
    if not status:
        return Group.PENDING
    try:
        return GROUP_OF[Status(status)]
    except (ValueError, KeyError):
        return Group.PENDING


CROSS_LEVEL_WARNING = (
    "The space and its serving NER are on different drawing pages/levels and no "
    "shaft, riser or vertical route is registered between them: the distance was "
    "not measured. Register the vertical route to measure it."
)


@dataclass(frozen=True)
class DistanceRules:
    """Per-project, editable distance policy.

    ``routing_slack_factor`` multiplies a straight-line measurement to estimate the
    installed cable length. It is ignored when a real route is used, because the
    route length is already the installed length.
    """

    general_limit_m: float = 90.0
    photographer_limit_m: float = 70.0
    near_limit_ratio: float = 0.90
    routing_slack_factor: float = 1.0
    allow_cross_floor_without_route: bool = False

    def limit_for(self, demand: DemandType) -> float:
        return (self.photographer_limit_m if demand is DemandType.PHOTOGRAPHER
                else self.general_limit_m)

    def near_limit_threshold(self, demand: DemandType) -> float:
        return self.limit_for(demand) * self.near_limit_ratio

    def __post_init__(self) -> None:
        if self.general_limit_m <= 0 or self.photographer_limit_m <= 0:
            raise ValueError("distance limits must be positive")
        if not 0 < self.near_limit_ratio <= 1:
            raise ValueError("near_limit_ratio must be in (0, 1]")
        if self.routing_slack_factor < 1.0:
            raise ValueError("routing_slack_factor must be >= 1.0")


@dataclass
class DistanceEvaluation:
    """A single audited distance verdict."""

    status: Status
    demand_type: DemandType
    method: DistanceMethod
    limit_m: float
    geometric_distance_m: Optional[float]
    effective_distance_m: Optional[float]
    margin_m: Optional[float]
    scale_label: Optional[str]
    scale_confirmed: bool
    routing_slack_factor: float
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_human_validation: bool = True
    farthest_point_distance_m: Optional[float] = None
    detail: dict = field(default_factory=dict)

    @property
    def compliant(self) -> bool:
        return self.status in (Status.SERVED, Status.NEAR_LIMIT)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["demand_type"] = self.demand_type.value
        d["method"] = self.method.value
        d["compliant"] = self.compliant
        return d

    def summary(self, display: str = "m") -> str:
        if self.effective_distance_m is None:
            return f"{self.status.value} (no distance available)"
        # Never printed onto the limit (R4-7): "90.04 m of 90 m", not
        # "90.0 m of 90 m"; the one-decimal figure otherwise, as before.
        v, lim = self.effective_distance_m, self.limit_m
        m = figure_vs_limit(v, lim, 1)
        ft = figure_vs_limit(metres_to(v, "ft"), metres_to(lim, "ft"), 1, "ft")
        shown = {"m": m, "ft": ft, "both": f"{m} ({ft})"}.get(display)
        if shown is None:
            format_length(v, display)        # the same error for a bad mode
        return f"{self.status.value}: {shown} of {format_length(lim, display)}"


def evaluate_distance(
    *,
    distance_units: Optional[float],
    scale: Optional[Scale],
    rules: DistanceRules,
    demand_type: DemandType = DemandType.GENERAL,
    method: DistanceMethod = DistanceMethod.STRAIGHT_LINE,
    route_length_m: Optional[float] = None,
    farthest_vertex_units: Optional[float] = None,
    match_confirmed: bool = True,
    assumptions: Optional[list[str]] = None,
    farthest_point_m: Optional[float] = None,
    route_scales_confirmed: Optional[bool] = None,
) -> DistanceEvaluation:
    """Evaluate one area/point against its serving NER.

    Exactly one of ``distance_units`` (straight line, in PDF units, converted with
    ``scale``) or ``route_length_m`` (already in metres, from a registered cable
    route) drives the verdict.

    ``farthest_point_m`` is the room polygon's farthest vertex already in
    metres, measured along a route: from that vertex, straight, to where the
    route joins the space's sheet, then along the route - from another level
    and on the space's own sheet alike, on every project (5.6.0).
    ``route_scales_confirmed`` False makes a route verdict wait for the
    scales it was measured with, as a straight line waits for its sheet's: a
    traced route on a sheet whose scale is not confirmed, or a route through
    a shaft or riser with any sheet on it unconfirmed, is never definitive.
    """
    assumptions = list(assumptions or [])
    warnings: list[str] = []
    limit = rules.limit_for(demand_type)

    base = DistanceEvaluation(
        status=Status.NOT_ANALYSABLE,
        demand_type=demand_type,
        method=method,
        limit_m=limit,
        geometric_distance_m=None,
        effective_distance_m=None,
        margin_m=None,
        scale_label=scale.label if scale else None,
        scale_confirmed=bool(scale and scale.confirmed),
        routing_slack_factor=rules.routing_slack_factor,
        assumptions=assumptions,
        warnings=warnings,
    )

    # --- route-based ------------------------------------------------------
    if method is DistanceMethod.ROUTE:
        if route_length_m is None:
            base.status = Status.ROUTE_NOT_VALIDATED
            warnings.append("Route method requested but no route length was supplied.")
            return base
        geometric = route_length_m
        effective = route_length_m
        assumptions.append("Distance measured along a registered cable route.")
    # --- straight line ----------------------------------------------------
    else:
        if distance_units is None:
            base.status = Status.NOT_ANALYSABLE
            warnings.append("No geometry available to measure.")
            return base
        if scale is None:
            base.status = Status.SCALE_NOT_CONFIRMED
            warnings.append("No scale is associated with this page.")
            return base
        geometric = scale.to_metres(distance_units)
        effective = geometric * rules.routing_slack_factor
        warnings.append(STRAIGHT_LINE_WARNING)
        if rules.routing_slack_factor > 1.0:
            assumptions.append(
                f"Straight-line distance multiplied by a routing slack factor of "
                f"{rules.routing_slack_factor:g} to estimate installed cable length."
            )
        else:
            assumptions.append(
                "Routing slack factor is 1.0: the verdict uses the raw straight-line "
                "distance and will under-estimate the installed cable length."
            )

    base.geometric_distance_m = geometric
    base.effective_distance_m = effective
    base.margin_m = limit - effective

    if farthest_vertex_units is not None and scale is not None:
        far = scale.to_metres(farthest_vertex_units) * (
            rules.routing_slack_factor if method is DistanceMethod.STRAIGHT_LINE else 1.0
        )
        base.farthest_point_distance_m = far
        if far > effective:
            assumptions.append(
                "Compliance evaluated against the farthest vertex of the room polygon, "
                "not only its centre."
            )
            effective = far
            base.effective_distance_m = effective
            base.margin_m = limit - effective
    elif farthest_point_m is not None:
        base.farthest_point_distance_m = farthest_point_m
        if farthest_point_m > effective:
            assumptions.append(
                "Compliance evaluated against the farthest vertex of the room polygon, "
                "not only its centre: from that vertex to where the route joins this "
                "sheet, then along the route.")
            effective = farthest_point_m
            base.effective_distance_m = effective
            base.margin_m = limit - effective

    # --- blocking conditions, in priority order ---------------------------
    # An unconfirmed match no longer blocks the verdict (3.0.0). Marcelo,
    # 14 Oct 2026: a TEAP space the matcher placed on a room, with a NER
    # measured on the same sheet, is served by that NER — waiting for a click
    # left real ports out of every NER's load. The row is still marked: the
    # match is unconfirmed, the result asks for validation, and the Demand
    # screen filters on it. ``Status.MATCH_NOT_CONFIRMED`` is kept for runs
    # stored before 3.0.0.
    if not match_confirmed:
        assumptions.append(
            "The room was matched to the TEAP line by the matcher and not yet "
            "confirmed by a person; the verdict is for that room.")

    if method is DistanceMethod.STRAIGHT_LINE and (scale is None or not scale.confirmed):
        base.status = Status.SCALE_NOT_CONFIRMED
        base.requires_human_validation = True
        return base
    if method is DistanceMethod.ROUTE and route_scales_confirmed is False:
        base.status = Status.SCALE_NOT_CONFIRMED
        base.requires_human_validation = True
        warnings.append("The route is measured with a scale that is not confirmed on "
                        "at least one of the sheets it runs on.")
        return base

    # --- verdict ----------------------------------------------------------
    if effective > limit:
        base.status = Status.OUT_OF_LIMIT
    elif effective > rules.near_limit_threshold(demand_type):
        base.status = Status.NEAR_LIMIT
    else:
        base.status = Status.SERVED

    base.requires_human_validation = (method is DistanceMethod.STRAIGHT_LINE
                                      or not match_confirmed)
    return base


def evaluate_area(
    *,
    area: Area,
    ner_point: Point,
    scale: Optional[Scale],
    rules: DistanceRules,
    demand_type: DemandType = DemandType.GENERAL,
    route_length_m: Optional[float] = None,
    match_confirmed: bool = True,
    route_far_m: Optional[float] = None,
    route_scales_confirmed: Optional[bool] = None,
) -> DistanceEvaluation:
    """Convenience wrapper: measure an :class:`Area` against a NER position.

    ``route_far_m`` and ``route_scales_confirmed`` go with every route (see
    :func:`evaluate_distance`; 5.6.0). Given with a route on the same sheet,
    ``route_far_m`` replaces the straight line from the farthest vertex to
    the NER: that line is not the cable that serves the corner."""
    assumptions: list[str] = []
    if area.estimated:
        assumptions.append(
            "Service point estimated at the room label/centroid; not a surveyed outlet "
            "location."
        )

    if area.page != ner_point.page:
        if route_length_m is not None:
            return evaluate_distance(
                distance_units=None, scale=scale, rules=rules, demand_type=demand_type,
                method=DistanceMethod.ROUTE, route_length_m=route_length_m,
                match_confirmed=match_confirmed, assumptions=assumptions,
                farthest_point_m=route_far_m,
                route_scales_confirmed=route_scales_confirmed,
            )
        ev = DistanceEvaluation(
            status=Status.NOT_ANALYSABLE,
            demand_type=demand_type,
            method=DistanceMethod.STRAIGHT_LINE,
            limit_m=rules.limit_for(demand_type),
            geometric_distance_m=None, effective_distance_m=None, margin_m=None,
            scale_label=scale.label if scale else None,
            scale_confirmed=bool(scale and scale.confirmed),
            routing_slack_factor=rules.routing_slack_factor,
            assumptions=assumptions,
            warnings=["The area and the NER are on different drawing pages/levels. "
                      "Cross-floor service requires a registered shaft, riser or "
                      "vertical route."],
        )
        return ev

    rep = area.representative_point()
    d_units = rep.distance_units(ner_point)
    far = area.farthest_vertex_from(ner_point)
    method = DistanceMethod.ROUTE if route_length_m is not None else DistanceMethod.STRAIGHT_LINE
    far_units = far[1] if far else None
    if route_length_m is not None and route_far_m is not None:
        # The farthest vertex along the traced route, as across levels
        # (5.6.0, every project): a straight line from that vertex to the
        # NER is not the cable that serves it.
        far_units = None
    return evaluate_distance(
        distance_units=d_units,
        scale=scale,
        rules=rules,
        demand_type=demand_type,
        method=method,
        route_length_m=route_length_m,
        farthest_vertex_units=far_units,
        match_confirmed=match_confirmed,
        assumptions=assumptions,
        farthest_point_m=route_far_m if route_length_m is not None else None,
        route_scales_confirmed=route_scales_confirmed,
    )


def evaluate_cross_level(
    *,
    area: Area,
    ner_point: Point,
    scale: Optional[Scale],
    rules: DistanceRules,
    demand_type: DemandType = DemandType.GENERAL,
    match_confirmed: bool = True,
) -> DistanceEvaluation:
    """The verdict for a room somebody *chose* on another page, with no route.

    Marcelo, 10 Sep 2026: a space on level 2 served by the NER directly below
    it on level 1 "pode e vai acontecer" — and when he assigns that room, the
    ports have to count against its capacity. What the engine must **not** do
    is invent a distance: two sheets share no coordinate system, and a
    straight line between a point on page 12 and a point on page 13 is a
    number with no meaning. So this evaluation carries the limit, the scale
    and the assumptions like any other, and no distance at all. The status
    says why, and ``requires_human_validation`` stays true: the day the
    vertical route is registered, the analysis measures it and this status
    disappears on its own.

    An unconfirmed match still outranks it, as everywhere else: a room chosen
    for a space that may be the wrong space is the wrong problem to report.
    """
    assumptions: list[str] = []
    if area.estimated:
        assumptions.append(
            "Service point estimated at the room label/centroid; not a surveyed outlet "
            "location."
        )
    ev = DistanceEvaluation(
        status=Status.CROSS_LEVEL_NOT_MEASURED,
        demand_type=demand_type,
        method=DistanceMethod.STRAIGHT_LINE,
        limit_m=rules.limit_for(demand_type),
        geometric_distance_m=None, effective_distance_m=None, margin_m=None,
        scale_label=scale.label if scale else None,
        scale_confirmed=bool(scale and scale.confirmed),
        routing_slack_factor=rules.routing_slack_factor,
        assumptions=assumptions,
        warnings=[CROSS_LEVEL_WARNING],
        requires_human_validation=True,
        detail={"space_page": area.page, "ner_page": ner_point.page},
    )
    return ev
