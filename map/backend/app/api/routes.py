"""HTTP API.

Thin by design: parsing, persistence and dispatch only. Every number returned here
was produced by ``app.domain``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import pickle
import re
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, selectinload

from .. import auth as A
from .. import isolate
from .. import services as S
from .. import sheets as SH
from .auth_routes import current_user
from ..config import settings
from ..db import get_session
from ..jobs import job_to_dict, manager as job_manager
from ..domain.coverage import SERVING_KINDS
from ..domain.distance import (GROUP_LABEL, PENDING_NO_ROOM, PENDING_NOT_ANALYSED,
                               STATUS_LABEL as STATUS_LABEL_TEXT, DemandType, Group,
                               Status, group_of)
from ..domain.pdf_ingest import ElementKind, render_page_png
from ..domain.reports import export_csv, export_marked_pdf, export_workbook
from ..domain.units import Scale, parse_scale_text, scale_from_ratio, scale_from_two_points
from ..models import (Alias, AuditLog, Drawing, DrawingSpace, Element, Job, KeepDecision,
                      MappingTemplate, NerAsset, NerStatus, Page, Project, RouteSegmentRow,
                      Scenario, SpaceMatchRow, SpaceOccurrenceLink, TeapFile, TeapSpace, User,
                      VerticalLinkRow,
                      Visibility, Zone)

router = APIRouter()


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _err(exc: S.ValidationError) -> HTTPException:
    return HTTPException(422, str(exc))


def _split(sh: "SH.Sheets", key: int) -> tuple[Optional[int], int]:
    """``(drawing_id, page)`` of a sheet key the client sent — 404 when it
    names no sheet of the project (4.1.0)."""
    try:
        return sh.split(key)
    except SH.SheetError as e:
        raise HTTPException(404, str(e))


def _on_sheet(model, sh: "SH.Sheets", key: int) -> list:
    """The ``where`` clauses that pick the rows of ``model`` drawn on sheet
    ``key`` — page number *and* book (4.1.0)."""
    did, page = _split(sh, key)
    first = sh.drawing_ids[0] if sh.drawing_ids else None
    return [model.page == page, S.on_book(model.drawing_id, did, first)]


def _page_by_key(db: Session, p: Project, key: int) -> Optional[Page]:
    sh = SH.for_project(db, p.id)
    did, page = _split(sh, key)
    q = select(Page).join(Drawing).where(Drawing.project_id == p.id, Page.number == page)
    if did is not None:
        q = q.where(Page.drawing_id == did)
    return db.execute(q).scalars().first()


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@router.get("/meta")
def meta() -> dict:
    return {"app": settings.app_name, "version": settings.version,
            "disclaimer": settings.disclaimer,
            "max_upload_mb": settings.max_upload_mb,
            "element_kinds": [ElementKind.TER, ElementKind.NER_CRITICAL,
                              ElementKind.NER_NONCRITICAL, ElementKind.IDF,
                              ElementKind.COW, ElementKind.SHAFT]}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str
    venue_code: Optional[str] = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a project needs a name")
        if len(v) > 200:            # the column holds 200 (4.8.0: 5,000 were stored)
            raise ValueError("a project name has at most 200 characters")
        return v

    @field_validator("venue_code")
    @classmethod
    def _venue(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if len(v) > 40:
            raise ValueError("a venue code has at most 40 characters")
        return v or None


class ProjectSettingsIn(BaseModel):
    general_limit_m: Optional[float] = Field(None, gt=0)
    photographer_limit_m: Optional[float] = Field(None, gt=0)
    near_limit_ratio: Optional[float] = Field(None, gt=0, le=1)
    routing_slack_factor: Optional[float] = Field(None, ge=1.0)
    #: 4.8.0: only what the Display units select offers.
    display_units: Optional[Literal["m", "ft", "both"]] = None
    demand_rules: Optional[dict[str, Any]] = None
    match_settings: Optional[dict[str, Any]] = None
    synonyms: Optional[dict[str, Any]] = None
    #: Fibre runs to the TER per NER (2.8.0) — a count, never a length.
    fibre_runs_critical: Optional[int] = Field(None, ge=0, le=16)
    fibre_runs_noncritical: Optional[int] = Field(None, ge=0, le=16)


@router.get("/projects")
def list_projects(user: User = Depends(current_user),
                  db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()
    owners = {u.id: u.username for u in db.execute(select(User)).scalars()}
    return [{"id": p.id, "name": p.name, "venue_code": p.venue_code,
             "description": p.description, "created_at": p.created_at.isoformat(),
             "drawings": len(p.drawings), "teap_files": len(p.teap_files),
             "scenarios": len(p.scenarios),
             "visibility": p.visibility,
             "owner": owners.get(p.owner_id) if p.owner_id else None,
             "mine": p.owner_id == user.id or p.owner_id is None,
             "can_edit": A.can_write_project(user, p)}
            for p in rows if A.can_read_project(user, p)]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user),
                   db: Session = Depends(get_session)) -> dict:
    # The same name in other capitals is the same project to a person (4.2.0).
    if db.execute(select(Project).where(
            func.lower(Project.name) == body.name.lower())).first():
        raise HTTPException(409, "a project with that name already exists")
    # Private until its owner decides otherwise: a venue design should not become
    # readable by every account on the machine because somebody forgot a toggle.
    p = Project(id=S.next_project_id(db), name=body.name, venue_code=body.venue_code,
                description=body.description, owner_id=user.id,
                visibility=Visibility.PRIVATE)
    db.add(p)
    db.flush()
    S.audit(db, p.id, "project.created", entity="project", entity_id=p.id,
            actor=user.username, payload={"visibility": p.visibility})
    db.commit()
    return {"id": p.id, "name": p.name, "visibility": p.visibility}


class VisibilityIn(BaseModel):
    visibility: str


@router.post("/projects/{project_id}/visibility")
def set_visibility(project_id: int, body: VisibilityIn,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_session)) -> dict:
    """Share a project with the other accounts on this installation, or stop.

    Shared means *readable* by signed-in accounts here. It does not publish
    anything anywhere, and it does not grant editing.
    """
    p = _project(db, project_id)
    if body.visibility not in Visibility.ALL:
        raise HTTPException(422, f"visibility must be one of {list(Visibility.ALL)}")
    before = p.visibility
    p.visibility = body.visibility
    S.audit(db, p.id, "project.visibility", entity="project", entity_id=p.id,
            actor=user.username, payload={"from": before, "to": p.visibility})
    db.commit()
    return {"id": p.id, "visibility": p.visibility,
            "note": ("Shared projects are readable by other accounts on this "
                     "installation only. Nothing is published outside this machine.")}


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(current_user),
                db: Session = Depends(get_session)) -> dict:
    p = _project(db, project_id)
    pages = db.execute(select(Page).join(Drawing)
                       .where(Drawing.project_id == p.id).order_by(Page.number)).scalars().all()
    # One list of sheets across every book (4.1.0), book by book: ``number``
    # is the sheet key every other call speaks — the page number itself in a
    # one-book project — and ``page`` / ``drawing_id`` say where it is.
    sh = SH.for_project(db, p.id)
    pages = sorted(pages, key=lambda pg: sh.key(pg.drawing_id, pg.number))
    return {
        "id": p.id, "name": p.name, "venue_code": p.venue_code,
        "description": p.description,
        "settings": {
            "general_limit_m": p.general_limit_m,
            "photographer_limit_m": p.photographer_limit_m,
            "near_limit_ratio": p.near_limit_ratio,
            "routing_slack_factor": p.routing_slack_factor,
            "display_units": p.display_units,
            "demand_rules": p.demand_rules, "match_settings": p.match_settings,
            "synonyms": p.synonyms,
            "fibre_runs_critical": p.fibre_runs_critical,
            "fibre_runs_noncritical": p.fibre_runs_noncritical,
        },
        # The annotations kept for the placing step (2.14.0) can run to
        # hundreds of entries with evidence; the screen needs their count.
        "drawings": [{"id": d.id, "filename": d.filename, "page_count": d.page_count,
                      # The same file imported before 4.6.0 refused it: named
                      # so the Import screen can point at the copy to remove.
                      "duplicate_of": [x.filename for x in p.drawings
                                       if x.id < d.id and d.sha256 and x.sha256 == d.sha256],
                      "summary":{**{k: v for k, v in (d.extract_summary or {}).items()
                                     if k != "ner_annotations"},
                                  "ner_annotations_count": len((d.extract_summary or {}).get("ner_annotations") or [])}}
                     for d in p.drawings],
        "teap_files": [{"id": t.id, "filename": t.filename, "sheet": t.sheet,
                        "header_row": t.header_row, "imported_rows": t.imported_rows,
                        "mapping": t.mapping} for t in p.teap_files],
        "multi_book": sh.multi,
        "pages": [{"number": sh.key(pg.drawing_id, pg.number),
                   "page": pg.number, "drawing_id": pg.drawing_id,
                   "sheet_label": sh.label(sh.key(pg.drawing_id, pg.number)),
                   "width": pg.width, "height": pg.height,
                   "rotation": pg.rotation, "is_vector": pg.is_vector,
                   "needs_ocr": pg.needs_ocr, "level": pg.level,
                   "sheet_title": pg.sheet_title,
                   # The drawing number from the title block, or "" when the
                   # sheet carries none — "Page 13 - VFF-410B8-CSL-O".
                   "sheet_number": pg.sheet_number or "",
                   # A key plan only illustrates (2.13.0); "manual" when the
                   # person switched it, "title" when the title block said so.
                   "key_plan": bool(pg.key_plan),
                   "key_plan_source": pg.key_plan_source,
                   "scale_label": pg.scale_label,
                   "scale_source": pg.scale_source,
                   "scale_confirmed": pg.scale_confirmed,
                   "metres_per_unit": pg.metres_per_unit,
                   "candidates": pg.scale_candidates} for pg in pages],
        "scenarios": [{"id": s.id, "name": s.name, "kind": s.kind,
                       "summary": s.summary} for s in p.scenarios],
        "scale_ready": all(pg.scale_confirmed for pg in pages) if pages else False,
    }


@router.patch("/projects/{project_id}/settings")
def update_settings(project_id: int, body: ProjectSettingsIn,
                    db: Session = Depends(get_session)) -> dict:
    p = _project(db, project_id)
    # The photographer limit is the stricter one (4.8.0): a value above the
    # general limit was stored and turned the rule upside down.
    general = body.general_limit_m if body.general_limit_m is not None else p.general_limit_m
    photo = body.photographer_limit_m if body.photographer_limit_m is not None else p.photographer_limit_m
    if general is not None and photo is not None and photo > general:
        raise HTTPException(422, f"the photographer limit ({photo:g} m) cannot be longer than "
                                 f"the general limit ({general:g} m)")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    S.audit(db, p.id, "project.settings_updated", payload=body.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_session)) -> Response:
    p = _project(db, project_id)
    # The project's own audit log goes with it; who deleted it, when, and what
    # it held is kept in the installation's log (4.2.1) — it used to leave no
    # trace at all.
    from ..models import NerAsset as _NA
    held = {"name": p.name, "venue_code": p.venue_code,
            "drawings": [d.filename for d in p.drawings],
            "teap_files": [t.filename for t in p.teap_files],
            "ners": db.execute(select(func.count(_NA.id)).where(_NA.project_id == p.id)).scalar() or 0,
            "created_at": p.created_at.isoformat() if p.created_at else None}
    db.delete(p)
    S.audit(db, None, "project.deleted", entity="project", entity_id=project_id,
            payload=held, justification="project deleted with its files and its audit log")
    db.commit()
    held["files_removed"] = S.remove_project_files(project_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Handing a project to another computer (4.6.0) — see ``app/transfer.py``
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/export")
def export_project(project_id: int, background: bool = Query(True),
                   user: User = Depends(current_user),
                   db: Session = Depends(get_session)) -> dict:
    """Write the whole project — rows and documents — into one package for
    another computer. Anyone who can read the project may take a copy."""
    from .. import transfer as TR
    p = _project(db, project_id)

    def work(ctx, jdb: Session) -> dict:
        proj = jdb.get(Project, project_id)
        out = TR.export_project(jdb, proj, actor=user.username, progress=ctx.progress)
        S.audit(jdb, proj.id, "project.exported", entity="project", entity_id=proj.id,
                actor=user.username,
                payload={k: out[k] for k in ("filename", "bytes", "sha256", "documents")})
        jdb.commit()
        out.pop("path", None)
        return out

    if background:
        return {"job_id": job_manager.submit(kind="project.export", project_id=p.id,
                                             fn=work, db=db), "background": True}
    try:
        out = TR.export_project(db, p, actor=user.username)
    except TR.TransferError as e:
        raise HTTPException(422, str(e))
    S.audit(db, p.id, "project.exported", entity="project", entity_id=p.id,
            actor=user.username,
            payload={k: out[k] for k in ("filename", "bytes", "sha256", "documents")})
    db.commit()
    out.pop("path", None)
    return out


@router.get("/projects/{project_id}/export/{filename}")
def download_export(project_id: int, filename: str,
                    db: Session = Depends(get_session)) -> FileResponse:
    _project(db, project_id)
    if not re.fullmatch(r"NER-project_[A-Za-z0-9._-]+\.zip", filename):
        raise HTTPException(404, "package not found")
    path = settings.exports_dir / f"project_{project_id}" / filename
    if not path.is_file():
        raise HTTPException(404, "package not found — export the project again")
    return FileResponse(path, media_type="application/zip", filename=filename)


@router.post("/projects/import", status_code=201)
async def import_project(file: UploadFile = File(...), name: str = Query(""),
                         background: bool = Query(True),
                         user: User = Depends(current_user),
                         db: Session = Depends(get_session)) -> dict:
    """Create a new project, owned by the person importing, from a package
    exported on another computer. Nothing existing is touched."""
    import shutil as _sh
    import uuid as _uuid

    from .. import transfer as TR
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(422, "a project package is a .zip file exported by the RackReach Map")
    inbox = settings.data_dir / "imports"
    inbox.mkdir(parents=True, exist_ok=True)
    # A package left behind by a server stopped mid-import is cleared after a day.
    import time as _time
    for old in inbox.glob("*.zip"):
        try:
            if _time.time() - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    path = inbox / f"{_uuid.uuid4().hex}.zip"
    limit = TR.MAX_PACKAGE_MB << 20
    size = 0
    with open(path, "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"the package is larger than {TR.MAX_PACKAGE_MB} MB")
            out.write(chunk)
    try:
        TR.read_manifest(path)          # refuse a wrong file before queuing anything
    except TR.TransferError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    user_id = user.id

    def work(ctx, jdb: Session) -> dict:
        try:
            who = jdb.get(User, user_id)
            return TR.import_project(jdb, path, user=who, name=name or None,
                                     progress=ctx.progress)
        finally:
            path.unlink(missing_ok=True)

    if background:
        return {"job_id": job_manager.submit(kind="project.import", project_id=None,
                                             fn=work, db=db), "background": True}
    try:
        return await run_in_threadpool(TR.import_project, db, path, user=user,
                                       name=name or None)
    except TR.TransferError as e:
        raise HTTPException(422, str(e))
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/drawings", status_code=201)
async def upload_drawing(project_id: int, file: UploadFile = File(...),
                         background: bool = Query(False),
                         place_ners: bool = Query(False),
                         db: Session = Depends(get_session)) -> dict:
    """Upload a drawing.

    With ``background=true`` the file is stored immediately and the parsing runs as
    a cancellable job — a 34-page venue design book takes about half a minute and
    has no business holding an HTTP request open.

    The NERs the book states are read but not placed (2.14.0): that is the
    *Place NERs from the drawing* step on the Scale screen, after the key
    plans are ticked and the scales confirmed. ``place_ners=true`` does it at
    once, as the import did before.
    """
    p = _project(db, project_id)
    try:
        path = S.store_upload(p.id, file.filename or "drawing.pdf", file.file,
                              allowed_suffixes=(".pdf",))
    except S.ValidationError as e:
        raise _err(e)
    name = file.filename or path.name
    # The same book twice is refused before anything is read (4.6.0).
    twin = S.duplicate_drawing(db, p.id, name, S.sha256_file(path))
    if twin is not None:
        path.unlink(missing_ok=True)
        raise HTTPException(409, twin)

    if background:
        def work(ctx, jdb: Session) -> dict:
            proj = jdb.get(Project, project_id)
            try:
                d = S.ingest_drawing(jdb, proj, path, name, progress=ctx.progress,
                                     place_ners=place_ners)
            except BaseException as exc:
                # A failed or cancelled import leaves nothing behind (4.2.0): the
                # stored file used to stay in data\uploads for good.
                path.unlink(missing_ok=True)
                friendly = _unreadable_pdf(exc, name)
                if friendly is exc:
                    raise
                raise friendly from exc
            return {"id": d.id, "page_count": d.page_count, "summary": d.extract_summary}
        job_id = job_manager.submit(kind="drawing.ingest", project_id=p.id,
                                    fn=work, db=db)
        return {"job_id": job_id, "background": True}

    try:
        # Off the event loop (4.8.0): read inside this ``async`` handler, a
        # book held every other request — another screen waited 58 s.
        drawing = await run_in_threadpool(S.ingest_drawing, db, p, path, name,
                                          place_ners=place_ners)
    except S.ValidationError as e:
        path.unlink(missing_ok=True)
        raise _err(e)
    except Exception as e:                              # noqa: BLE001
        # A file that is not a PDF used to answer 500 (4.2.0).
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(_unreadable_pdf(e, name)))
    return {"id": drawing.id, "page_count": drawing.page_count,
            "summary": drawing.extract_summary}


@router.delete("/projects/{project_id}/drawings/{drawing_id}")
def delete_drawing(project_id: int, drawing_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_session)) -> dict:
    """Remove one book from the project with everything read off it (4.6.0)
    — see :func:`app.services.delete_drawing` for what moves and what goes."""
    p = _project(db, project_id)
    d = db.get(Drawing, drawing_id)
    if d is None or d.project_id != p.id:
        raise HTTPException(404, "drawing not found")
    busy = db.execute(select(Job).where(Job.project_id == p.id,
                                        Job.state.in_(("pending", "running")))).scalars().first()
    if busy is not None:
        raise HTTPException(409, "a task is still running on this project — wait for it to "
                                 "finish, then remove the book")
    try:
        return S.delete_drawing(db, p, d, actor=user.username)
    except S.ValidationError as e:
        raise _err(e)


@router.get("/projects/{project_id}/drawings/{drawing_id}/impact")
def drawing_impact(project_id: int, drawing_id: int,
                   db: Session = Depends(get_session)) -> dict:
    """What removing a book would take with it — counted before the person
    confirms, so the question is asked with the answer on the screen."""
    p = _project(db, project_id)
    d = db.get(Drawing, drawing_id)
    if d is None or d.project_id != p.id:
        raise HTTPException(404, "drawing not found")
    ids = sorted(x.id for x in p.drawings)
    first = ids[0] if ids else None
    here = (lambda col: S.on_book(col, d.id, first)) if d.id == first else (lambda col: col == d.id)
    rooms = select(DrawingSpace.id).where(DrawingSpace.drawing_id == d.id)
    ners_here = db.execute(select(NerAsset).where(
        NerAsset.project_id == p.id, here(NerAsset.canonical_drawing_id),
        NerAsset.deleted_at.is_(None))).scalars().all()
    only_here = []
    for a in ners_here:
        other = db.execute(select(Element.id).where(
            Element.ner_asset_id == a.id, Element.drawing_id.is_not(None),
            Element.drawing_id != d.id).limit(1)).first()
        if other is None:
            only_here.append(a.display_name)
    return {
        "filename": d.filename, "pages": d.page_count,
        "spaces": db.execute(select(func.count()).select_from(rooms.subquery())).scalar() or 0,
        "markers": db.execute(select(func.count(Element.id)).where(
            Element.project_id == p.id, here(Element.drawing_id))).scalar() or 0,
        "ners_only_here": sorted(only_here),
        "ners_moved": len(ners_here) - len(only_here),
        "ties": db.execute(select(func.count(TeapSpace.id)).where(
            TeapSpace.project_id == p.id,
            TeapSpace.locked_drawing_space_id.in_(rooms))).scalar() or 0,
        "duplicate_of": [x.filename for x in p.drawings
                         if x.id != d.id and d.sha256 and x.sha256 == d.sha256],
    }


def _unreadable_pdf(exc: BaseException, name: str) -> BaseException:
    """The message a person reads when a book cannot be read (4.2.0) — not the
    reader's own text with the full path of the stored copy in it."""
    if not isinstance(exc, Exception) or isinstance(exc, S.ValidationError):
        return exc
    text = str(exc)
    if "FileDataError" in text or "open file" in text or "not a PDF" in text.lower() \
            or "cannot open" in text.lower():
        return S.ValidationError(f"'{name}' could not be read as a PDF — the file is damaged, "
                                 f"protected or not a PDF. Nothing was imported.")
    return exc


