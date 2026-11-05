"""RackReach Map 1.0.0 — the map-only edition.

What makes it the map edition and not the full analyzer: no TEAP, no
matching, no analysis, no workbook; the PDF draws everything that is drawn,
Wi-Fi points included; everything may be drawn on a key plan; and the
installation never shares a name, a port or a task with the full analyzer.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pymupdf
import pytest

TMP = tempfile.mkdtemp(prefix="ner-map-100-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/t100.sqlite3")

from fastapi.testclient import TestClient                          # noqa: E402

from app import migrate                                            # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.main import app                                           # noqa: E402
from app.models import Drawing, Page, Project                      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


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
    r = c.post("/api/auth/login", json={"username": "tester", "password": "a-long-test-phrase"})
    assert r.status_code == 200, r.text
    return c


def _project(name: str, key_plan: bool = False) -> int:
    path = f"{TMP}/{name}.pdf"
    doc = pymupdf.open()
    doc.new_page(width=1000, height=700)
    doc.save(path)
    doc.close()
    with SessionLocal() as db:
        p = Project(name=name, venue_code="VVV")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename=f"{name}.pdf", stored_path=path,
                    sha256=(name * 64)[:64], page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=1000, height=700, metres_per_unit=1.0,
                    scale_label="1u=1m", scale_source="manual", scale_confirmed=True,
                    key_plan=key_plan, key_plan_source="manual" if key_plan else None))
        db.commit()
        migrate.backfill_assets(db, db.get(Project, p.id))
        db.commit()
        return p.id


def test_no_teap_matching_analysis_route_or_vertical_endpoint(client):
    pid = _project("no-teap")
    for path in (f"/api/projects/{pid}/teap", f"/api/projects/{pid}/matching/run",
                 f"/api/projects/{pid}/analyse", f"/api/projects/{pid}/routes",
                 f"/api/projects/{pid}/verticals", f"/api/projects/{pid}/wireless/place-from-teap"):
        assert client.post(path).status_code in (404, 405), path
    assert client.get("/api/reference/naming").status_code in (404, 405)


def test_the_only_report_is_the_marked_pdf(client):
    pid = _project("only-pdf")
    for fmt in ("xlsx", "csv"):
        assert client.post(f"/api/projects/{pid}/reports/{fmt}").status_code == 422


def test_a_ner_and_a_wifi_point_may_go_on_a_key_plan(client):
    pid = _project("key-ok", key_plan=True)
    for i, kind in enumerate(("NER_CRITICAL", "NER_NONCRITICAL", "WIRELESS", "TER")):
        label = "" if kind == "WIRELESS" else f"K{i}"
        r = client.post(f"/api/projects/{pid}/elements",
                        json={"page": 1, "kind": kind, "label": label, "x": 100 + 200 * i, "y": 100})
        assert r.status_code == 201, (kind, r.text)


def test_the_pdf_draws_ner_and_ter_named_and_wifi_as_a_sign_only(client):
    pid = _project("pdf-all")
    for kind, label, x in (("NER_CRITICAL", "NERc_01.1", 300), ("TER", "TER A", 500),
                           ("WIRELESS", "", 700)):
        r = client.post(f"/api/projects/{pid}/elements",
                        json={"page": 1, "kind": kind, "label": label, "x": x, "y": 300})
        assert r.status_code == 201, r.text
    wifi = r.json()["label"]
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false")
    assert r.status_code == 200, r.text
    from app.config import settings
    doc = pymupdf.open(settings.exports_dir / f"project_{pid}" / r.json()["file"])
    text = doc[0].get_text()
    doc.close()
    for word in ("NERc_01.1", "TER A", "NER MAP", "Reach circle - general limit"):
        assert word in text, word
    assert wifi not in text, "a Wi-Fi point is a sign only, with no name"
    assert "slack" not in text.lower() and "TEAP" not in text


def test_the_installation_never_shares_a_name_or_a_port_with_the_full_analyzer():
    for rel in ("setup.bat", "autostart.bat", "tools/server-start.cmd",
                "tools/watchdog-install.ps1", "tools/watchdog.ps1", "run.py"):
        if not (ROOT / rel).exists():      # setup.bat stays in the package, not the install
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        assert "NER Spatial Coverage Analyzer" not in text, rel
        assert not re.search(r"(?<![\d.])8000(?!\d)", text), rel
    if (ROOT / "setup.bat").exists():
        setup = (ROOT / "setup.bat").read_text(encoding="utf-8", errors="ignore")
        assert "RackReach Map" in setup and "ner-map-analyzer" in setup
    assert (ROOT / "VERSION").read_text().strip().startswith("1.")


def test_the_menu_is_the_map_study():
    app_tsx = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    for gone in ("MatchingPanel", "DemandPanel", "WifiPanel", "ExceptionsPanel"):
        assert gone not in app_tsx, gone
    viewer = (ROOT / "frontend/src/pages/Viewer.tsx").read_text(encoding="utf-8")
    groups = viewer[viewer.index("const TOOL_GROUPS"):viewer.index("const ILLUSTRATIVE_TOOLS")]
    assert '"measure"' in groups and '"add-ner-critical"' in groups and '"add-wireless"' in groups
    assert '"route"' not in groups and '"vertical"' not in groups and "-ref" not in groups
