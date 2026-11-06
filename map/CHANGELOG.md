# Changelog — RackReach Map

Every release is numbered with **semantic versioning**, `MAJOR.MINOR.PATCH`:
PATCH for a correction, MINOR for something new that keeps every project
valid, MAJOR for a change an installation cannot absorb silently.

The number lives in one file, `VERSION`. To release: edit `VERSION`, the
`"version"` in `frontend/package.json`, add an entry here, `npm run build` in
`frontend`, put the number in the mastheads of `docs/MANUAL.html`,
`docs/INSTALL.html`, `docs/QUICK-INSTALL.html` and `docs/HISTORY.html`, run the
tests, build the package.

## 1.2.0 — 2026-11-05

MINOR: the 900-series master plans, carried over from RackReach 5.5.0.

- Import and rack placing read the strokes one at a time and keep only those
  that can be the rack block symbol or a label's arrow; a leader walk that
  reaches the edge of the window kept widens it, then reads the sheet whole,
  so what is found never differs from the full read (checked on the 34 real
  books with the full edition's code, which is the same).
- A sheet whose longest side is above about 38 in is drawn in the Drawing
  viewer from tiles the server cuts at the zoom on screen, kept with the
  composite maps and deleted with the book or the project. It opens fitted;
  `Fit` fits it again; the wheel zooms out further (0.005). Ordinary sheets
  are drawn as before.
- The page picture endpoint draws at most 6,000 px on the longest side.
- Tiles are drawn by a **renderer process** of their own, which keeps the
  open books and their display lists; the server only waits on a pipe. MuPDF
  holds Python's interpreter lock for the whole of a tile, so slow tiles drawn
  in the server held every other request (sign-in 32 s, `/healthz` 37 s
  behind a burst on a pathological sheet). A book that crashes MuPDF takes the
  renderer down, not the server; the next tile starts a new one.
  `NER_INPROCESS=1` draws in the server, as the import does.
- Tile requests wait on threads of their own (`api/tile_runner.py`), are
  guarded without holding a database connection, and a request gone before
  its turn gets nothing drawn; the sign-in check of every request runs off
  the event loop and `/healthz` answers on it. Forty tiles asked at once used
  to hold every database connection and every thread (found by the
  independent testers on the first and third candidates).
- Cancelling an import or *Place racks* while a sheet's strokes are read no
  longer crashes the server when the book is read in-process (an exception
  cannot cross MuPDF's C++ loop: it is now held and raised after it), and a
  cancelled *Place racks* no longer records "0 rooms found" for the book.
- A book or project being removed serves no tile; a tile request already
  waiting can no longer open the deleted PDF again or write its tiles back.
- The progress bar of an import and of *Place racks* moves while a sheet is
  read, with the stage in words ("reading page 2 of 4: 180,000 strokes
  read"); it used to move once per sheet, and a master plan sheet takes tens
  of seconds.
- **Public edition: the last internal words.** Rack names in the help text
  read `RKc_01.1` / `RK_20.1`, room codes of the project the tool was built
  for are no longer named, and the reasons, notes, evidence and errors the
  server writes reach the screens in the public words too
  (`app/public_words.py`). Portuguese says *o rack*, not *a rack*.
- **From RackReach 5.5.0-rc5:** a tile already on disk is read at once; a
  tile whose screen was closed is not drawn; the renderer gets 45 s per tile,
  then it is replaced, so a hung renderer no longer holds every tile and every
  removal; removals go first; the isolated reader sets its import path before
  it reads its request (Mac); the viewer's notice, first fit and the view after
  a tiled sheet are right.
- **From RackReach 5.5.0-rc6:** a slow tile says so on the sheet and is asked
  again by itself; a removal takes the tile lock once.

## 1.1.0 — 2026-11-05

MINOR: the map-only edition is **RackReach Map**, beside RackReach. It speaks
in general terms (rack, equipment room, design book, AV network) and names the
racks it creates `RKc_01.1` and `RK_20.1`. Nothing else changes: no screen,
no rule, no project. The desktop shortcut is now **RackReach Map**.

## 1.0.0 — 2026-11-05

First version of the **NER Map Analyzer**, the map-only edition of the rack
Spatial Coverage Analyzer, built from its source at version 5.3.0.

**What it is.** A visual study of the venue design books (design book): import the
books, confirm the scales, place racks from the drawing or by hand, draw equipment rooms,
IDFs, COWs, AV network nodes, Wi-Fi points and named areas, measure on the sheet's
scale, look at the reach circles, and export the marked-up PDF and the
composite map PDF. Nothing is counted and the demand list is not read.

**Kept from 5.3.0.** Projects (with project packages, between Map Analyzer
installations only), Import of the books (several series per project), Scale
with key plans and `Place racks from the drawing`, the Drawing viewer with
search, the composite map, the marked-up PDF with the legend on the first
page, accounts and the audit log, the installers (Windows and macOS) with the
watchdog.

**Removed.** demand list import and mapping templates; Demand × drawing; Demand per
space; the Wi-Fi step (Place Wi-Fi from the demand list); Exceptions; the coverage
analysis and `Run analysis`; the analysis workbook (XLSX) and the coverage
CSV; the `route` and `vertical` tools; the reference tools (`*rack★`, `*rack`,
`*equipment room`, `*COW`, `*AV network`); the naming reference panel; the criticality matrix
endpoints; the routing slack factor, near-limit threshold and fibre-run
settings. The racks screen is reduced to name, criticality, pages, delete and
restore.

**Changed.** Every tool works on a key plan too (a key plan is still skipped
by `Place racks from the drawing` and its rooms are not read). The reach
circles are drawn at the limit itself, a straight line on the sheet. The PDF
legend lists the two reach circles instead of the reference and
"match not confirmed" rows.

**Separate installation.** Folder `%LOCALAPPDATA%\Programs\ner-map-analyzer`,
address `http://localhost:8001`, scheduled tasks `NER Map Analyzer` and
`NER Map Analyzer watchdog`, desktop shortcut `NER Map Analyzer`, macOS label
`com.marcelobarbosa.ner-map-analyzer`. It never touches an installation of the
RackReach, and a project package of one is refused by the
other.