@router.get("/projects/{project_id}/pages/{page_no}/image")
def page_image(project_id: int, page_no: int, dpi: int = Query(110, ge=40, le=300),
               db: Session = Depends(get_session)) -> Response:
    p = _project(db, project_id)
    if not p.drawings:
        raise HTTPException(404, "no drawing uploaded")
    # ``page_no`` is a sheet key (4.1.0) — the page number in a one-book project.
    try:
        did, page_no = SH.for_project(db, p.id).split(page_no)
    except SH.SheetError as e:
        raise HTTPException(404, str(e))
    drawing = db.get(Drawing, did) if did is not None else p.drawings[0]
    try:
        png = render_page_png(drawing.stored_path, page_no, dpi=dpi)
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(404, f"page not available: {e}")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/projects/{project_id}/drawings/{drawing_id}/file")
def drawing_file(project_id: int, drawing_id: int,
                 db: Session = Depends(get_session)) -> FileResponse:
    """Serve the original PDF so the viewer can render it with PDF.js."""
    _project(db, project_id)
    d = db.get(Drawing, drawing_id)
    if d is None or d.project_id != project_id:
        raise HTTPException(404, "drawing not found")
    path = Path(d.stored_path)
    if not path.exists():
        raise HTTPException(404, "the stored file is missing")
    return FileResponse(path, media_type="application/pdf", filename=d.filename)


class ScaleIn(BaseModel):
    """Either a ratio, a scale annotation, or a two-point calibration."""

    denominator: Optional[float] = Field(None, gt=0)
    text: Optional[str] = None
    pdf_distance_units: Optional[float] = Field(None, gt=0)
    real_distance: Optional[float] = Field(None, gt=0)
    unit: str = "m"
    confirm: bool = False


