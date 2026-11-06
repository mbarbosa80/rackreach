import { useEffect, useMemo, useRef, useState } from "react";
import { Image as KImage, Label, Tag, Text } from "react-konva";
import { api } from "../lib/api";

/**
 * A sheet too large for one bitmap, drawn in tiles the server cuts (5.5.0).
 *
 * The viewer draws an ordinary sheet once, whole, with PDF.js, at 2.2 px per
 * point in a bitmap of at most 6,000 px. A 900-series master plan (340 x 220
 * in) got 0.25 px per point that way: blurred at every zoom, and the whole
 * 145 MB book loaded in the browser first. Such a sheet is drawn instead from
 * tiles at the zoom on screen — the composite map's way (5.0.0): a coarse
 * picture of the whole sheet first, then the tiles in view, sharp, asked for
 * again whenever the zoom or the view changes. Marcelo, 5 Nov 2026: "quando dá
 * zoom, não renderiza de novo ou leva muito tempo."
 *
 * Coordinates are the sheet's points, the same the markers are drawn in, so
 * this sits on the stage where the PDF.js bitmap would.
 */

export const TILE_PX = 512;
export const TILE_ZMAX = 10;
/** Above this longest side (points) a sheet is tiled — where the single
 *  bitmap would fall under 2.2 px per point. Equal to the server's rule. */
export const TILED_ABOVE_PT = 6000 / 2.2;
/** Tiles kept loaded in the browser, the ones seen last. */
const KEEP_IMAGES = 400;
/** More tiles than this in view at one level: that level is not asked for. */
const MAX_IN_VIEW = 160;

export function isTiledSheet(w: number, h: number): boolean {
  return Math.max(w || 0, h || 0) > TILED_ABOVE_PT;
}

const scaleOf = (z: number) => 2 ** z / 64;

type Tile = { key: string; z: number; tx: number; ty: number; x: number; y: number; w: number; h: number };

function levelTiles(z: number, W: number, H: number,
                    view?: { x0: number; y0: number; x1: number; y1: number }): Tile[] {
  const span = TILE_PX / scaleOf(z);
  const nx = Math.max(1, Math.ceil(W / span)), ny = Math.max(1, Math.ceil(H / span));
  const x0 = view ? Math.max(0, Math.floor(view.x0 / span)) : 0;
  const y0 = view ? Math.max(0, Math.floor(view.y0 / span)) : 0;
  const x1 = view ? Math.min(nx - 1, Math.floor(view.x1 / span)) : nx - 1;
  const y1 = view ? Math.min(ny - 1, Math.floor(view.y1 / span)) : ny - 1;
  if (x1 < x0 || y1 < y0 || (x1 - x0 + 1) * (y1 - y0 + 1) > MAX_IN_VIEW) return [];
  const out: Tile[] = [];
  for (let ty = y0; ty <= y1; ty++) {
    for (let tx = x0; tx <= x1; tx++) {
      const x = tx * span, y = ty * span;
      out.push({ key: `${z}/${tx}/${ty}`, z, tx, ty, x, y,
                 w: Math.min(span, W - x), h: Math.min(span, H - y) });
    }
  }
  return out;
}

