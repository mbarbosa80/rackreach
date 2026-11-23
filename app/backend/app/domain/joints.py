"""Page joints - the match line between two sheets of one level (5.9.0-rc7).

Marcelo, 22 Nov 2026: on the 400-series detail sheets a level is cut into
sections, one per sheet, and a NER drawn at the top right of sheet 15 can
serve the spaces at the top left of sheet 16, a few metres away across the
cut. The engine measures only within a sheet, so that NER was no candidate
("Served by" counted its ports but left the distance unmeasured). The books
print the cut on both sheets as a match line ("MATCH LINE - SEE SHEET ...");
a person marks it on both, and this module measures across it.

A joint is two segments, the same line as each sheet draws it, end 1 with
end 1 and end 2 with end 2: the point at a fraction ``t`` along one is the
point at ``t`` along the other. The distance from a space on one sheet to a
NER on the other is the shortest of

    |space -> line(t)| (this sheet's scale) + gap + |line(t) -> NER| (the
    other sheet's scale)

over ``t`` in [0, 1]. Each term is a straight line, as within a sheet when no
route is traced; ``gap_m`` is what lies between the two drawn lines in reality
(0 when they are the same line, as a match line is). The sum is convex in
``t``, so a golden-section search finds the shortest. Deterministic.

rc8 (tester T7-9): a level cut into four sheets (2 x 2) is joined by four
lines, and a NER at the inner corner of sheet 1 serves the inner corner of
sheet 4, diagonally across. The path may go through one sheet in between -
two lines, the straight piece across the middle sheet on its own scale. The
sum is jointly convex in the two fractions, so the shortest over the second
for each value of the first is convex in the first: two nested golden-section
searches. Never more than two lines: a level cut into more sheets in a row
is a level for the 900 series.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .geometry import Point
from .units import Scale

#: Golden-section iterations: the bracket shrinks by 0.618 each step, so 80
#: steps take [0, 1] below 1e-16 - far under a millimetre on any sheet.
_STEPS = 80
#: The nested search for a path through a middle sheet: 0.618^36 < 1e-7 of
#: the line's length on each of the two lines - a millimetre on a 10 km line.
_STEPS2 = 36


def joint_order(key: str):
    """How two equally short joints are ordered (rc8, T7-8): by their number,
    J2 before J10, as routes are (R2 before R10)."""
    m = re.search(r"(\d+)", key or "")
    return (int(m.group(1)) if m else 0, key or "")


@dataclass(frozen=True)
class PageJoint:
    """A match line marked on two sheets of the same level."""

    key: str
    a_page: int
    a1: tuple[float, float]
    a2: tuple[float, float]
    b_page: int
    b1: tuple[float, float]
    b2: tuple[float, float]
    gap_m: float = 0.0
    label: str = ""

    def sides(self, page: int, other: int):
        """The joint seen from ``page`` towards ``other``: (here1, here2,
        there1, there2), or None when it does not join the two."""
        if self.a_page == page and self.b_page == other:
            return self.a1, self.a2, self.b1, self.b2
        if self.b_page == page and self.a_page == other:
            return self.b1, self.b2, self.a1, self.a2
        return None


@dataclass
class JointPath:
    """The shortest way across a joint, for the row's notes."""

    joint: PageJoint
    length_m: float
    here_m: float          #: from the space to the line, on its sheet
    there_m: float         #: from the line to the NER, on the NER's sheet
    cross: tuple[float, float]   #: where the line is crossed, on the space's sheet
    t: float
    #: rc8: a path through a sheet in between - the second line, that sheet,
    #: and the straight piece across it (on its scale).
    joint2: Optional[PageJoint] = None
    mid_page: Optional[int] = None
    mid_m: float = 0.0

    @property
    def joints(self) -> tuple:
        return (self.joint,) if self.joint2 is None else (self.joint, self.joint2)

    def order(self):
        return tuple(joint_order(j.key) for j in self.joints)