@router.post("/projects/{project_id}/pages/{page_no}/scale")
def set_scale(project_id: int, page_no: int, body: ScaleIn,
              db: Session = Depends(get_session)) -> dict:
    p = _project(db, project_id)
    page = _page_by_key(db, p, page_no)
    if page is None:
        raise HTTPException(404, "page not found")

    if body.pdf_distance_units and body.real_distance:
        sc = scale_from_two_points(body.pdf_distance_units, body.real_distance, body.unit)
    elif body.denominator:
        sc = scale_from_ratio(body.denominator)
    elif body.text:
        sc = parse_scale_text(body.text)
        if sc is None:
            raise HTTPException(422, f"could not interpret the scale text {body.text!r}")
    else:
        raise HTTPException(422, "supply a denominator, a scale text, or two calibration points")

    page.metres_per_unit = sc.metres_per_unit
    page.scale_label = sc.label
    page.scale_source = sc.source
    page.scale_confirmed = bool(body.confirm)
    S.audit(db, p.id, "scale.set", entity="page", entity_id=page.id,
            payload={"page": page_no, "label": sc.label, "confirmed": body.confirm})
    db.commit()
    return {"page": page_no, "label": sc.label, "metres_per_unit": sc.metres_per_unit,
            "confirmed": page.scale_confirmed}


class KeyPlanIn(BaseModel):
    key_plan: bool


@router.post("/projects/{project_id}/pages/{page_no}/key-plan")
def set_key_plan(project_id: int, page_no: int, body: KeyPlanIn,
                 user: User = Depends(current_user),
                 db: Session = Depends(get_session)) -> dict:
    """Say whether a page is a key plan (2.13.0) — the switch in the Drawing
    viewer, for a title block read wrong. A key plan only illustrates: its
    rooms leave every TEAP × drawing crossing, so ties to them are released
    and the matching is run again (which also brings a page's rooms back when
    it stops being a key plan). NERs already drawn on the page are left as
    they are — they are said so in the answer."""
    p = _project(db, project_id)
    try:
        out = S.set_key_plan(db, p, page_no, body.key_plan, actor=user.username)
    except S.ValidationError as exc:
        raise HTTPException(404, str(exc)) from exc
    ners_here = db.execute(select(Element).where(
        Element.project_id == p.id, *_on_sheet(Element, SH.for_project(db, p.id), page_no),
        Element.scenario_id.is_(None),
        Element.kind.in_(("NER_CRITICAL", "NER_NONCRITICAL", "WIRELESS")))).scalars().all()
    out["counted_here"] = [e.label for e in ners_here]
    return out


class KeyPlansIn(BaseModel):
    #: page number → is it a key plan
    pages: dict[int, bool]


@router.post("/projects/{project_id}/pages/key-plans")
def set_key_plans(project_id: int, body: KeyPlansIn,
                  user: User = Depends(current_user),
                  db: Session = Depends(get_session)) -> dict:
    """The key-plan ticks of the Scale screen (2.14.0), several pages at once:
    ticked pages lose their spaces and leave every crossing, unticked pages
    get their spaces read, and the matching runs once at the end."""
    p = _project(db, project_id)
    try:
        return S.set_key_plans(db, p, {int(k): bool(v) for k, v in body.pages.items()},
                               actor=user.username)
    except S.ValidationError as exc:
        raise HTTPException(404, str(exc)) from exc


#: Held while a placing job is checked for and submitted, so two presses that
#: arrive together cannot both find "nothing running" (4.0.0).
_PLACE_SUBMIT = threading.Lock()


@router.post("/projects/{project_id}/ners/place-from-drawing")
def place_ners_from_drawing(project_id: int, background: bool = Query(False),
                            user: User = Depends(current_user),
                            db: Session = Depends(get_session)) -> dict:
    """The NER placing step (2.14.0): the team's annotations, the TEC.003
    rooms and the "Network Equipment Rack (NER)" labels of every book become
    NERs — never on a key plan, never where a NER already is. Run it after
    the key plans are ticked and the scales confirmed; running it again only
    adds what is still missing."""
    p = _project(db, project_id)
    # A second press while a placing job of this project is still queued or
    # running is refused (4.0.0) — it would read the same "nothing placed
    # yet" and place every NER again.
    # Only a job alive in this server counts: a row a crash left "running"
    # must not block the button for ever.
    if background:
        def work(ctx, jdb: Session) -> dict:
            return S.place_ners_from_drawing(jdb, jdb.get(Project, project_id),
                                            actor=user.username, progress=ctx.progress)
        with _PLACE_SUBMIT:
            busy = [j for j in db.execute(select(Job).where(
                Job.project_id == p.id, Job.kind == "ner.place",
                Job.state.in_(("pending", "running")))).scalars()
                if job_manager.is_active(j.id)]
            if busy:
                raise HTTPException(409, S.PLACE_NERS_BUSY)
            return {"job_id": job_manager.submit(kind="ner.place", project_id=p.id,
                                                 fn=work, db=db), "background": True}
    try:
        return S.place_ners_from_drawing(db, p, actor=user.username)
    except S.BusyError as exc:
        raise HTTPException(409, str(exc)) from exc


class WifiUndoIn(BaseModel):
    space_key: Optional[str] = None
    element_id: Optional[int] = None


@router.post("/projects/{project_id}/pages/scale/confirm-all")
def confirm_all_scales(project_id: int, db: Session = Depends(get_session)) -> dict:
    """Confirm the detected scale on every page that has one, in one act.

    A 75-page book meant 75 clicks on the Scale screen, one per row, for the
    same decision each time — "the scale printed in the title block is the
    scale". This confirms whatever each page already holds: the detected
    value, or one typed in and set earlier. It never invents one: a page with
    no scale at all is skipped and named in the answer, because the only
    honest thing to do with such a page is to keep blocking its results until
    somebody types the ratio in. The per-page controls stay for exactly that.
    """
    p = _project(db, project_id)
    pages = db.execute(select(Page).join(Drawing)
                       .where(Drawing.project_id == p.id)
                       .order_by(Page.number)).scalars().all()
    confirmed: list[int] = []
    already: list[int] = []
    skipped: list[int] = []
    sh = SH.for_project(db, p.id)
    for page in sorted(pages, key=lambda pg: sh.key(pg.drawing_id, pg.number)):
        k = sh.key(page.drawing_id, page.number)
        if page.metres_per_unit is None:
            skipped.append(k)
        elif page.scale_confirmed:
            already.append(k)
        else:
            page.scale_confirmed = True
            confirmed.append(k)
    S.audit(db, p.id, "scale.confirmed_all", entity="project", entity_id=p.id,
            payload={"confirmed": confirmed, "already": already,
                     "skipped_no_scale": skipped})
    db.commit()
    return {"confirmed": confirmed, "already_confirmed": already,
            "skipped_no_scale": skipped}


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

class ElementIn(BaseModel):
    page: int
    kind: str
    label: str
    x: float
    y: float
    number: Optional[int] = None
    total_ports: Optional[int] = None
    used_ports: int = 0
    reserved_ports: int = 0
    locked: bool = False
    removable: bool = True
    movable: bool = True
    notes: str = ""
    #: Wireless points only: the TEAP space this access point stands in for.
    #: Left out, the server proposes the nearest matched room on the sheet.
    space_key: Optional[str] = None
    #: NER markers only (2.12.0): this marker is another drawing of that NER
    #: of the register — the answer to "a NER with this name already exists;
    #: is it the same one?". Linked to it, confirmed, never a new room.
    same_as_asset_id: Optional[int] = None


class ElementPatch(BaseModel):
    kind: Optional[str] = None
    label: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    total_ports: Optional[int] = None
    used_ports: Optional[int] = None
    reserved_ports: Optional[int] = None
    locked: Optional[bool] = None
    removable: Optional[bool] = None
    movable: Optional[bool] = None
    confirmed: Optional[bool] = None
    notes: Optional[str] = None
    #: Wireless points only. A space key ties the point by hand; an empty
    #: string clears the tie by hand (and stops the automatic proposal from
    #: coming back on the next move). ``None`` leaves it alone.
    space_key: Optional[str] = None
    #: Wireless points only (4.5.0): ``indoor`` (a covered area), ``outdoor``
    #: (an open area) or an empty string for "not said". ``None`` leaves it.
    environment: Optional[str] = None


@router.get("/projects/{project_id}/elements")
def list_elements(project_id: int, page: Optional[int] = None,
                  db: Session = Depends(get_session)) -> list[dict]:
    _project(db, project_id)
    # ``page`` and every row's ``page`` are sheet keys (4.1.0) — page numbers
    # in a one-book project.
    sh = SH.for_project(db, project_id)
    # The register NER of each marker and that NER's other markers come in
    # two queries for the whole list (2.17.0), not two per row.
    q = (select(Element).where(Element.project_id == project_id)
         .options(selectinload(Element.ner_asset).selectinload(NerAsset.occurrences)))
    if page is not None:
        q = q.where(*_on_sheet(Element, sh, page))
    space_names = {t.space_key: t.space_name for t in db.execute(
        select(TeapSpace).where(TeapSpace.project_id == project_id)).scalars()}
    out = []
    rows = db.execute(q.order_by(Element.page, Element.id)).scalars().all()
    for e in sorted(rows, key=lambda e: (sh.of(e), e.id)):
        # From V2 capacity belongs to the canonical NER, not to the marker. The
        # marker columns stay as they were so no V1 data is lost, but the register
        # is what is read and written here.
        a = e.ner_asset
        # A NER deleted on the NERs screen is gone from the plan. The row and
        # its markers stay in the database — the audit needs them, and Restore
        # brings them back — but the viewer must not keep drawing a room the
        # study no longer contains, or offer to drag it.
        if a is not None and (a.deleted_at is not None or a.merged_into_id is not None):
            continue
        cap_src = a if a is not None else e
        out.append({
            "id": e.id, "page": sh.of(e), "kind": e.kind, "label": e.label,
            "number": e.number, "x": e.x, "y": e.y, "origin": e.origin,
            "confidence": e.confidence, "confirmed": e.confirmed,
            "locked": a.locked if a is not None else e.locked,
            "removable": e.removable, "movable": e.movable,
            "total_ports": cap_src.total_ports, "used_ports": cap_src.used_ports,
            "reserved_ports": cap_src.reserved_ports, "evidence": e.evidence,
            "notes": e.notes, "capacity_known": cap_src.total_ports is not None,
            "ner_asset_id": e.ner_asset_id,
            "ner_code": a.code if a is not None else None,
            "ner_name": a.display_name if a is not None else None,
            "link_status": e.link_status,
            "occurrence_count": len(a.occurrences) if a is not None else 1,
            "space_key": e.space_key, "tie_source": e.tie_source,
            "space_name": space_names.get(e.space_key) if e.space_key else None,
            # 2.13.0: a NER read off the room the drawing prints (a TEC.003
            # code or a "Network Equipment Rack (NER)" label), and the label.
            "detection_method": e.detection_method,
            "printed_as": e.nearby_text or None,
            # A NER's is the room's; a Wi-Fi point's is its own (4.5.0).
            "environment": a.environment if a is not None else e.environment,
            "pending": list(a.pending_validations or []) if a is not None else [],
        })
    return out


#: Every kind a marker can be created as (4.8.0: anything else was stored).
CREATABLE_KINDS = frozenset({
    "TER", "NER_CRITICAL", "NER_NONCRITICAL", "IDF", "COW", "DIVA", "WIRELESS",
    "NER_CRITICAL_REF", "NER_NONCRITICAL_REF", "TER_REF", "COW_REF", "DIVA_REF", "SHAFT"})
