# Changelog — RackReach Map

Every release is numbered with **semantic versioning**, `MAJOR.MINOR.PATCH`:
PATCH for a correction, MINOR for something new that keeps every project
valid, MAJOR for a change an installation cannot absorb silently.

The number lives in one file, `VERSION`. To release: edit `VERSION`, the
`"version"` in `frontend/package.json`, add an entry here, `npm run build` in
`frontend`, put the number in the mastheads of `docs/MANUAL.html`,
`docs/INSTALL.html`, `docs/QUICK-INSTALL.html` and `docs/HISTORY.html`, run the
tests, build the package.

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
