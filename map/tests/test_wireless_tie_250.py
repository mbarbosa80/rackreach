"""2.5.0 — a drawn access point stands in for the wireless port the TEAP requests.

On the event's TEAP every space with Wi-Fi carries one ``DWISA`` line — *Wireless
Connection - Shared Internet Access (SIA) Wireless LAN* — with **one network
port**, the access point's uplink (88 of the 110 VAA spaces). A wireless
point drawn in the viewer takes one port on the NER that reaches it. Nothing
tied the two, so a space with five drawn access points put **six** wireless
ports on the switch: five drawn plus the TEAP's one. The LLD is built from
that number (Marcelo, 21 Sep 2026: *"ter uma porta a mais pode ser uma conta
muito alta"*).

The rule now: a wireless point is tied to the TEAP space it stands in for —
proposed as the nearest matched room on its sheet, changed or cleared in the
Inspector — and a space with at least one point tied to it has its TEAP
wireless port taken off; the points carry the wireless on their own NER. With
nothing drawn the TEAP's port stays: the TEAP rules. Indoor and outdoor
access points follow one rule and differ in symbol, numbering and the report.
"""

from __future__ import annotations

import os
import tempfile

import openpyxl
import pytest

TMP = tempfile.mkdtemp(prefix="ner-250-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/t250.sqlite3")

from fastapi.testclient import TestClient                          # noqa: E402
from sqlalchemy import select                                      # noqa: E402

from app import migrate                                            # noqa: E402
from app import services as S                                      # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.main import app                                           # noqa: E402
from app.models import (Drawing, DrawingSpace, Element, Page, Project,  # noqa: E402
                        TeapFile, TeapSpace)

DWISA = "Wireless Connection - Shared Internet Access (SIA) Wireless LAN"


@pytest.fixture(scope="module")
def client():
    init_db()
    c = TestClient(app)
    from app import auth as A
    with SessionLocal() as db:
        try:
            A.create_user(db, username="tester", password="a-long-test-phrase",
                          display_name="Test User", is_admin=True, actor="tests")
        except A.AuthError:
            pass
    assert c.post("/api/auth/login", json={"username": "tester",
                                           "password": "a-long-test-phrase"}).status_code == 200
    return c


def _line(product, qty, ports, code="X", row=1):
    return {"row": row, "product": product, "code": code, "qty": qty, "ports": ports,
            "rule": "A", "explanation": ""}


def _project(name) -> int:
    """One sheet at 1 unit = 1 m."""
    with SessionLocal() as db:
        p = Project(name=name, venue_code="TST")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="d.pdf", stored_path=f"{TMP}/d.pdf",
                    sha256="x" * 64, page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=1000, height=700, metres_per_unit=1.0,
                    scale_label="1u=1m", scale_source="manual", scale_confirmed=True,
                    level="LEVEL 01"))
        db.add(TeapFile(project_id=p.id, filename="teap.xlsx", stored_path=f"{TMP}/t.xlsx",
                        sha256="y" * 64))
        db.commit()
        return p.id


def _room(pid, code, name, x, y) -> int:
    with SessionLocal() as db:
        p = db.get(Project, pid)
        r = DrawingSpace(project_id=pid, drawing_id=p.drawings[0].id, page=1,
                         space_code=code, name=name, x=x, y=y, confidence=0.9)
        db.add(r)
        db.commit()
        return r.id


def _teap(pid, code, name, lines) -> str:
    with SessionLocal() as db:
        tf = db.execute(select(TeapFile).where(TeapFile.project_id == pid)).scalars().first()
        t = TeapSpace(project_id=pid, teap_file_id=tf.id, space_key=f"TST|{code}",
                      space_code=code, space_name=name,
                      total_ports=sum(ln["ports"] for ln in lines), lines=lines,
                      wireless_required=any("Wireless" in ln["product"] for ln in lines))
        db.add(t)
        db.commit()
        return t.space_key


def _ner(pid, label, x, y, *, total_ports=48) -> int:
    with SessionLocal() as db:
        p = db.get(Project, pid)
        e = Element(project_id=pid, drawing_id=p.drawings[0].id, page=1,
                    kind="NER_NONCRITICAL", label=label, x=x, y=y, origin="manual",
                    confidence=1.0, confirmed=True, total_ports=total_ports)
        db.add(e)
        db.commit()
        migrate.backfill_assets(db, db.get(Project, pid))
        db.refresh(e)
        if e.ner_asset is not None:
            e.ner_asset.total_ports = total_ports
            db.commit()
        return e.id


def _ap(client, pid, x, y, kind="WIRELESS", **extra):
    r = client.post(f"/api/projects/{pid}/elements",
                    json={"page": 1, "kind": kind, "label": "", "x": x, "y": y, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _element(client, pid, el_id):
    return next(e for e in client.get(f"/api/projects/{pid}/elements").json() if e["id"] == el_id)


def _match(client, pid):
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200


def _analyse(pid):
    with SessionLocal() as db:
        p = db.get(Project, pid)
        result, ners = S.analyse(db, p)
        S.save_scenario(db, p, "As-Is", "as_is", result, ners)
        db.commit()
    return result


OFFICE = [_line("Laptop", 4, 4, "LAP", 1), _line(DWISA, 1, 1, "DWISA", 2)]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The tie: proposed, moved, chosen, cleared
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# One kind of access point (2.14.0)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Points drawn before 2.5.0
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------