#: The longest marker name the register keeps (the column is 120 characters).
LABEL_MAX = 120


def _check_new_element(db: Session, p: Project, body: "ElementIn") -> None:
    """What the screens never send, refused at the door (4.8.0 — the QA pass of
    28 Oct 2026 found each of these stored as sent): an unknown kind, a
    position that is not a number or lies far off the sheet, a name longer
    than the register keeps and a NER with no name."""
    import math as _math

    if body.kind not in CREATABLE_KINDS:
        raise HTTPException(422, f"unknown marker kind '{body.kind}'")
    if not (_math.isfinite(body.x) and _math.isfinite(body.y)):
        raise HTTPException(422, "the position must be a number")
    pg = _page_by_key(db, p, body.page)
    if pg is not None and pg.width and pg.height:
        span = max(pg.width, pg.height)
        if not (-0.1 * span <= body.x <= 1.1 * span and -0.1 * span <= body.y <= 1.1 * span):
            raise HTTPException(422, "the position is off the sheet")
    if len(body.label or "") > LABEL_MAX:
        raise HTTPException(422, f"a name has at most {LABEL_MAX} characters")
    if body.kind in ("NER_CRITICAL", "NER_NONCRITICAL") and not (body.label or "").strip():
        raise HTTPException(422, "a NER needs a name")
    # A NER under the name of another one is *not* refused here: the register
    # takes it as a second drawing of that room when level and building agree,
    # and asks (Possible duplicates) when they do not — the viewer asks the
    # person first and sends ``same_as_asset_id`` when the answer is "same".


@router.post("/projects/{project_id}/elements", status_code=201)
def create_element(project_id: int, body: ElementIn,
                   db: Session = Depends(get_session)) -> dict:
    p = _project(db, project_id)
    from ..domain.pdf_ingest import DEMAND_KINDS

    # The sheet first (4.2.1): a page the project does not have is refused
    # before anything is proposed or written for it.
    _split(SH.for_project(db, p.id), body.page)
    _check_new_element(db, p, body)
    # RackReach Map (1.0.0): everything is drawn to illustrate, so a NER
    # or a Wi-Fi point may go on a key plan too.
    tie: Optional[dict] = None
    if body.kind in DEMAND_KINDS:
        if not (body.label or "").strip().strip("?"):
            # A wireless point needs no name from the person placing forty of
            # them: AP-1, AP-2 …, numbered per project, never reused within it.
            body.label = S.next_wireless_label(db, p.id, body.kind)
        if body.space_key is None:
            tie = S.propose_wireless_tie(db, p, body.page, body.x, body.y)
    from .. import migrate
    from .. import ner_register as R

    twin: Optional[NerAsset] = None
    if body.same_as_asset_id is not None:
        if body.kind not in migrate.ASSET_KINDS:
            raise HTTPException(422, "only a NER marker can be another drawing of a NER")
        try:
            twin = R.get_asset(db, p, body.same_as_asset_id)
        except R.RegisterError as exc:
            raise HTTPException(404, str(exc)) from exc
        if twin.deleted_at is not None or twin.merged_into_id is not None:
            raise HTTPException(409, f"{twin.display_name} was removed from the register — "
                                     "restore it on the NERs screen first, or choose another name")
        # One room, one name, one classification: the marker takes the
        # room's, whatever button placed it.
        body.label = twin.display_name
        if twin.classification == "critical":
            body.kind = "NER_CRITICAL"
        elif twin.classification == "non_critical":
            body.kind = "NER_NONCRITICAL"
    data = body.model_dump()
    data.pop("same_as_asset_id", None)
    if body.kind not in DEMAND_KINDS:
        data.pop("space_key", None)
    # The sheet the marker is placed on names its book and page (4.1.0).
    # It used to go to the first book whatever sheet the viewer showed.
    did, data["page"] = _split(SH.for_project(db, p.id), body.page)
    if did is None:
        did = p.drawings[0].id if p.drawings else None
    e = Element(project_id=p.id, drawing_id=did,
                origin="manual", confidence=1.0, confirmed=True,
                detection_method="manual", document_kind="VDB",
                link_status="unlinked", **data)
    if body.kind in DEMAND_KINDS:
        if body.space_key is not None:
            e.space_key = body.space_key or None
            e.tie_source = "manual"
            # A point drawn by hand for the space brings its Wi-Fi back (4.8.0).
            S.undecline_wifi_space(db, p, e.space_key)
        elif tie is not None:
            e.space_key = tie["space_key"]
            e.tie_source = "auto"
            e.evidence = [f"tied to {tie['space_name']} — nearest matched room on this sheet"
                          + (f", {tie['distance_m']} m from its label" if tie["distance_m"] is not None else "")]
        else:
            e.tie_source = "auto"          # looked, found nothing; a move looks again
            e.evidence = ["no matched room on this sheet within the general limit: "
                          "tied to no TEAP space — not in the TEAP, no port counted"]
    if twin is not None:
        e.ner_asset_id = twin.id
        e.link_status = "confirmed"
        e.link_score = 1.0
        e.evidence = [f"placed as another drawing of {twin.display_name} — "
                      f"the person confirmed it is the same NER"]
    db.add(e)
    db.flush()
    S.audit(db, p.id, "element.created", entity="element", entity_id=e.id,
            payload={**body.model_dump(), "space_key": e.space_key, "tie_source": e.tie_source})
    if twin is not None:
        S.audit(db, p.id, "ner.occurrence_link", entity="ner_asset", entity_id=twin.id,
                justification="same name — confirmed as the same NER when it was placed",
                payload={"element_id": e.id, "page": body.page,
                         "to": {"asset_id": twin.id, "status": "confirmed"}})
    db.commit()

    # A marker a human placed is an equipment room they mean to have, so it enters
    # the register straight away rather than waiting for the next import.
    register = migrate.backfill_assets(db, p)
    db.refresh(e)
    space_name = None
    if e.space_key:
        space_name = db.execute(select(TeapSpace.space_name).where(
            TeapSpace.project_id == p.id, TeapSpace.space_key == e.space_key)).scalar()
    return {"id": e.id, "ner_asset_id": e.ner_asset_id, "register": register,
            "label": e.label, "kind": e.kind, "space_key": e.space_key,
            "space_name": space_name, "tie_source": e.tie_source,
            "evidence": list(e.evidence or [])}


@router.patch("/projects/{project_id}/elements/{element_id}")
def update_element(project_id: int, element_id: int, body: ElementPatch,
                   justification: str = "", db: Session = Depends(get_session)) -> dict:
    _project(db, project_id)
    e = db.get(Element, element_id)
    if e is None or e.project_id != project_id:
        raise HTTPException(404, "element not found")
    changes = body.model_dump(exclude_none=True)
    locked = e.ner_asset.locked if e.ner_asset is not None else e.locked
    if locked and any(k in changes for k in ("x", "y")):
        raise HTTPException(409, "element is locked: unlock it before moving it")

    from .. import migrate
    from .. import ner_register as R
    from ..domain.pdf_ingest import DEMAND_KINDS, REFERENCE_KINDS, VISUAL_ONLY_KINDS

    # The wireless tie: by hand it is a decision ("" clears it and stays
    # cleared); a move re-proposes it only while it is still the automatic one.
    tie_change = changes.pop("space_key", None)
    if tie_change is not None:
        if e.kind not in DEMAND_KINDS:
            raise HTTPException(422, "only a wireless point can be tied to a TEAP space")
        if tie_change and not db.execute(select(TeapSpace.id).where(
                TeapSpace.project_id == project_id, TeapSpace.space_key == tie_change)).first():
            raise HTTPException(404, "TEAP space not found")
        e.space_key = tie_change or None
        e.tie_source = "manual"
        changes["space_key"] = e.space_key
        changes["tie_source"] = "manual"
        S.undecline_wifi_space(db, _project(db, project_id), e.space_key)

    # Indoor / outdoor (4.5.0): said by a person for one Wi-Fi point, the way
    # it is said for a NER on the NERs screen.
    if "environment" in changes:
        env = (changes["environment"] or "").strip().lower() or None
        if e.kind not in DEMAND_KINDS:
            raise HTTPException(422, "indoor / outdoor is set here for a wireless point only; "
                                     "a NER's is set on the NERs screen")
        if env not in (None, "indoor", "outdoor"):
            raise HTTPException(422, "indoor / outdoor must be “indoor”, “outdoor” or empty")
        changes.pop("environment")
        if e.environment != env:
            # Its own action, neutral to the analysis: nothing the engine
            # measures changes, and the workbook reads it off the point, so
            # it must not mark the last run out of date.
            S.audit(db, project_id, "wireless.environment", entity="element", entity_id=e.id,
                    justification=justification,
                    payload={"label": e.label, "from": e.environment, "to": env})
            e.environment = env
        if not changes and tie_change is None:
            db.commit()
            return {"ok": True}

    if "kind" in changes and changes["kind"] not in (
            migrate.ASSET_KINDS + VISUAL_ONLY_KINDS + DEMAND_KINDS + REFERENCE_KINDS
            + ("SHAFT", "UNKNOWN")):
        raise HTTPException(422, f"unknown marker kind {changes['kind']!r}")
    was_ner = e.kind in migrate.ASSET_KINDS
    sh = SH.for_project(db, project_id)
    moved_from = ({"page": sh.of(e), "x": e.x, "y": e.y}
                  if any(k in changes for k in ("x", "y")) else None)
    #: fields that describe the room rather than this drawing of it
    CANONICAL = ("total_ports", "used_ports", "reserved_ports", "locked")
    for k, v in changes.items():
        setattr(e, k, v)
        if e.ner_asset is not None and k in CANONICAL:
            setattr(e.ner_asset, k, v)
            e.ner_asset.updated_by = "user"
    if "label" in changes and e.ner_asset is not None and e.ner_asset.display_name != e.label:
        # One name: renaming the marker renames the room, with the history the
        # register keeps. (Renaming the room on the NERs screen relabels the
        # marker the same way — the two never disagree.)
        try:
            R.rename(db, _project(db, project_id), e.ner_asset, e.label, actor="user",
                     reason=justification or "renamed on the drawing")
        except R.NameInUse as exc:
            raise HTTPException(409, str(exc)) from exc
        except R.RegisterError as exc:
            raise HTTPException(422, str(exc)) from exc
    if "kind" in changes:
        now_ner = changes["kind"] in migrate.ASSET_KINDS
        if e.ner_asset is not None and was_ner and now_ner:
            # Critical ↔ non-critical from the drawing is the same decision as
            # on the NERs screen, and takes effect the same way: on the room,
            # on every marker of it, confirmed.
            try:
                R.classify(db, _project(db, project_id), e.ner_asset,
                           "critical" if changes["kind"] == "NER_CRITICAL" else "non_critical",
                           confirmed=True, actor="user",
                           reason=justification or "kind changed on the drawing")
            except R.RegisterError as exc:
                raise HTTPException(422, str(exc)) from exc
        elif e.ner_asset is not None and was_ner and not now_ner:
            # A NER re-read as a TER, an IDF, a COW or a DIVA node stops being
            # a register row. If this was the room's only marker the row is
            # retired with the reason on it; if the room is drawn elsewhere
            # too, only this marker leaves it.
            asset = e.ner_asset
            e.ner_asset_id = None
            e.link_status = "unlinked"
            db.flush()
            if not [o for o in asset.occurrences if o.id != e.id]:
                asset.deleted_at = _dt.datetime.now(_dt.timezone.utc)
                asset.deleted_reason = f"its marker was re-classified as {changes['kind']}"
                asset.status = NerStatus.REJECTED
                asset.keep_decision = KeepDecision.REJECT
                asset.updated_by = "user"
                S.audit(db, project_id, "ner.removed", entity="ner_asset",
                        entity_id=asset.id, justification=asset.deleted_reason,
                        payload={"code": asset.code, "cause": "marker re-classified"})
    if e.ner_asset is not None and any(k in changes for k in ("x", "y")):
        # Only the marker on the canonical sheet moves the canonical position
        # (3.0.0). A drag on another sheet used to copy that sheet's x/y
        # under the canonical page — the room then stood in the wrong place
        # on the canonical sheet of the marked-up PDF and in every distance.
        if (e.ner_asset.position_source == "occurrence"
                and (e.ner_asset.canonical_page is None
                     or SH.on_canonical_sheet(e, e.ner_asset))
                and (moved_from is None or e.ner_asset.canonical_page is None
                     or (abs((e.ner_asset.canonical_x or 0) - moved_from["x"]) < 1.0
                         and abs((e.ner_asset.canonical_y or 0) - moved_from["y"]) < 1.0)
                     or len([o for o in e.ner_asset.occurrences if o.page == e.page
                             and SH.same_book(o.drawing_id, e.drawing_id)]) == 1)):
            e.ner_asset.canonical_page = e.page
            e.ner_asset.canonical_drawing_id = e.drawing_id or e.ner_asset.canonical_drawing_id
            e.ner_asset.canonical_x = e.x
            e.ner_asset.canonical_y = e.y
        # Dragging a NER read off a room label (2.13.0) onto its room answers
        # the "position" question that row was carrying.
        e.ner_asset.pending_validations = [
            v for v in (e.ner_asset.pending_validations or []) if not v.startswith("position:")]
    if "confirmed" in changes:
        e.confirmed_by = "user" if changes["confirmed"] else None
        if e.ner_asset is not None:
            e.link_status = "confirmed" if changes["confirmed"] else "auto_linked"
    if (e.kind in DEMAND_KINDS and tie_change is None and e.tie_source not in ("manual", "teap")
            and any(k in changes for k in ("x", "y"))):
        # A point placed from the TEAP (2.19.0) stands for that space's
        # wireless port wherever the person drags it; only a hand-made or
        # auto-proposed tie is looked at again after a move.
        tie = S.propose_wireless_tie(db, _project(db, project_id), sh.of(e), e.x, e.y)
        e.space_key = tie["space_key"] if tie else None
        e.tie_source = "auto"              # looked again; found something or not
        changes["space_key"] = e.space_key
        changes["tie_source"] = e.tie_source
    if moved_from is not None:
        # A move is audited as a move — from where to where — under the room's
        # own action when the marker is a NER, so the log reads the same as a
        # move of a register room (2.8.1; it used to be an "element.updated"
        # carrying only the destination).
        S.audit(db, project_id, "ner.moved" if e.ner_asset is not None else "element.moved",
                entity="ner_asset" if e.ner_asset is not None else "element",
                entity_id=e.ner_asset.id if e.ner_asset is not None else e.id,
                justification=justification or "moved on the drawing",
                payload={"element_id": e.id, "label": e.label, "from": moved_from,
                         "to": {"page": sh.of(e), "x": e.x, "y": e.y}})
        rest = {k: v for k, v in changes.items() if k not in ("x", "y")}
        if rest:
            S.audit(db, project_id, "element.updated", entity="element", entity_id=e.id,
                    justification=justification, payload=rest)
    else:
        S.audit(db, project_id, "element.updated", entity="element", entity_id=e.id,
                justification=justification, payload=changes)
    db.commit()

    # A marker that just became a NER is an equipment room the study is about,
    # so it enters the register now rather than at the next import.
    if "kind" in changes and changes["kind"] in migrate.ASSET_KINDS and not was_ner:
        migrate.backfill_assets(db, _project(db, project_id))
    return {"ok": True}


