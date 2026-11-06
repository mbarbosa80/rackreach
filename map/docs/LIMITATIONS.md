# Known limitations — RackReach Map 1.2.0

* Distances and reach circles are straight lines on one sheet. No cable route,
  no level-to-level link.
* No demand list, no demand, no ports, no capacity: nothing is counted.
* OCR is not built: a scanned sheet with no text layer gives no room names.
* The backend still carries the code of the full analyzer that the screens no
  longer use (matching, demand, coverage). It is not reachable from the
  interface; removing it is a candidate for a later version.