def _lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _dist(p, q) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _golden(f, steps: int = _STEPS) -> float:
    """The minimum of a convex ``f`` on [0, 1]: where, ends included."""
    lo, hi = 0.0, 1.0
    g = (math.sqrt(5) - 1) / 2
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = f(x1), f(x2)
    for _ in range(steps):
        if f1 <= f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - g * (hi - lo)
            f1 = f(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + g * (hi - lo)
            f2 = f(x2)
    return min(((lo + hi) / 2, 0.0, 1.0), key=f)


def _better(p: JointPath, best: Optional[JointPath]) -> bool:
    return best is None or p.length_m < best.length_m - 1e-9 or (
        abs(p.length_m - best.length_m) <= 1e-9 and p.order() < best.order())


def shortest_across(here: Point, here_scale: Scale, there: Point, there_scale: Scale,
                    joints: Iterable[PageJoint],
                    scales: Optional[Mapping[int, Scale]] = None) -> Optional[JointPath]:
    """The shortest path from ``here`` (a space) to ``there`` (a NER on another
    sheet) through any joint that joins their two sheets, or None. With
    ``scales`` (every sheet's), also through one sheet in between (rc8)."""
    joints = list(joints)
    best = _direct(here, here_scale, there, there_scale, joints)
    if scales:
        for p in _through_one(here, here_scale, there, there_scale, joints, scales):
            if _better(p, best):
                best = p
    return best


def _through_one(here, here_scale, there, there_scale, joints, scales):
    hm, tm = here_scale.metres_per_unit, there_scale.metres_per_unit
    hp, op = (here.x, here.y), (there.x, there.y)
    for j1 in joints:
        mid = j1.b_page if j1.a_page == here.page else (
            j1.a_page if j1.b_page == here.page else None)
        if mid is None or mid in (here.page, there.page):
            continue
        ms = scales.get(mid)
        if ms is None:
            continue
        mm = ms.metres_per_unit
        h1, h2, m1, m2 = j1.sides(here.page, mid)
        for j2 in joints:
            if j2 is j1:
                continue
            s2 = j2.sides(mid, there.page)
            if s2 is None:
                continue
            n1, n2, o1, o2 = s2
            g = float(j1.gap_m or 0.0) + float(j2.gap_m or 0.0)

            def inner(t1: float):
                a, b = _lerp(h1, h2, t1), _lerp(m1, m2, t1)
                head = _dist(hp, a) * hm

                def f2(t2: float) -> float:
                    return _dist(b, _lerp(n1, n2, t2)) * mm + _dist(_lerp(o1, o2, t2), op) * tm
                t2 = _golden(f2, _STEPS2)
                return head + g + f2(t2), t2

            t1 = _golden(lambda t: inner(t)[0], _STEPS2)
            total, t2 = inner(t1)
            cross = _lerp(h1, h2, t1)
            here_m = _dist(hp, cross) * hm
            mid_m = _dist(_lerp(m1, m2, t1), _lerp(n1, n2, t2)) * mm
            there_m = _dist(_lerp(o1, o2, t2), op) * tm
            yield JointPath(joint=j1, length_m=total, here_m=here_m, there_m=there_m,
                            cross=cross, t=t1, joint2=j2, mid_page=mid, mid_m=mid_m)


def _direct(here, here_scale, there, there_scale, joints) -> Optional[JointPath]:
    best: Optional[JointPath] = None
    hm = here_scale.metres_per_unit
    tm = there_scale.metres_per_unit
    for j in joints:
        s = j.sides(here.page, there.page)
        if s is None:
            continue
        h1, h2, o1, o2 = s
        hp, op = (here.x, here.y), (there.x, there.y)

        def f(t: float) -> float:
            return (_dist(hp, _lerp(h1, h2, t)) * hm + float(j.gap_m or 0.0)
                    + _dist(_lerp(o1, o2, t), op) * tm)

        lo, hi = 0.0, 1.0
        g = (math.sqrt(5) - 1) / 2
        x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
        f1, f2 = f(x1), f(x2)
        for _ in range(_STEPS):
            if f1 <= f2:
                hi, x2, f2 = x2, x1, f1
                x1 = hi - g * (hi - lo)
                f1 = f(x1)
            else:
                lo, x1, f1 = x1, x2, f2
                x2 = lo + g * (hi - lo)
                f2 = f(x2)
        t = (lo + hi) / 2
        # The ends themselves, in case the minimum sits on one of them.
        t = min((t, 0.0, 1.0), key=f)
        cross = _lerp(h1, h2, t)
        here_m = _dist(hp, cross) * hm
        there_m = _dist(_lerp(o1, o2, t), op) * tm
        total = here_m + float(j.gap_m or 0.0) + there_m
        p = JointPath(joint=j, length_m=total, here_m=here_m, there_m=there_m,
                      cross=cross, t=t)
        if _better(p, best):
            best = p
    return best


def stand_in(here: Point, path: JointPath, here_scale: Scale) -> Point:
    """A point on the space's own sheet, ``path.length_m`` away from it in a
    straight line through the crossing: the NER as the space sees it across
    the joint. Measuring to it gives the joint's length, and the room's far
    corner is measured towards the line, the way the cable goes."""
    hx, hy = here.x, here.y
    cx, cy = path.cross
    d_units = path.length_m / here_scale.metres_per_unit
    dx, dy = cx - hx, cy - hy
    n = math.hypot(dx, dy)
    if n < 1e-12:
        # The space sits on the line: any direction will do; along the line.
        dx, dy, n = 1.0, 0.0, 1.0
    return Point(hx + dx / n * d_units, hy + dy / n * d_units, here.page)
