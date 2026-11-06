# RackReach Map

**Version 1.2.0** — the number lives in `VERSION`; see `CHANGELOG.md`.

The map-only edition of RackReach: a local web
application for studying rack placement on venue design books (design book) — import the
books, confirm the scales, place racks and the other markers by hand, measure,
look at the reach circles, and export the marked-up PDF and the composite map.
No demand list is read and nothing is counted.

> This tool supports the design process. It does not replace engineering approval,
> a site survey, cable route validation or physical inspection.

© 2026 Marcelo Barbosa. Created by Marcelo Barbosa. Free software under the
[GNU AGPL-3.0](LICENSE.txt), keeping the credit to the author — see
[`NOTICE.txt`](NOTICE.txt). Components keep their own licences
([`THIRD-PARTY-NOTICES.txt`](THIRD-PARTY-NOTICES.txt)).

## Separate from the full analyzer

| | RackReach Map | RackReach |
|---|---|---|
| Address | http://localhost:8001 | http://localhost:8000 |
| Folder | `%LOCALAPPDATA%\Programs\ner-map-analyzer` | `%LOCALAPPDATA%\Programs\ner-analyzer` |
| Tasks | `NER Map Analyzer`, `NER Map Analyzer watchdog` | its own |
| Data | its own database and uploads | its own |

## Documentation

| File | What it is |
|---|---|
| `docs/MANUAL.html` | User manual, screen by screen |
| `docs/INSTALL.html`, `docs/QUICK-INSTALL.html` | Installation |
| `docs/HISTORY.html`, `CHANGELOG.md` | Origin and versions |
| `docs/LIMITATIONS.md` | What it does not do |

## Install (Windows)

Unzip the package and double-click `setup-noadmin.bat` (or run `setup.bat` as
administrator). It installs its own Python, registers the two scheduled tasks,
starts the server and opens http://localhost:8001. Projects are kept on
updates.

## Develop

`frontend/` is React + TypeScript + Vite (`npm run build` writes
`backend/app/static/`); `backend/` is FastAPI + SQLAlchemy + SQLite with
PyMuPDF. Tests: `run-tests.bat` (or `.python\python.exe -m pytest tests`).
Package: `build_package.bat`.