@router.delete("/projects/{project_id}/elements/{element_id}", status_code=204)
def delete_element(project_id: int, element_id: int, justification: str = "",
                   scope: str = Query("ner", pattern="^(ner|marker)$"),
                   db: Session = Depends(get_session)) -> Response:
    """Take a marker off the drawing.

    A NER marker is one drawing of a room the register holds, and the same
    room is often drawn on several sheets (a level plan and two details). From
    2.18.1 deleting it in the viewer deletes **the NER**: the room leaves the
    register — every page, the NERs list, the analysis, the export — in one
    act, the way *Remove* on the NERs screen does, and *Restore* brings every
    page back. Marcelo, 13 Oct 2026: *"se eu uso a opção delete de uma NR que
    aparece repetida em outras páginas, ela tem que ser apagada de todas as
    páginas e ela tem que sumir da lista de NERs."* Until then only this one
    marker went, the room stayed on the other sheets and in the list, and
    nothing told him.

    ``scope=marker`` keeps the old, narrower act for the one case that needs
    it — a duplicate drawing of a room on one sheet: this marker alone is
    deleted, and the NER is retired only when it was the room's last one.
    Every other kind of marker (TER, IDF, COW, DIVA, AP, references) is a
    row of its own and is simply deleted.
    """
    from .. import ner_register as R
    p = _project(db, project_id)
    e = db.get(Element, element_id)
    if e is None or e.project_id != project_id:
        raise HTTPException(404, "element not found")
    asset = e.ner_asset
    if e.locked or (asset is not None and asset.locked):
        raise HTTPException(409, "element is locked")

    if asset is not None and scope == "ner":
        if asset.deleted_at is None:
            sh = SH.for_project(db, p.id)
            pages = sorted({sh.of(o) for o in asset.occurrences})
            here = sh.of(e)
            try:
                R.soft_delete(db, p, asset, actor="user",
                              reason=justification or
                              (f"deleted in the Drawing viewer on {sh.label(here)}"
                               + ((f" — it was also drawn on page(s) "
                                   f"{', '.join(str(n) for n in pages if n != here)}"
                                   if not sh.multi else
                                   f" — it was also drawn on "
                                   f"{', '.join(sh.label(n) for n in pages if n != here)}")
                                  if len(pages) > 1 else "")))
            except R.RegisterError as exc:
                raise HTTPException(409, str(exc)) from exc
        # The markers stay in the table, hidden with their room, so Restore
        # puts the NER back on every sheet it was drawn on.
        return Response(status_code=204)

    from ..domain.pdf_ingest import DEMAND_KINDS
    wifi = e.kind in DEMAND_KINDS
    S.audit(db, project_id, "element.deleted", entity="element", entity_id=e.id,
            justification=justification,
            # The scope names what was asked of a NER marker; any other marker
            # is just this one (4.8.0: a Wi-Fi point was logged as "ner").
            payload={"label": e.label, "kind": e.kind,
                     "page": SH.for_project(db, project_id).of(e),
                     "scope": scope if asset is not None else "marker"}
            | ({"space_key": e.space_key} if wifi and e.space_key else {}))
    if wifi and e.space_key:
        # A person deleted this space's Wi-Fi point (4.8.0): Place Wi-Fi from
        # the TEAP leaves the space without one from now on — unless another
        # point is still tied to it.
        others = db.execute(select(Element.id).where(
            Element.project_id == project_id, Element.id != e.id,
            Element.scenario_id.is_(None), Element.space_key == e.space_key,
            Element.kind.in_(DEMAND_KINDS))).first()
        if others is None:
            S.decline_wifi_space(db, p, e.space_key)

    # The room the sheet prints there is still this NER (5.1.0, D25): the next
    # Place NERs from the drawing reads it as this one instead of creating a
    # new NER on the spot this marker held.
    if asset is not None:
        _sh = SH.for_project(db, project_id)
        S.release_spot(asset, e, f"its drawing on {_sh.label(_sh.of(e))} was deleted by hand "
                                 f"(Delete only this marker)")
    # Deleting the last drawing of a room must not leave the register holding a
    # NER nothing supports any more — but it must not silently drop it either.
    db.delete(e)
    db.flush()
    if asset is not None and not [o for o in asset.occurrences if o.id != e.id]:
        from ..models import KeepDecision, NerStatus
        import datetime as _dt

        asset.deleted_at = _dt.datetime.now(_dt.timezone.utc)
        asset.deleted_reason = (justification
                                or "last document occurrence of this NER was deleted")
        asset.status = NerStatus.REJECTED
        asset.keep_decision = KeepDecision.REJECT
        asset.updated_by = "user"
        S.audit(db, project_id, "ner.removed", entity="ner_asset", entity_id=asset.id,
                justification=asset.deleted_reason,
                payload={"code": asset.code, "cause": "last occurrence deleted"})
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Drawing spaces
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/drawing-spaces")
def list_drawing_spaces(project_id: int, page: Optional[int] = None,
                        db: Session = Depends(get_session)) -> list[dict]:
    _project(db, project_id)
    sh = SH.for_project(db, project_id)
    q = select(DrawingSpace).where(DrawingSpace.project_id == project_id)
    if page is not None:
        q = q.where(*_on_sheet(DrawingSpace, sh, page))
    # A key plan's rooms are not listed at all (2.13.0): not drawn on the
    # sheet, not searchable, not offered in the TEAP × drawing dropdown —
    # tying a TEAP line there sent people to the right room on the wrong page.
    key_pages = S.key_plan_pages(db, project_id)
    # Which TEAP lines each room is already tied to. The worklist offers the
    # rooms nobody is tied to first — that is where a TEAP line the matcher
    # could not place is most likely to be — without hiding the rest, because
    # two TEAP lines can rightly share one room.
    tied: dict[int, list[str]] = {}
    for m, t in db.execute(
            select(SpaceMatchRow, TeapSpace)
            .join(TeapSpace, TeapSpace.id == SpaceMatchRow.teap_space_id)
            .where(SpaceMatchRow.project_id == project_id,
                   SpaceMatchRow.drawing_space_id.is_not(None),
                   SpaceMatchRow.rejected.is_(False))).all():
        tied.setdefault(m.drawing_space_id, []).append(t.space_code or t.space_name)
    rows = [s for s in db.execute(q.order_by(DrawingSpace.page, DrawingSpace.name)).scalars()
            if sh.of(s) not in key_pages]
    return [{"id": s.id, "page": sh.of(s), "space_code": s.space_code, "name": s.name,
             "x": s.x, "y": s.y, "polygon": s.polygon, "area_label": s.area_label,
             "confidence": s.confidence, "confirmed": s.confirmed,
             "origin": s.origin, "evidence": s.evidence,
             "tied_to": sorted(tied.get(s.id, []))}
            for s in sorted(rows, key=sh.of)]


class SpacePatch(BaseModel):
    name: Optional[str] = None
    space_code: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    polygon: Optional[list] = None
    confirmed: Optional[bool] = None