export function PageTiles({ projectId, drawingId, page, width, height, zoom, pan, size, onReady }: {
  projectId: number; drawingId: number; page: number; width: number; height: number;
  zoom: number; pan: { x: number; y: number }; size: { w: number; h: number };
  /** Called once the picture of the whole sheet is in (or could not come). */
  onReady?: () => void;
}) {
  const sheet = `${projectId}/${drawingId}/${page}`;
  // Loaded tiles of this sheet, by "z/x/y", in the order last wanted.
  const images = useRef(new Map<string, HTMLImageElement>());
  const [, setLoaded] = useState(0);
  // Tiles the server is too slow to draw just now (rc6): it answers 503 with
  // the seconds to wait. By "z/x/y", when to ask again (ms since the epoch).
  // Asked again by itself then — no pan needed — and said on the sheet
  // meanwhile, instead of a blank square and no word.
  const slow = useRef(new Map<string, number>());
  const timers = useRef<number[]>([]);
  const sheetRef = useRef("");
  const [retryTick, setRetryTick] = useState(0);
  const [, setClock] = useState(0);

  // A new sheet: the tiles of the one left go, and its picture is waited
  // for again (rc5): coming back to a sheet whose picture was in before,
  // from one whose picture was not, left "Rendering the page…" up for good.
  useEffect(() => {
    const kept = images.current;
    failed.current.clear();
    readyFor.current = "";
    slow.current.clear();
    sheetRef.current = sheet;
    return () => {
      kept.forEach((img) => { img.onload = null; img.onerror = null; img.src = ""; });
      kept.clear();
      timers.current.forEach((id) => window.clearTimeout(id));
      timers.current = [];
    };
  }, [sheet]);

  const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
  // The whole sheet in about 1,000 px: always drawn, under everything.
  const zBase = Math.max(0, Math.min(TILE_ZMAX, Math.floor(Math.log2(1024 / Math.max(width, height) * 64))));
  // The level that gives a screen pixel a tile pixel at this zoom.
  const zNow = Math.max(zBase, Math.min(TILE_ZMAX, Math.ceil(Math.log2(Math.max(zoom, 1e-6) * dpr * 64))));

  const wanted = useMemo(() => {
    const view = { x0: -pan.x / zoom, y0: -pan.y / zoom,
                   x1: (size.w - pan.x) / zoom, y1: (size.h - pan.y) / zoom };
    const base = levelTiles(zBase, width, height);
    return zNow > zBase ? base.concat(levelTiles(zNow, width, height, view)) : base;
  }, [zBase, zNow, width, height, pan.x, pan.y, zoom, size.w, size.h]);

  // The whole-sheet picture in (or refused): the viewer's "Rendering the
  // page" notice goes. Checked after every render — a tile coming in renders.
  const readyFor = useRef("");
  const failed = useRef(new Set<string>());
  const baseKeys = useMemo(() => levelTiles(zBase, width, height).map((t) => t.key), [zBase, width, height]);
  useEffect(() => {
    if (readyFor.current === sheet) return;
    const m = images.current;
    const settled = baseKeys.every((k) => {
      if (failed.current.has(k)) return true;
      const img = m.get(k);
      return !!img && img.complete && img.naturalWidth > 0;
    });
    if (settled) {
      readyFor.current = sheet;
      onReady?.();
    }
  });

  // A tile that did not come: was it refused for being slow? An image does
  // not say why it failed, so the server is asked once more — a refusal is
  // answered at once, without drawing anything.
  const probe = (key: string, url: string, forSheet: string) => {
    void fetch(url, { cache: "no-store", credentials: "same-origin" }).then((r) => {
      if (sheetRef.current !== forSheet) return;
      if (r.status === 503) {
        const secs = Math.max(1, Number(r.headers.get("Retry-After")) || 30);
        const at = Date.now() + secs * 1000;
        slow.current.set(key, at);
        timers.current.push(window.setTimeout(() => {
          if (sheetRef.current !== forSheet) return;
          // Due again: no longer "failed", so the next pass asks for it.
          failed.current.delete(key);
          setRetryTick((n) => n + 1);
        }, secs * 1000 + 250));
        setClock((n) => n + 1);
        return;
      }
      // Any other answer ends the wait (rc7): a retry that failed another way
      // (404 — the book removed, the renderer stopped) left "trying again…"
      // up for good. The tile is then failed, as any tile that did not come.
      slow.current.delete(key);
      if (r.ok) {
        failed.current.delete(key);              // drawn meanwhile: ask again now
        setRetryTick((n) => n + 1);
      }
      setClock((n) => n + 1);
    }).catch(() => {                             // offline: the next pan asks again
      if (sheetRef.current !== forSheet) return;
      slow.current.delete(key);
      setClock((n) => n + 1);
    });
  };

  // Ask for what is wanted and not loaded yet; forget the oldest beyond the cap.
  useEffect(() => {
    const m = images.current;
    const now = Date.now();
    for (const t of wanted) {
      const have = m.get(t.key);
      if (have) { m.delete(t.key); m.set(t.key, have); continue; }
      const due = slow.current.get(t.key);
      if (due !== undefined && due > now) continue;   // refused until then
      const img = new window.Image();
      const url = api.pageTileUrl(projectId, drawingId, page, t.z, t.tx, t.ty);
      const forSheet = sheet;
      img.onload = () => { slow.current.delete(t.key); setLoaded((n) => n + 1); };
      img.onerror = () => {
        m.delete(t.key); failed.current.add(t.key); setLoaded((n) => n + 1);
        probe(t.key, url, forSheet);
      };
      img.src = url;
      m.set(t.key, img);
    }
    while (m.size > KEEP_IMAGES) {
      const oldest = m.keys().next().value as string;
      const img = m.get(oldest);
      if (img) { img.onload = null; img.onerror = null; img.src = ""; }
      m.delete(oldest);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, projectId, drawingId, page, retryTick]);

  // The notice counts down while a tile in view waits for its turn again.
  const wantedKeys = useMemo(() => new Set(wanted.map((t) => t.key)), [wanted]);
  const waits = [...slow.current.entries()].filter(([k]) => wantedKeys.has(k)).map(([, at]) => at);
  const slowInView = waits.length > 0;
  useEffect(() => {
    if (!slowInView) return;
    const id = window.setInterval(() => setClock((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [slowInView]);

  // Draw every loaded tile in view at this level or coarser, coarse first:
  // the tiles of the zoom just left fill the gaps while the new ones arrive.
  const view = { x0: -pan.x / zoom, y0: -pan.y / zoom,
                 x1: (size.w - pan.x) / zoom, y1: (size.h - pan.y) / zoom };
  const shown: { t: Tile; img: HTMLImageElement }[] = [];
  images.current.forEach((img, key) => {
    if (!img.complete || !img.naturalWidth) return;
    const [z, tx, ty] = key.split("/").map(Number);
    if (z > zNow) return;
    const span = TILE_PX / scaleOf(z);
    const x = tx * span, y = ty * span;
    const w = Math.min(span, width - x), h = Math.min(span, height - y);
    if (z !== zBase && (x > view.x1 || y > view.y1 || x + w < view.x0 || y + h < view.y0)) return;
    shown.push({ t: { key, z, tx, ty, x, y, w, h }, img });
  });
  shown.sort((a, b) => a.t.z - b.t.z);

  const secondsLeft = slowInView ? Math.max(0, Math.ceil((Math.min(...waits) - Date.now()) / 1000)) : 0;
  const notice = !slowInView ? "" : secondsLeft > 0
    ? `Part of this sheet is slow to draw — trying again in ${secondsLeft} s`
    : "Part of this sheet is slow to draw — trying again…";

  return (
    <>
      {shown.map(({ t, img }) => (
        <KImage key={t.key} image={img} x={t.x} y={t.y} width={t.w} height={t.h}
                listening={false} perfectDrawEnabled={false} />
      ))}
      {notice && (
        // Fixed on the screen, top left of the view, whatever the zoom.
        <Label name="slow-tiles-notice" x={(12 - pan.x) / zoom} y={(12 - pan.y) / zoom}
               scaleX={1 / zoom} scaleY={1 / zoom} listening={false}>
          <Tag fill="#fff4e0" stroke="#d98a00" strokeWidth={1} cornerRadius={4} opacity={0.95} />
          <Text text={notice} fontSize={13} padding={7} fill="#5a3b00" />
        </Label>
      )}
    </>
  );
}
