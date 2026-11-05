/**
 * NERs — the list of the RackReach Map (1.0.0).
 *
 * The map study needs the names, the pages each NER is drawn on, a rename, a
 * criticality (it sets the colour on the map) and a delete. Nothing else:
 * no capacity, no lock, no confirmation, no naming convention, no space code,
 * no indoor / outdoor. The work itself is done in the Drawing viewer.
 */

import { useEffect, useMemo, useState } from "react";
import type { Ctx } from "../App";
import { api } from "../lib/api";
import { KIND_COLOUR, KIND_LABEL, REFERENCE_KINDS, VISUAL_ONLY_KINDS } from "../lib/notation";
import { t } from "../lib/i18n";
import { useSort } from "../components/useSort";
import { PageButton, pageName, sheetNo } from "./panels";
import { nerNameKey } from "./Viewer";

function useLoad<T>(fn: () => Promise<T>, deps: any[], initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const run = async () => {
    setLoading(true);
    try { setData(await fn()); } catch { /* the list stays as it was */ } finally { setLoading(false); }
  };
  useEffect(() => { void run(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, deps);
  return { data, loading, reload: run };
}

export function NersPanel({ ctx }: { ctx: Ctx }) {
  const p = ctx.project;
  const s = useSort<any>();
  const [busy, setBusy] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const sr = useSort<any>();
  const reg = useLoad(() => (p ? api.ners(p.id, true) : Promise.resolve(null)), [p?.id], null as any);
  const els = useLoad(() => (p ? api.elements(p.id) : Promise.resolve([])), [p?.id], [] as any[]);

  const all: any[] = reg.data?.ners || [];
  const rows = useMemo(() => all.filter((r) => r.is_live), [all]);
  const removed = useMemo(() => all.filter((r) => r.deleted_at && !r.merged_into_id), [all]);

  const tally = useMemo(() => {
    const acc: Record<string, { count: number; pages: Set<number> }> = {};
    for (const e of els.data || []) {
      if (!VISUAL_ONLY_KINDS.has(e.kind) && !REFERENCE_KINDS.has(e.kind) && e.kind !== "WIRELESS") continue;
      const row = acc[e.kind] || (acc[e.kind] = { count: 0, pages: new Set() });
      row.count += 1;
      row.pages.add(e.page);
    }
    return acc;
  }, [els.data]);

  if (!p) return <div className="muted">{t("Select a project first.")}</div>;

  const act = async (fn: () => Promise<any>, ok?: string) => {
    setBusy(true);
    try {
      await fn();
      await Promise.all([reg.reload(), els.reload()]);
      if (ok) ctx.notify(ok);
    } catch (e: any) {
      ctx.notify(e.message, "err");
    } finally {
      setBusy(false);
    }
  };

  const critical = rows.filter((r) => r.classification === "critical").length;

  return (
    <>
      <div className="card">
        <h2>{t("NERs")}</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          {t("Every NER on the map, whether it came from the drawing or you drew it. Click a page to open it in the Drawing viewer. Rename, set critical or non-critical (the colour on the map) or delete it here; everything else is done in the Drawing viewer. Giving a NER the name of another one asks whether they are the same NER drawn on two pages.")}
        </p>
        <div className="row" style={{ alignItems: "center" }}>
          <span className="pill">{rows.length} {t("NERs")}</span>
          <span className="pill" style={{ color: "var(--bad)" }}>{critical} {t("critical")}</span>
          <span className="pill">{rows.length - critical} {t("non-critical")}</span>
          <span style={{ flex: 1 }} />
          {removed.length > 0 && (
            <button className={showRemoved ? "small active" : "small"} onClick={() => setShowRemoved((v) => !v)}>
              {t("Removed")} ({removed.length})
            </button>
          )}
        </div>
        {Object.keys(tally).length > 0 && (
          <p className="small muted" style={{ marginBottom: 0 }}>
            {t("Also on the map, managed in the Drawing viewer")}:{" "}
            {Object.keys(tally).map((k, i) => {
              const pages = Array.from(tally[k].pages).sort((a, b) => a - b);
              return (
                <span key={k}>
                  {i > 0 ? " · " : ""}
                  <span style={{ color: KIND_COLOUR[k], fontWeight: 600 }}>{tally[k].count}</span>{" "}
                  {k === "WIRELESS" ? "Wi-Fi" : t(KIND_LABEL[k] || k)} ({t("p.")} {pages.slice(0, 8).map((n) => sheetNo(p, n)).join(", ")}{pages.length > 8 ? ` +${pages.length - 8}` : ""})
                </span>
              );
            })}
          </p>
        )}
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <s.Th k="display_name">{t("Name")}</s.Th>
              <s.Th k="classification">{t("Criticality")}</s.Th>
              <s.Th k="page" get={(r: any) => r.canonical?.page}>{t("Pages")}</s.Th>
              <th />
            </tr>
          </thead>
          <tbody>
            {s.apply(rows).map((r: any) => (
              <NerRow key={r.id} ctx={ctx} r={r} all={rows} busy={busy} act={act} />
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={4} className="muted">
                {reg.loading ? t("Loading…") : t("No NER yet — place them from the drawing on the Scale screen, or draw them in the Drawing viewer.")}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {showRemoved && removed.length > 0 && (
        <div className="card">
          <h2>{t("Removed NERs")}</h2>
          <table>
            <thead><tr><sr.Th k="display_name">{t("Name")}</sr.Th><sr.Th k="deleted_at">{t("When")}</sr.Th><th /></tr></thead>
            <tbody>
              {sr.apply(removed).map((r: any) => (
                <tr key={r.id} style={{ opacity: 0.75 }}>
                  <td>{r.display_name}</td>
                  <td className="small muted">{r.deleted_at ? new Date(r.deleted_at).toLocaleString() : "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="small" disabled={busy}
                            onClick={() => act(() => api.restoreNer(p.id, r.id), `${r.display_name} ${t("restored")}`)}>
                      {t("Restore")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function NerRow({ ctx, r, all, busy, act }: any) {
  const p = ctx.project;
  const [name, setName] = useState(r.display_name);
  useEffect(() => setName(r.display_name), [r.display_name]);
  const editable = r.editable !== false;

  const rename = () => {
    const nn = name.trim();
    if (!nn || nn === r.display_name) { setName(r.display_name); return; }
    const twin = (all || []).find((o: any) => o.id !== r.id && nerNameKey(o.display_name) === nerNameKey(nn));
    if (twin) {
      const same = confirm(
        `${nn} ${t("is already the name of another NER")}`
        + `${twin.pages?.length ? ` (p. ${twin.pages.map((n: number) => sheetNo(p, n)).join(", ")})` : ""}.\n\n`
        + t("Is it the same NER? OK makes the two one NER, with every marker of both. Cancel keeps the current name — choose another one."));
      if (same) {
        act(() => api.consolidateNers(p.id, {
          primary_id: twin.id, duplicate_id: r.id, force: true,
          justification: `the same name (${nn}) given on the NERs screen`,
        }), t("Joined into one NER."));
      } else setName(r.display_name);
      return;
    }
    act(() => api.renameNer(p.id, r.id, { name: nn, reason: "renamed on the NERs screen" }));
  };

  const remove = () => {
    const where = (r.pages || []).map((n: number) => pageName(p, n)).join(", ");
    if (!confirm(`${t("Delete")} ${r.display_name}${where ? ` (${where})` : ""}? ${t("It leaves every page it is drawn on. Removed NERs can be restored.")}`)) return;
    act(() => api.removeNer(p.id, r.id, "deleted on the NERs screen"), `${r.display_name} ${t("deleted")}`);
  };

  const pages: number[] = r.pages?.length ? r.pages : (r.canonical?.page != null ? [r.canonical.page] : []);
  return (
    <tr>
      <td style={{ minWidth: 200 }}>
        <input value={name} className="mono" disabled={!editable || busy}
               title={t("Rename — the marker on the map follows")}
               onChange={(e) => setName(e.target.value)}
               onBlur={rename}
               onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      </td>
      <td style={{ minWidth: 150 }}>
        <select value={r.classification} disabled={busy || !editable}
                style={{ color: r.classification === "critical" ? "var(--bad)" : undefined }}
                onChange={(e) => act(() => api.classifyNer(p.id, r.id, {
                  classification: e.target.value, confirmed: e.target.value !== "pending",
                  reason: "chosen on the NERs screen",
                }))}>
          <option value="critical">{t("Critical")}</option>
          <option value="non_critical">{t("Non-critical")}</option>
          {r.classification === "pending" && <option value="pending">{t("Not stated on the drawing")}</option>}
        </select>
      </td>
      <td>
        {pages.length === 0 && <span className="muted small">{t("no position")}</span>}
        {pages.map((n: number, i: number) => {
          const o = (r.occurrences || []).find((q: any) => q.page === n)
            || (r.canonical?.page === n ? r.canonical : null);
          return (
            <span key={n}>
              {i > 0 && " "}
              <PageButton ctx={ctx} page={n} x={o?.x} y={o?.y} pill />
            </span>
          );
        })}
      </td>
      <td style={{ textAlign: "right" }}>
        <button className="small danger" disabled={busy || !editable} onClick={remove}>{t("Delete")}</button>
      </td>
    </tr>
  );
}
