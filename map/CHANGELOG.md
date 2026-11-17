# Changelog — RackReach Map

Every release is numbered with **semantic versioning**, `MAJOR.MINOR.PATCH`:
PATCH for a correction, MINOR for something new that keeps every project
valid, MAJOR for a change an installation cannot absorb silently.

The number lives in one file, `VERSION`. To release: edit `VERSION`, the
`"version"` in `frontend/package.json`, add an entry here, `npm run build` in
`frontend`, put the number in the mastheads of `docs/MANUAL.html`,
`docs/INSTALL.html`, `docs/QUICK-INSTALL.html` and `docs/HISTORY.html`, run the
tests, build the package.

## 1.3.0 — 2026-11-17

From RackReach 5.7.0, what concerns the map study:
- **Notes on the racks.** The inspector of the Drawing viewer has the rack's
  `Notes`; with the pointer over a rack that has one, the note shows beside it
  on every sheet. On the composite map a rack with a note carries a small pale
  dot, shows its note under the pointer, and the note is written in the
  selection panel too. Report: **with rack notes** (off by default) prints each
  note under its rack on the marked-up PDF and on the composite map PDF,
  wrapped, on every sheet the rack is on.
- **The level of a sheet by hand.** On the Scale screen, a detail sheet whose
  title states no level has a `level?` box (`02`, `B1`, `LEVEL 03`); its racks
  with no level take it. A level the title states is never changed.
- A rack placed by hand takes the rename rules (80 characters, no control
  character, no leading = + - @).
- The name-only labels an import leaves out are not added back at the next
  start; a book that got them goes through the space rules once more.
- Wi-Fi points: a new point given a deleted point's number no longer inherits
  its drags.
- A book that cannot be marked up answers in words, naming the file; an
  export that cannot be saved because the last one is still open in a PDF
  reader says so.
- rc2 (findings of the independent tester on rc1, and Marcelo, 17 Nov 2026):
  a **Notes (n)** button on the Drawing viewer, off each time it opens, keeps
  every rack's note written under it on the sheet; a note keeps its en dashes,
  curly quotes, euro signs and arrows on the PDFs and is moved in, or above
  the rack, at the edge of a sheet instead of being cut; `2` is written
  `LEVEL 02` and `LEVEL 2` / `LEVEL 02` are one level; a title block read at
  start-up that states a level replaces a level typed by hand on that sheet;
  a level typed on a sheet of the second book no longer reaches a rack of the
  first book.
- rc3 (findings of the independent tester on rc2): `LEVEL -1` (a basement) is
  no longer level 1; the composite map groups `LEVEL 2` and a typed `2` as one
  map; a locked PDF of several books or of the composite map is said in
  words, and a book that cannot be read is not called a locked export; a
  check mark, a double arrow or CJK in a note is drawn in a fallback font, an
  emoji as "?"; a note taller than the sheet is written wider, then smaller;
  the `Notes (n)` button counts the racks of the sheet open, keeps the note on
  the sheet at every edge, and the register racks "not on the sheet" show
  theirs; a level with a control character, or on a key plan, is refused.

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