@router.patch("/projects/{project_id}/drawing-spaces/{space_id}")
def update_drawing_space(project_id: int, space_id: int, body: SpacePatch,
                         db: Session = Depends(get_session)) -> dict:
    _project(db, project_id)
    s = db.get(DrawingSpace, space_id)
    if s is None or s.project_id != project_id:
        raise HTTPException(404, "space not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    S.audit(db, project_id, "drawing_space.updated", entity="drawing_space",
            entity_id=s.id, payload=body.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# TEAP
# ---------------------------------------------------------------------------

class TeapMappingIn(BaseModel):
    sheet: str
    header_row: int = Field(1, ge=1)
    mapping: dict[str, Optional[str]]
    venue_filter: Optional[str] = None
    save_as_template: Optional[str] = None
    #: Explicit opt-in to importing every venue in a master allocation plan.
    #: Without it a multi-venue sheet is refused, because importing 96 venues
    #: against one venue's drawings is never what anyone means.
    allow_all_venues: bool = False


class TeapPreviewIn(BaseModel):
    sheet: str
    header_row: int = Field(1, ge=1)
    mapping: dict[str, Optional[str]]


class TeapSpacePatch(BaseModel):
    criticality: Optional[str] = None
    criticality_confirmed: Optional[bool] = None
    demand_type: Optional[str] = None
    level: Optional[str] = None
    building: Optional[str] = None
    total_ports: Optional[float] = None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

class MatchUndo(BaseModel):
    justification: str = ""


class BulkMatchDecision(BaseModel):
    """Confirm or reject every match sharing one method and confidence band.

    After the page tie-break was fixed, the reference venue's review queue became
    21 rooms whose base code matched but whose sub-index differed (TEAP
    ``PRS.006.02`` against drawing ``PRS.006``). Each is a two-second judgement,
    but twenty-one separate clicks is the kind of friction that gets a review
    abandoned half-way. The user still decides — this only saves the clicking, and
    it records one audit entry per row exactly as the individual action does.
    """

    method: Optional[str] = None
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    max_confidence: float = Field(1.0, ge=0.0, le=1.0)
    confirmed: bool = True
    justification: str = ""


class MatchDecision(BaseModel):
    confirmed: Optional[bool] = None
    rejected: Optional[bool] = None
    drawing_space_id: Optional[int] = None
    #: Put the row back to whatever the matcher says. Needed because a missing
    #: ``drawing_space_id`` already means "do not touch the room".
    clear_room: bool = False
    justification: str = ""


# ---------------------------------------------------------------------------
# Zones, routes, shafts
# ---------------------------------------------------------------------------

class ZoneIn(BaseModel):
    page: int
    kind: str
    label: str = ""
    polygon: list[list[float]]
    notes: str = ""


#: The zone kinds. ``area`` (2.8.0) is a **named area drawn to point
#: something out** — "BTOC", "TER", the broadcast compound — illustrative
#: only: drawn on the sheet and in the marked-up PDF, never measured, never
#: counted, never a room a space is served from. The other four predate the
#: simplification of September 2026; their tools are gone from the viewer
#: but the rows still load.
ZONE_KINDS = ("area", "permitted", "forbidden", "technical", "photographer")


def _zone_dict(z: Zone, sh: Optional["SH.Sheets"] = None) -> dict:
    return {"id": z.id, "page": sh.of(z) if sh is not None else z.page,
            "kind": z.kind, "label": z.label, "polygon": z.polygon, "notes": z.notes}


@router.get("/projects/{project_id}/zones")
def list_zones(project_id: int, page: Optional[int] = None,
               db: Session = Depends(get_session)) -> list[dict]:
    _project(db, project_id)
    sh = SH.for_project(db, project_id)
    q = select(Zone).where(Zone.project_id == project_id)
    if page is not None:
        q = q.where(*_on_sheet(Zone, sh, page))
    rows = db.execute(q.order_by(Zone.page, Zone.id)).scalars().all()
    return [_zone_dict(z, sh) for z in sorted(rows, key=sh.of)]


@router.post("/projects/{project_id}/zones", status_code=201)
def create_zone(project_id: int, body: ZoneIn,
                db: Session = Depends(get_session)) -> dict:
    _project(db, project_id)
    if body.kind not in ZONE_KINDS:
        raise HTTPException(422, "kind must be area, permitted, forbidden, technical or photographer")
    if len(body.polygon) < 3:
        raise HTTPException(422, "a zone needs at least three points")
    if body.kind == "area" and not body.label.strip():
        raise HTTPException(422, "a named area needs a name")
    data = body.model_dump()
    data["label"] = data["label"].strip()
    sh = SH.for_project(db, project_id)
    data["drawing_id"], data["page"] = _split(sh, body.page)
    z = Zone(project_id=project_id, **data)
    db.add(z)
    db.flush()
    S.audit(db, project_id, "zone.created", entity="zone", entity_id=z.id,
            payload={"kind": z.kind, "label": z.label, "page": body.page})
    db.commit()
    return _zone_dict(z, sh)


class ZonePatch(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None
    polygon: Optional[list[list[float]]] = None


@router.patch("/projects/{project_id}/zones/{zone_id}")
def update_zone(project_id: int, zone_id: int, body: ZonePatch,
                db: Session = Depends(get_session)) -> dict:
    """Rename a named area, or move its outline (2.8.0)."""
    _project(db, project_id)
    z = db.get(Zone, zone_id)
    if z is None or z.project_id != project_id:
        raise HTTPException(404, "zone not found")
    changes = body.model_dump(exclude_none=True)
    if "label" in changes:
        changes["label"] = changes["label"].strip()
        if z.kind == "area" and not changes["label"]:
            raise HTTPException(422, "a named area needs a name")
    if "polygon" in changes and len(changes["polygon"]) < 3:
        raise HTTPException(422, "a zone needs at least three points")
    for k, v in changes.items():
        setattr(z, k, v)
    S.audit(db, project_id, "zone.updated", entity="zone", entity_id=z.id, payload=changes)
    db.commit()
    return _zone_dict(z, SH.for_project(db, project_id))


@router.delete("/projects/{project_id}/zones/{zone_id}", status_code=204)
def delete_zone(project_id: int, zone_id: int, db: Session = Depends(get_session)) -> Response:
    _project(db, project_id)
    z = db.get(Zone, zone_id)
    if z is None or z.project_id != project_id:
        raise HTTPException(404, "zone not found")
    S.audit(db, project_id, "zone.deleted", entity="zone", entity_id=z.id,
            payload={"kind": z.kind, "label": z.label,
                     "page": SH.for_project(db, project_id).of(z)})
    db.delete(z)
    db.commit()
    return Response(status_code=204)


#: The largest coordinate a route corner may have (5.3.0 rc4). PDF pages are
#: at most 14,400 points a side; a corner at 1e200 was accepted and made
#: every analysis of the project fail with OverflowError until it was deleted.
ROUTE_COORD_LIMIT = 1_000_000.0


def _sane_corner(*values: float) -> None:
    for c in values:
        if c != c or abs(c) > ROUTE_COORD_LIMIT:
            raise ValueError("a route corner must be a point on the sheet")


class RouteIn(BaseModel):
    page: int = Field(..., ge=1)
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "tray"
    label: str = ""

    @model_validator(mode="after")
    def _segment(self) -> "RouteIn":
        _sane_corner(self.x0, self.y0, self.x1, self.y1)
        if abs(self.x0 - self.x1) < 1e-6 and abs(self.y0 - self.y1) < 1e-6:
            raise ValueError("a route segment needs two different corners")
        return self


#: A route is one named path of segments (4.7.0): "R1", "R2" … per project.
_ROUTE_NAME_RX = re.compile(r"^R(\d+)$")


class RoutePathIn(BaseModel):
    """A route drawn in one go: corner after corner, double-click to end."""
    page: int = Field(..., ge=1)
    points: list[list[float]] = Field(..., min_length=2, max_length=500)
    kind: str = "tray"

    @field_validator("points")
    @classmethod
    def _pairs(cls, v: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for q in v:
            if len(q) != 2 or any(c != c or c in (float("inf"), float("-inf")) for c in q):
                raise ValueError("each corner is [x, y]")
            _sane_corner(*q)
            if out and abs(out[-1][0] - q[0]) < 1e-6 and abs(out[-1][1] - q[1]) < 1e-6:
                continue      # the second click of a double-click
            out.append([float(q[0]), float(q[1])])
        if len(out) < 2:
            raise ValueError("a route needs two different corners")
        return out


class VerticalIn(BaseModel):
    key: str
    lower_page: int = Field(..., ge=1)
    lower_x: float
    lower_y: float
    upper_page: int = Field(..., ge=1)
    upper_x: float
    upper_y: float
    #: A shaft or riser is metres long, not kilometres (rc6: Infinity and
    #: 1e-300 were accepted, and a foot at 1e200 broke every analysis).
    length_m: float = Field(..., gt=0.01, le=500)
    kind: str = "shaft"
    label: str = ""

    @model_validator(mode="after")
    def _feet(self) -> "VerticalIn":
        _sane_corner(self.lower_x, self.lower_y, self.upper_x, self.upper_y)
        if (self.lower_page == self.upper_page and abs(self.lower_x - self.upper_x) < 1e-6
                and abs(self.lower_y - self.upper_y) < 1e-6):
            raise ValueError("a vertical link needs two different feet")
        return self


# ---------------------------------------------------------------------------
# Coverage, optimisation, scenarios
# ---------------------------------------------------------------------------

def _analysis_payload(db: Session, p: Project, result, ners, sc) -> dict:
    return {"scenario_id": sc.id, "summary": result.summary(),
            "assignments": [a.to_dict() for a in result.assignments],
            "ner_load": result.ner_load, "exceptions": result.exceptions,
            "redundancy": S.redundancy(db, p, result, ners),
            "disclaimer": settings.disclaimer}


# The optimiser stood here: a CP-SAT model that opened new equipment rooms on a
# grid of candidate positions and reported how much better its answer was than
# the drawing.
#
# It has been removed, and the reason is not that it worked badly. It is that
# the thing it optimised — distance on a plan — is a fraction of what decides
# where a NER goes. Power, cooling, physical space, security, access for the
# cabling, A/B independence and the continuity plan are not in the model, so its
# answer was always a proposal somebody then had to argue with using facts the
# tool could not see. Presented as an optimum, it made that argument harder.
#
# New rooms are placed by hand in the drawing viewer. The tool's job is to say
# what each placement costs — the distances, what falls outside the limits, what
# nothing reaches — which is exactly what the coverage analysis does.

# ``/compare`` compared two scenarios. There is one design now, so there is
# nothing to compare it against.

# ---------------------------------------------------------------------------
# Who serves what
# ---------------------------------------------------------------------------

class AssignIn(BaseModel):
    """Choose the equipment room that serves one space, or hand it back."""

    ner_asset_id: Optional[int] = None      # None clears the choice
    justification: str = ""


class CrossLevelIn(BaseModel):
    """Declare (or withdraw) that the room chosen for a space is on another level."""

    cross_level: bool
    justification: str = ""


def _alternatives(a: dict, labels: dict[str, Optional[str]]) -> list[dict]:
    """The rooms a stored assignment lists as alternatives, named and without
    the room that serves it. Keys are ``A<asset id>`` since the register; an
    older key that is not is skipped rather than guessed."""
    out = []
    for alt in a.get("alternatives") or []:
        key = alt.get("ner_key") or ""
        if key == a.get("ner_key") or not key.startswith("A") or not key[1:].isdigit():
            continue
        out.append({"ner_asset_id": int(key[1:]), "label": labels.get(key) or key,
                    "distance_m": alt.get("distance_m")})
    return out


def _group_of_row(row: dict) -> dict:
    """The pile a Demand row belongs to, and the one-line reason.

    Read from the row as it stands — the room it is tied to *now*, the
    status the last run gave it — so the group is right the moment a person
    ties a room or chooses a NER, before the next run catches up:

    * no room on the drawing and no NER chosen → pending, "no room on the
      drawing";
    * no room on the drawing, NER chosen → *served — not measured* (the run
      will count it; until then the row already says so);
    * a room, no run yet → pending, "not analysed yet";
    * otherwise the group of the run's status, with the status in words.
    """
    status = row.get("status")
    has_room = bool(row.get("matched_code") or row.get("matched_name")) \
        and not row.get("match_rejected")
    chosen = row.get("assigned_ner_asset_id") or row.get("teap_ner_asset_id")
    if not has_room:
        if chosen:
            g = Group.NOT_MEASURED
            reason = STATUS_LABEL_TEXT[Status.ASSIGNED_NOT_PLACED]
            if status == Status.INSUFFICIENT_CAPACITY.value:
                g, reason = Group.PROBLEM, STATUS_LABEL_TEXT[Status.INSUFFICIENT_CAPACITY]
        else:
            g, reason = Group.PENDING, PENDING_NO_ROOM
    elif not status:
        g, reason = Group.PENDING, PENDING_NOT_ANALYSED
    else:
        g = group_of(status)
        try:
            reason = STATUS_LABEL_TEXT[Status(status)]
        except ValueError:
            reason = status
    return {"group": g.value, "group_label": GROUP_LABEL[g], "group_reason": reason}


def _sheet_numbers(db: Session, project_id: int) -> "SH.PageMeta":
    """Sheet key → drawing number (4.1.0: by sheet, so each book's page 2 has
    its own)."""
    sh = SH.for_project(db, project_id)
    return SH.PageMeta(sh, {sh.key(pg.drawing_id, pg.number): (pg.sheet_number or "")
                            for pg in db.execute(select(Page).join(Drawing).where(
                                Drawing.project_id == project_id)).scalars()})


def _link_dict(ln: SpaceOccurrenceLink, other: DrawingSpace,
               sheet_numbers: dict[int, str]) -> dict:
    return {"id": ln.id, "drawing_space_id": other.id,
            "page": SH.key_of(sheet_numbers, other),
            "sheet": SH.meta_of(sheet_numbers, other) or None,
            "code": other.space_code, "name": other.name,
            "x": other.x, "y": other.y,
            "verdict": ln.verdict, "auto": bool(ln.auto), "method": ln.method,
            "level_basis": ln.level_basis,
            "evidence": list(ln.evidence or []), "decided_by": ln.decided_by,
            "decided_at": ln.decided_at.isoformat() if ln.decided_at else None}


def _wireless_rows(db: Session, p: Project, sc: Optional[Scenario],
                   sheet_numbers: dict[int, str]) -> list[dict]:
    """Every wireless point on the drawing, with what the last run said of it.

    Read from the markers, not from the run, so a point placed after the last
    analysis is on the list — saying "not analysed yet" — instead of missing.
    """
    last: dict[str, dict] = {}
    if sc is not None and isinstance(sc.result, dict):
        for a in sc.result.get("assignments") or []:
            if a.get("kind") == "wireless_point":
                last[a.get("space_key")] = a
    from ..domain.pdf_ingest import DEMAND_KINDS, WIRELESS_NAME

    space_names = {t.space_key: t.space_name for t in db.execute(
        select(TeapSpace).where(TeapSpace.project_id == p.id)).scalars()}
    pins, sources = S.pin_map(db, p)
    asset_names = {a.id: a.display_name for a in db.execute(
        select(NerAsset).where(NerAsset.project_id == p.id)).scalars()}
    out = []
    for e in db.execute(select(Element).where(
            Element.project_id == p.id, Element.scenario_id.is_(None),
            Element.kind.in_(DEMAND_KINDS)).order_by(Element.page, Element.id)).scalars():
        a = last.get(S.wireless_point_key(e.id)) or {}
        # What the point follows when nobody chose for it: the room fixed
        # for its TEAP space (2.11.0), named so the select can say so.
        space_pin = pins.get(e.space_key) if e.space_key else None
        inherited = (asset_names.get(int(space_pin[1:])) if space_pin else None)
        ev = a.get("evaluation") or {}
        out.append({
            "id": e.id, "label": e.label, "page": SH.key_of(sheet_numbers, e),
            "x": e.x, "y": e.y,
            "kind": e.kind, "ap_kind": WIRELESS_NAME.get(e.kind, "Wi-Fi"),
            "environment": e.environment,
            "space_key": e.space_key, "tie_source": e.tie_source,
            "space_name": space_names.get(e.space_key) if e.space_key else None,
            "sheet": SH.meta_of(sheet_numbers, e) or None,
            # No port of its own since 4.0.0: the DWISA line of its space is.
            "ports": a.get("ports_required", 0),
            "serving_ner": a.get("ner_label"), "status": a.get("status"),
            "distance_m": ev.get("effective_distance_m"), "limit_m": ev.get("limit_m"),
            "nearest_ner": a.get("nearest_label"),
            "nearest_distance_m": a.get("nearest_distance_m"),
            "margin_m": ev.get("margin_m"),
            "alternatives": _alternatives(a, {f"A{k}": v for k, v in asset_names.items()}),
            "notes": list(a.get("reasons") or []),
            "assigned_ner_asset_id": e.assigned_ner_asset_id,
            "assigned_ner": asset_names.get(e.assigned_ner_asset_id) if e.assigned_ner_asset_id else None,
            "space_ner": inherited,
            # rc6 (Marcelo, WIFI-55 on VBB): the run gave the point its
            # space's NER, not the nearest one - the select must say so.
            "follows_space": bool(e.space_key and a.get("pinned")
                                  and not e.assigned_ner_asset_id),
            "space_ner_source": sources.get(e.space_key) if space_pin else None,
        })
    return out


class WirelessServeIn(BaseModel):
    #: ``None`` releases the choice: the point follows its TEAP space's room
    #: again, or the nearest-NER rule.
    ner_asset_id: Optional[int] = None
    justification: str = ""


class OccurrenceVerdictIn(BaseModel):
    #: "confirmed" — it is the same space; "rejected" — it is not;
    #: "proposed" — take my answer back and let the rule decide again.
    verdict: str
    justification: str = ""


def _same_room_key(value: Optional[str]) -> str:
    """``TEC.004-02``, ``tec.004.02`` and ``TEC 004 02`` are one code."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _matched_to(r: TeapSpace, m: Optional[SpaceMatchRow], ds: Optional[DrawingSpace],
                sheet_numbers: dict[int, str]) -> dict:
    """The room on the drawing this TEAP line points at, and whether that is a
    redirection.

    ``redirected`` is true when the drawing room's code (or, failing a code,
    its name) is not the TEAP's own — the TEAP says ``PRS.001.03`` and the
    room it was tied to is labelled ``PRS.001.02``. That is the "from → to"
    the Demand screen shows; an identical code is not worth an arrow.
    """
    if ds is None:
        return {"matched_code": None, "matched_name": None, "matched_page": None,
                "matched_x": None, "matched_y": None,
                "matched_sheet": None, "match_confirmed": bool(m.confirmed) if m else None,
                "match_rejected": bool(m.rejected) if m else None,
                "match_method": m.method if m else None,
                "match_by": None, "redirected": False}
    if ds.space_code and r.space_code:
        same = _same_room_key(ds.space_code) == _same_room_key(r.space_code)
    elif ds.space_code and not r.space_code:
        same = _same_room_key(ds.space_code) == _same_room_key(r.space_name)
    else:
        same = _same_room_key(ds.name) == _same_room_key(r.space_name) or (
            bool(r.space_code) and _same_room_key(ds.name) == _same_room_key(r.space_code))
    return {
        "matched_code": ds.space_code, "matched_name": ds.name,
        "matched_page": SH.key_of(sheet_numbers, ds),
        # Where the room is on that sheet, for the page button (4.6.0).
        "matched_x": ds.x, "matched_y": ds.y,
        "matched_sheet": SH.meta_of(sheet_numbers, ds) or None,
        "match_confirmed": bool(m.confirmed) if m else None,
        "match_rejected": bool(m.rejected) if m else None,
        "match_method": m.method if m else None,
        # Who tied it: the person who locked the row, or the matcher.
        "match_by": (r.locked_by or "you") if r.locked_verdict == "assigned" else None,
        "redirected": not same,
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/reports/{fmt}")
def build_report(project_id: int, fmt: str, scenario: str = "As-Is",
                 background: bool = Query(False),
                 legend: bool = Query(True),
                 lines: bool = Query(True),
                 db: Session = Depends(get_session)) -> dict:
    """Build a report, in the request or as a job.

    The marked-up PDF is minutes of work on a full venue book, and it used to
    run inside the request on a single-worker server: nothing else could be
    served while it ran, and from the browser a server frozen for two and a
    half minutes is indistinguishable from a dead one. Run it as a job and the
    application stays answerable, with a progress bar saying which page it is
    on instead of a button that appears to do nothing.
    """
    p = _project(db, project_id)
    if fmt != "pdf":
        # RackReach Map (1.0.0): the report is the marked-up map. There is
        # no TEAP, so no analysis workbook and no coverage CSV.
        raise HTTPException(422, "the only report is the marked-up PDF")
    if background:
        def work(ctx, jdb: Session) -> dict:
            return _build_report_now(jdb, jdb.get(Project, project_id), fmt,
                                     scenario, progress=ctx.progress,
                                     draw_legend=legend, draw_links=lines)
        return {"job_id": job_manager.submit(kind="report.build", project_id=p.id,
                                             fn=work, db=db), "background": True}
    return _build_report_now(db, p, fmt, scenario, draw_legend=legend,
                             draw_links=lines)


def _marked_pdf(src: str, out: str, *, progress=None, **kw) -> list:
    """Mark up one book in a child interpreter (5.3.0) and return the
    positions found off their sheet. Falls back to this process when the
    arguments cannot be sent across - slower is better than no PDF."""
    try:
        res = isolate.run("app.domain.reports.export_marked_pdf_job", src, out,
                          progress=progress, **kw)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        logging.getLogger("ner.reports").warning(
            "marked PDF in-process (%s: %s)", type(exc).__name__, exc)
        from ..domain.reports import export_marked_pdf_job
        res = export_marked_pdf_job(src, out, progress=progress, **kw)
    return list(res.get("off_page") or [])


def _build_report_now(db: Session, p: Project, fmt: str, scenario: str,
                      progress=None, draw_legend: bool = True,
                      draw_links: bool = True) -> dict:
    # RackReach Map (1.0.0): no TEAP, no matching, no analysis. The PDF is
    # the book with what is drawn on it — the NERs with their reach circles,
    # the other markers, the Wi-Fi points and the named areas.
    from ..domain.coverage import CoverageResult
    result = CoverageResult(assignments=[], ner_load={}, exceptions=[], uncovered=[])
    ners = S.register_ners(db, p)
    draw_links = False
    refreshed = None
    from_snapshot = False
    nothing_matched = False
    scales = S.scale_map(db, p.id)
    out_dir = settings.exports_dir / f"project_{p.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{p.name.replace(' ', '_')}_{scenario.replace(' ', '_')}"

    off_page: list = []
    if fmt == "pdf":
        if not p.drawings:
            raise HTTPException(422, "no drawing to mark up")
        out = out_dir / f"{stem}_marked.pdf"
        # The picture shows every room that has a position; the numbers above
        # still counted only the NERs. The visual-only markers — TER, IDF, COW,
        # DIVA node — come last: they are on the sheet so the sheet shows what
        # is in the building, and the legend now has a row for each of them.
        drawn = list(ners) + S.rooms_to_draw(db, p, ners) + S.markers_to_draw(db, p)
        sh = SH.for_project(db, p.id)
        common = dict(coverage=result, ners=drawn, scale_for_page=scales,
                      rules=S.distance_rules(p), scenario=scenario,
                      coverage_available=not nothing_matched,
                      draw_legend=draw_legend, draw_links=draw_links,
                      areas=S.areas_to_draw(db, p))
        if not sh.multi:
            off_page.extend(_marked_pdf(p.drawings[0].stored_path, str(out),
                                        progress=progress, **common))
        else:
            # Several books (4.1.0): each is marked with its own sheets — its
            # page n is sheet ``sh.key(book, n)`` — and the marked books are
            # put one after the other in one file, in book order.
            import pymupdf
            books = sorted(p.drawings, key=lambda d: d.id)
            # Every sheet's name, for the cross-sheet notes on any book.
            labels = {sh.key(b.id, n): sh.label(sh.key(b.id, n))
                      for b in books for n in range(1, (b.page_count or 0) + 1)}
            parts = []
            try:
                for i, d in enumerate(books):
                    part = out_dir / f"{stem}_marked.part{i}.pdf"

                    def _prog(frac, msg, _i=i, _name=d.filename):
                        if progress is not None:
                            progress((_i + frac) / len(books), f"{_name}: {msg}")
                    # The legend box goes on the first page of the file only
                    # (4.3.0): the first book's first page.
                    off_page.extend(_marked_pdf(
                        d.stored_path, str(part), progress=_prog,
                        legend_page=1 if i == 0 else None,
                        page_keys={n: sh.key(d.id, n)
                                   for n in range(1, (d.page_count or 0) + 1)},
                        sheet_labels=labels, **common))
                    parts.append(part)
                merged = pymupdf.open()
                for part in parts:
                    with pymupdf.open(str(part)) as src:
                        merged.insert_pdf(src)
                merged.save(str(out), garbage=3, deflate=True)
                merged.close()
            finally:
                for part in parts:
                    part.unlink(missing_ok=True)
    S.audit(db, p.id, "report.generated", payload={"format": fmt, "file": out.name,
                                                   "off_page": len(off_page),
                                                   "legend": draw_legend,
                                                   "lines": draw_links})
    db.commit()
    body = {"file": out.name,
            "download": f"/api/projects/{p.id}/reports/download/{out.name}",
            "scenario": scenario,
            "source": "drawing"}
    if refreshed is not None:
        body["analysis_refreshed"] = refreshed
        body["note"] = ("The analysis was run again before this export: "
                        f"{refreshed['changes']} change(s) came after the last run.")
    if off_page:
        # Drawn anyway — a marker missing from an export with no explanation is
        # worse than one visibly in the wrong place — but never silently.
        body["off_page"] = off_page[:50]
        body["off_page_count"] = len(off_page)
        body["warning"] = (
            f"{len(off_page)} item(s) sit outside their page and were marked "
            f"where they are stored. This means the drawing was imported before "
            f"the page-rotation correction: re-import it to reposition them.")
    return body


@router.get("/projects/{project_id}/reports/download/{filename}")
def download_report(project_id: int, filename: str,
                    db: Session = Depends(get_session)) -> FileResponse:
    _project(db, project_id)
    safe = Path(filename).name
    path = settings.exports_dir / f"project_{project_id}" / safe
    if not path.exists():
        raise HTTPException(404, "report not found")
    return FileResponse(path, filename=safe)


@router.get("/projects/{project_id}/audit")
def audit_log(project_id: int, limit: int = Query(200, le=2000),
              db: Session = Depends(get_session)) -> list[dict]:
    _project(db, project_id)
    # The id breaks the tie. Windows' clock resolution is coarse enough that a
    # whole bulk decision — which stamps every row with one timestamp — can land
    # in the same tick as the click before it, and SQLite then returns those rows
    # in whatever order it likes: the screen that says who decided what, in which
    # order, would be showing an order that is not the one things happened in.
    rows = db.execute(select(AuditLog).where(AuditLog.project_id == project_id)
                      .order_by(AuditLog.at.desc(), AuditLog.id.desc())
                      .limit(limit)).scalars().all()
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id,
             "justification": a.justification, "payload": a.payload} for a in rows]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_session)) -> dict:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    return job_to_dict(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_session)) -> dict:
    """Ask a running job to stop at its next checkpoint.

    Cancellation is cooperative: the job stops where it next reports progress,
    which is at worst one page or one solver phase away. Nothing is left
    half-written because every job commits only at the end of its own step.
    """
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    if row.state not in ("pending", "running"):
        return {"cancelled": False, "state": row.state,
                "detail": f"the job already finished with state '{row.state}'"}
    ok = job_manager.cancel(job_id)
    return {"cancelled": ok, "state": row.state}


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: int, limit: int = Query(20, le=200),
              db: Session = Depends(get_session)) -> list[dict]:
    _project(db, project_id)
    rows = db.execute(select(Job).where(Job.project_id == project_id)
                      .order_by(Job.created_at.desc()).limit(limit)).scalars().all()
    return [job_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Aliases — the curated TEAP -> drawing dictionary
# ---------------------------------------------------------------------------

class AliasIn(BaseModel):
    kind: str = Field(..., pattern="^(code|name)$")
    teap_value: str
    drawing_value: str
    note: str = ""
    #: NULL project means the alias applies to every project. The space-code
    #: taxonomy is shared across venues, so a mapping learned once is worth reusing.
    global_scope: bool = False
    demand_hint: float = 0.0


class AliasImportIn(BaseModel):
    aliases: list[AliasIn]
    as_global: bool = True


# ---------------------------------------------------------------------------
# The VDB comments tracker reader stood here.
#
# It read sentences out of the comments column of a tracker spreadsheet
# ("Critical NER to be located inside the Technical Operation Center BRS.002-A")
# and wrote them into the register as proposed rooms — five of the fourteen with
# no position at all, because the space they named was not on the drawing yet.
#
# Removed with the solver, for the same reason. A NER that is not on a sheet is
# a drawing job, not a register row. The tracker is still where the requirement
# is recorded and discussed; when it is agreed, the room is drawn on the plan,
# and that drawing is what this application reads.
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/ner-assets/positions")
def ner_asset_positions(project_id: int, page: Optional[int] = None,
                        db: Session = Depends(get_session)) -> dict:
    """Every register room that has a position, and whether the drawing shows it.

    The viewer draws ``Element`` rows — markers the extractor found on the PDF.
    That was the whole picture for as long as every NER came from the sheet, and
    it stopped being the whole picture the moment the register could hold a room
    the sheet does not: a solver suggestion, or a proposal read from a comments
    tracker. Those are real rows with real positions, and they were invisible on
    the one screen where position is the point.

    So this returns the register's own view. ``on_drawing`` rows already reach
    the viewer as elements and are returned for completeness; ``proposed`` rows
    are the ones it has to draw itself.

    ``unplaced`` is reported as a count, never as a coordinate. A proposal whose
    room is not on this drawing set yet has no position, and inventing one for
    the sake of showing something is the failure this whole feature is built to
    avoid. The viewer says how many are waiting and sends you to the register.
    """
    from ..models import KeepDecision, NerStatus
    _project(db, project_id)
    drawn_asset_ids = {e.ner_asset_id for e in db.execute(
        select(Element).where(Element.project_id == project_id,
                              Element.ner_asset_id.is_not(None))).scalars()}

    sh = SH.for_project(db, project_id)
    rows, unplaced = [], []
    for a in db.execute(select(NerAsset)
                        .where(NerAsset.project_id == project_id)).scalars():
        if a.deleted_at is not None or a.merged_into_id is not None:
            continue
        if a.keep_decision == KeepDecision.REJECT or a.status == NerStatus.REJECTED:
            continue
        if not a.position_known:
            unplaced.append({
                "id": a.id, "code": a.code, "name": a.display_name,
                "critical": a.kind == ElementKind.NER_CRITICAL,
                "why": (a.pending_validations or [""])[0],
            })
            continue
        if page is not None and sh.of_asset(a) != page:
            continue
        rows.append({
            "id": a.id, "code": a.code, "name": a.display_name,
            "page": sh.of_asset(a), "x": a.canonical_x, "y": a.canonical_y,
            "kind": a.kind, "critical": a.kind == ElementKind.NER_CRITICAL,
            "status": a.status, "origin": a.origin,
            "position_source": a.position_source,
            "keep_decision": a.keep_decision,
            # The viewer needs this to decide whether the marker can be dragged.
            "locked": a.locked, "movable": a.movable,
            "classification_confirmed": a.classification_confirmed,
            # What the viewer keys its two layers on.
            "on_drawing": a.id in drawn_asset_ids,
            "proposed": a.status == NerStatus.SUGGESTED,
            "space_name": a.space_name, "space_code": a.space_code,
            "notes": a.notes,
            "why": (a.suggestion or {}).get("comment")
                   or (a.suggestion or {}).get("reason") or "",
            "pending_validations": a.pending_validations or [],
        })
    return {"positions": rows, "unplaced": unplaced,
            "unplaced_count": len(unplaced)}


class NerPositionIn(BaseModel):
    page: int = Field(..., ge=1)
    x: float
    y: float
    reason: str = ""

    @model_validator(mode="after")
    def _on_sheet(self) -> "NerPositionIn":
        _sane_corner(self.x, self.y)          # rc6: 1e200 accepted, NaN gave 500
        return self


@router.patch("/projects/{project_id}/ners/{ner_id}/position")
def move_ner_asset(project_id: int, ner_id: int, body: NerPositionIn,
                   db: Session = Depends(get_session)) -> dict:
    """Move a register room on the drawing.

    A NER that came off the sheet is moved by dragging its marker, which writes
    back to the marker. A room the sheet does not carry has no marker to drag —
    a solver suggestion, a tracker proposal, or either of those once accepted —
    and until now there was no way to move it at all. Accepting a proposal makes
    it part of the design; a part of the design you cannot reposition is not
    much of a design.

    The move is recorded as a human act: ``position_source`` becomes "manual",
    so the row stops claiming to sit where the tracker or the solver put it. A
    locked room refuses, which is what the lock is for.
    """
    from ..models import KeepDecision, NerStatus
    _project(db, project_id)
    a = db.get(NerAsset, ner_id)
    if a is None or a.project_id != project_id:
        raise HTTPException(404, "NER not found")
    if a.locked:
        raise HTTPException(400, "this NER is locked — unlock it before moving it")

    sh = SH.for_project(db, project_id)
    before = {"page": sh.of_asset(a), "x": a.canonical_x, "y": a.canonical_y,
              "position_source": a.position_source,
              "position_known": a.position_known}
    did, page = _split(sh, body.page)
    a.canonical_page = page
    if did is not None:
        a.canonical_drawing_id = did
    a.canonical_x = body.x
    a.canonical_y = body.y
    a.position_source = "manual"
    # Placing a room that had no position is the answer to the question its
    # pending validation was asking, so that validation goes.
    a.position_known = True
    a.pending_validations = [
        v for v in (a.pending_validations or [])
        if "not on this drawing set yet" not in v
        and "place this one by hand" not in v
        and "choose the room" not in v]
    S.audit(db, project_id, "ner.moved", entity="ner_asset", entity_id=a.id,
            justification=body.reason or "moved on the drawing",
            payload={"from": before, "to": {"page": body.page, "x": body.x,
                                            "y": body.y}})
    db.commit()
    return {"ok": True, "id": a.id, "page": sh.of_asset(a),
            "x": a.canonical_x, "y": a.canonical_y,
            "position_source": a.position_source}
