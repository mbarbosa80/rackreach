import { useEffect, useRef, useState } from "react";
import type { Ctx } from "../App";
import { JobBar } from "../components/JobBar";
import { api } from "../lib/api";
import { useJob } from "../lib/useJob";
import { t } from "../lib/i18n";
import { useSort } from "../components/useSort";
import { CompositeCard } from "./Composite";

function useAsync<T>(fn: () => Promise<T>, deps: any[], initial: T) {
  const [data, setData] = useState<T>(initial);
  // Starts true on purpose. With `false` there is a frame where the panel renders
  // the empty state before the first fetch runs, so a worklist with two unresolved
  // spaces briefly claims "0 unresolved" — false reassurance is the one thing this
  // tool must never give.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fn());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, setData, loading, error, reload: run };
}

/** Natural order for names: "SPT.6" before "SPT.16", case and accents ignored. */
export const SPACE_COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function ProjectsPanel({ ctx }: { ctx: Ctx }) {
  const sp = useSort<any>();
  const { data: projects, reload } = useAsync(() => api.listProjects(), [], [] as any[]);
  const [name, setName] = useState("");
  const [venue, setVenue] = useState("");
  //: The project package (4.6.0): one job for an export or an import.
  const xfer = useJob();
  const [importName, setImportName] = useState("");
  const [exporting, setExporting] = useState<number | null>(null);

  /** Write the whole project into one package and hand it to the browser. */
  const exportProject = async (pr: any) => {
    setExporting(pr.id);
    try {
      const j = await xfer.startAndWait(() => api.exportProject(pr.id));
      if (j.state === "done") {
        const r = j.result || {};
        const a = document.createElement("a");
        a.href = api.exportDownloadUrl(pr.id, r.filename);
        a.download = r.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        ctx.notify(`${t("Project package written")}: ${r.filename} (${Math.round((r.bytes || 0) / 1048576)} MB, ` +
                   `${r.documents} ${t("document(s)")}). ${t("It holds the venue's design books — share it only through your organisation's approved channels.")}`);
      } else if (j.state === "error") ctx.notify(j.error, "err");
    } catch (e: any) { ctx.notify(e.message, "err"); }
    finally { setExporting(null); }
  };

  /** A package from another computer becomes a new project of this account. */
  const importPackage = async (f: File) => {
    try {
      const j = await xfer.startAndWait(() => api.importProject(f, importName.trim()));
      if (j.state === "done") {
        const r = j.result || {};
        setImportName("");
        await reload();
        ctx.setProjectId(r.project_id);
        ctx.notify(`${t("Project imported as")} “${r.name}” — ${t("exported by")} ${r.exported_by || "?"}` +
                   ` (${t("version")} ${r.from_app_version || "?"}). ${t("It is a copy: changes made here do not reach the original.")}`);
      } else if (j.state === "error") ctx.notify(j.error, "err");
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  const create = async () => {
    if (!name.trim()) return;
    try {
      const r = await api.createProject({ name: name.trim(), venue_code: venue.trim() || undefined });
      setName("");
      setVenue("");
      await reload();
      ctx.setProjectId(r.id);
      ctx.notify(t("Project created. Import the drawings next."));
    } catch (e: any) {
      ctx.notify(e.message, "err");
    }
  };

  return (
    <>
      <div className="card">
        <h2>{t("New project")}</h2>
        <div className="row">
          <div style={{ flex: 2 }}>
            <label>{t("Project name")}</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Venue - VVV" />
          </div>
          <div style={{ flex: 1 }}>
            <label>{t("Venue code (optional)")}</label>
            <input value={venue} onChange={(e) => setVenue(e.target.value)} placeholder="VVV" />
          </div>
          <button className="primary" onClick={create}>{t("Create")}</button>
        </div>
      </div>

      <div className="card">
        <h2>{t("Projects")}</h2>
        <table>
          <thead>
            <tr><sp.Th k="name">{t("Name")}</sp.Th><sp.Th k="venue_code">{t("Venue")}</sp.Th>
                <sp.Th k="owner">{t("Owner")}</sp.Th><sp.Th k="visibility">{t("Visible to")}</sp.Th>
                <sp.Th k="drawings">{t("Drawings")}</sp.Th><th /></tr>
          </thead>
          <tbody>
            {sp.apply(projects).map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td className="mono">{p.venue_code || "—"}</td>
                <td className="small">
                  {p.mine ? <span style={{ color: "var(--ok)" }}>{t("you")}</span> : (p.owner || "—")}
                </td>
                <td style={{ minWidth: 150 }}>
                  {p.can_edit ? (
                    <select
                      value={p.visibility || "private"}
                      title={t("Shared means readable by the other accounts on this machine. Nothing is published anywhere.")}
                      onChange={async (e) => {
                        try {
                          await api.setVisibility(p.id, e.target.value);
                          await reload();
                          ctx.notify(
                            e.target.value === "shared"
                              ? t("Readable by the other accounts on this installation.")
                              : t("Private again — only you and administrators."),
                          );
                        } catch (err: any) { ctx.notify(err.message, "err"); }
                      }}
                    >
                      <option value="private">{t("only me")}</option>
                      <option value="shared">{t("accounts on this machine")}</option>
                    </select>
                  ) : (
                    <span className="small muted">{t("shared with you · read only")}</span>
                  )}
                </td>
                <td>{p.drawings}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  <button className="small" onClick={() => ctx.setProjectId(p.id)}>{t("Open")}</button>{" "}
                  <button className="small" disabled={xfer.busy}
                          title={t("Write the whole project — drawings and everything drawn on them — into one package, to continue the work on another computer")}
                          onClick={() => void exportProject(p)}>
                    {exporting === p.id ? t("Exporting…") : t("Export")}
                  </button>{" "}
                  <button
                    className="small danger"
                    disabled={!p.can_edit}
                    title={p.can_edit ? "" : t("only the owner can delete this project")}
                    onClick={async () => {
                      if (!confirm(`${t("Delete")} “${p.name}” ${t("and everything in it?")}`)) return;
                      await api.deleteProject(p.id);
                      if (ctx.projectId === p.id) ctx.setProjectId(null);
                      await reload();
                    }}
                  >
                    {t("Delete")}
                  </button>
                </td>
              </tr>
            ))}
            {projects.length === 0 && (
              <tr><td colSpan={6} className="muted">{t("No projects yet.")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="card">
        {/* Below the list (5.2.0): the list is what is opened every day; a
            package comes in now and then. */}
        <h2>{t("Import a project package")}</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          {t("A package is a .zip written by Export in the RackReach Map on another computer: the drawings and everything drawn on them — scales, key plans, NERs, Wi-Fi points, areas. It comes in as a new project of yours, private until you share it. The original is not touched and nothing is kept in step between the two copies. Packages of RackReach are not accepted.")}
        </p>
        <div className="row">
          <div style={{ flex: 2 }}>
            <label>{t("Name for the imported project (optional)")}</label>
            <input value={importName} onChange={(e) => setImportName(e.target.value)}
                   placeholder={t("the name it had — with “(imported …)” if taken here")} />
          </div>
          <div style={{ flex: 2 }}>
            <label>{t("Package (.zip)")}</label>
            <input type="file" accept=".zip,application/zip" disabled={xfer.busy}
                   onChange={(e) => {
                     const f = e.target.files?.[0];
                     e.target.value = "";
                     if (f) void importPackage(f);
                   }} />
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <JobBar job={xfer.job} onCancel={xfer.cancel} onDismiss={xfer.clear} />
        </div>
      </div>

    </>
  );
}

// ---------------------------------------------------------------------------

export function SetupPanel({ ctx }: { ctx: Ctx }) {
  const sf = useSort<any>();
  const p = ctx.project;
  const [settings, setSettings] = useState<any>(null);

  const drawJob = useJob();
  //: The books chosen together in the file dialog, and how each one went
  //  (4.6.0). Declared with every other hook, above the early return: placed
  //  below it in 4.6.0, the screen had one hook more once a project was open
  //  than before, and React refused to draw it (error #310) whenever a
  //  project was chosen after the application opened without one (4.6.1).
  const [queue, setQueue] = useState<{ name: string; state: string; note?: string }[]>([]);

  useEffect(() => { setSettings(p?.settings ? { ...p.settings } : null); }, [p?.id]);

  if (!p) return <div className="muted">{t("Select a project first.")}</div>;


  /**
   * Several books chosen together in the file dialog (4.6.0), read one after
   * the other — each to its end before the next, so the same book chosen
   * twice is refused by the server instead of racing in beside itself. A
   * file refused (the same book, a name already in the project, not a PDF)
   * is said and the rest carry on.
   */
  const uploadDrawings = async (files: File[]) => {
    // The same file picked twice in one selection is dropped here already.
    const seen = new Set<string>();
    const list = files.filter((f) => {
      const k = `${f.name.toLowerCase()}|${f.size}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
    const status = list.map((f) => ({ name: f.name, state: "waiting" as string, note: "" }));
    setQueue([...status]);
    const done: string[] = [];
    const refused: string[] = [];
    for (let i = 0; i < list.length; i++) {
      status[i].state = "reading";
      setQueue([...status]);
      try {
        const j = await drawJob.startAndWait(() => api.uploadDrawing(p.id, list[i]));
        if (j.state === "done") {
          const r = j.result || {};
          status[i].state = "imported";
          status[i].note = `${r.page_count} ${t("pages")}, ${r.summary?.spaces ?? 0} ${t("spaces")}`;
          done.push(list[i].name);
        } else {
          status[i].state = j.state === "cancelled" ? "cancelled" : "refused";
          status[i].note = j.error || j.stage;
          refused.push(list[i].name);
          if (j.state === "cancelled") break;
        }
      } catch (e: any) {
        status[i].state = "refused";
        status[i].note = e.message;
        refused.push(list[i].name);
      }
      setQueue([...status]);
      await ctx.reload();
    }
    if (done.length) {
      ctx.notify(
        `${done.length} ${t("book(s) imported")}` +
        (refused.length ? ` · ${refused.length} ${t("refused — see the list")}` : "") +
        ` — ${t("tick the key plans and confirm the scales on the Scale screen")}`,
        refused.length ? "err" : undefined,
      );
    } else if (refused.length) {
      ctx.notify(`${t("Nothing imported")} — ${status.find((x) => x.note)?.note || ""}`, "err");
    }
  };

  /** Take one book out (4.6.0), after saying what goes with it. */
  const removeDrawing = async (d: any) => {
    try {
      const imp = await api.drawingImpact(p.id, d.id);
      const lines = [
        `${t("Remove")} ${d.filename} ${t("from this project?")}`,
        "",
        `${imp.pages} ${t("pages")}, ${imp.spaces} ${t("spaces")}, ${imp.markers} ${t("marker(s) drawn on it")}`,
      ];
      if (imp.duplicate_of?.length) lines.push(`${t("It is the same file as")} ${imp.duplicate_of.join(", ")}.`);
      if (imp.ners_only_here?.length) {
        lines.push(`${t("NERs drawn only on this book leave the register")}: ${imp.ners_only_here.join(", ")}`);
      }
      if (imp.ners_moved) lines.push(`${imp.ners_moved} ${t("NER(s) also drawn on another book stay, placed there")}`);
      lines.push("", t("This cannot be undone."));
      if (!confirm(lines.join("\n"))) return;
      const r = await api.deleteDrawing(p.id, d.id);
      await ctx.reload();
      ctx.notify(`${d.filename} ${t("removed")}` +
                 (r.ners_removed?.length ? ` · ${r.ners_removed.length} ${t("NER(s) left the register")}` : ""));
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  return (
    <>
      <div className="card">
        <h2>{t("1 · Drawings (PDF)")}</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          {t("Choose one book or several at once (Ctrl or Shift in the file dialog). They are read one after the other. The same file twice, or a book whose name is already in the project, is refused — remove the old one first if it is a new revision.")}
        </p>
        <input type="file" accept="application/pdf" multiple
               disabled={drawJob.busy}
               onChange={(e) => {
                 const files = Array.from(e.target.files || []);
                 e.target.value = "";
                 if (files.length) void uploadDrawings(files);
               }} />
        <div style={{ marginTop: 10 }}>
          <JobBar job={drawJob.job} onCancel={drawJob.cancel} onDismiss={drawJob.clear} />
        </div>
        {queue.length > 1 && (
          <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            {queue.map((q, i) => (
              <li key={i} style={{ color: q.state === "refused" ? "var(--bad)" : q.state === "imported" ? "var(--ok)" : undefined }}>
                {q.name} — {t(q.state)}{q.note ? ` · ${q.note}` : ""}
              </li>
            ))}
          </ul>
        )}
        <table style={{ marginTop: 10 }}>
          <thead><tr><sf.Th k="filename">{t("File")}</sf.Th><sf.Th k="page_count">{t("Pages")}</sf.Th><sf.Th k="spaces" get={(d: any) => d.summary?.spaces}>{t("Spaces")}</sf.Th><sf.Th k="markers" get={(d: any) => d.summary?.elements}>{t("Markers")}</sf.Th><sf.Th k="needs_ocr" get={(d: any) => d.summary?.needs_ocr_pages?.length || 0}>{t("OCR needed")}</sf.Th><th /></tr></thead>
          <tbody>
            {sf.apply(p.drawings).map((d: any) => (
              <tr key={d.id}>
                <td>
                  {d.filename}
                  {/* A copy imported before 4.6.0 refused it. */}
                  {d.duplicate_of?.length > 0 && (
                    <div className="small" style={{ color: "var(--warn)" }}>
                      {t("same file as")} {d.duplicate_of.join(", ")} — {t("remove one of them")}
                    </div>
                  )}
                </td>
                <td>{d.page_count}</td>
                <td>{d.summary?.spaces ?? "—"}</td>
                <td>{d.summary?.elements ?? "—"}</td>
                <td>{d.summary?.needs_ocr_pages?.length ? d.summary.needs_ocr_pages.join(", ") : "none"}</td>
                <td style={{ textAlign: "right" }}>
                  <button className="small danger" disabled={drawJob.busy}
                          title={t("Take this book out of the project, with its rooms and what is drawn on it")}
                          onClick={() => void removeDrawing(d)}>{t("Remove")}</button>
                </td>
              </tr>
            ))}
            {p.drawings.length === 0 && <tr><td colSpan={6} className="muted">{t("No drawing yet.")}</td></tr>}
          </tbody>
        </table>
      </div>

      {settings && (
        <div className="card">
          <h2>{t("2 · Distance circles")}</h2>
          <p className="muted small" style={{ marginTop: 0 }}>
            {t("The reach circles drawn around every NER in the Drawing viewer, the composite map and the PDF. They are straight-line circles on the sheet's scale — a picture of reach, not a cable length.")}
          </p>
          <div className="grid c4">
            <div>
              <label>{t("General limit (m)")}</label>
              <input type="number" value={settings.general_limit_m}
                     onChange={(e) => setSettings({ ...settings, general_limit_m: Number(e.target.value) })} />
            </div>
            <div>
              <label>{t("Photographer limit (m)")}</label>
              <input type="number" value={settings.photographer_limit_m}
                     onChange={(e) => setSettings({ ...settings, photographer_limit_m: Number(e.target.value) })} />
            </div>
            <div>
              <label>{t("Display units")}</label>
              <select value={settings.display_units}
                      onChange={(e) => setSettings({ ...settings, display_units: e.target.value })}>
                <option value="m">{t("metres")}</option>
                <option value="ft">{t("feet")}</option>
                <option value="both">{t("both")}</option>
              </select>
            </div>
          </div>
          <button className="primary" onClick={async () => {
            try {
              await api.updateSettings(p.id, settings);
              await ctx.reload();
              ctx.notify(t("Saved."));
            } catch (e: any) { ctx.notify(`${t("Not saved")}: ${e.message}`, "err"); }
          }}>{t("Save")}</button>
        </div>
      )}
    </>
  );
}


export function ScalePanel({ ctx }: { ctx: Ctx }) {
  const ss = useSort<any>();
  const p = ctx.project;
  const [denoms, setDenoms] = useState<Record<number, string>>({});
  //: The key-plan ticks (2.14.0): what the person ticked since the last
  //  Apply, page → answer. Ticked by default from the title block.
  const [ticks, setTicks] = useState<Record<number, boolean>>({});
  const [applying, setApplying] = useState(false);
  const placeJob = useJob();
  // The double-click guards of the two placing steps (4.0.0, 2.19.0) — refs
  // read synchronously, because a double click fires two clicks before React
  // re-renders the disabled button. Declared above the early return (4.6.1):
  // below it the screen called three hooks more once a project was open.
  const placingNersRef = useRef(false);
  useEffect(() => { setTicks({}); }, [p?.id]);
  if (!p) return null;

  const keyOf = (pg: any) => (pg.number in ticks ? ticks[pg.number] : !!pg.key_plan);
  const dirty = p.pages.filter((pg: any) => pg.number in ticks && ticks[pg.number] !== !!pg.key_plan);
  const applyKeyPlans = async () => {
    if (!dirty.length) return;
    setApplying(true);
    try {
      const body: Record<number, boolean> = {};
      for (const pg of dirty) body[pg.number] = ticks[pg.number];
      const r = await api.setKeyPlans(p.id, body);
      await ctx.reload();
      setTicks({});
      const parts = [`${r.changed.length} ${t("page(s) changed")}.`];
      if (r.spaces_removed) parts.push(`${r.spaces_removed} ${t("space(s) taken off the key plans")}.`);
      if (r.spaces_read) parts.push(`${r.spaces_read} ${t("space(s) read off the pages that are detail sheets again")}.`);
      ctx.notify(parts.join(" "));
    } catch (e: any) { ctx.notify(e.message, "err"); }
    finally { setApplying(false); }
  };

  const keyCount = p.pages.filter((pg: any) => keyOf(pg)).length;
  const detailUnconfirmed = p.pages.filter((pg: any) => !keyOf(pg) && !pg.scale_confirmed).length;
  const nerPass = (p.drawings || []).map((d: any) => d.summary?.ner_pass).filter(Boolean);
  // A double click fires two clicks before React re-renders the disabled
  // button, so the guard is a ref read synchronously (4.0.0); the server
  // refuses a second run too.
  const placeNers = async () => {
    if (placingNersRef.current || placeJob.busy) return;
    placingNersRef.current = true;
    try { await placeNersOnce(); } finally { placingNersRef.current = false; }
  };
  const placeNersOnce = async () => {
    const warn = [];
    if (dirty.length) warn.push(t("you have key-plan ticks not applied yet"));
    if (detailUnconfirmed) warn.push(`${detailUnconfirmed} ${t("detail page(s) have no confirmed scale — a NER near an existing one is told apart at 12 m, which needs the scale")}`);
    const q = (warn.length ? `${t("Before placing")}: ${warn.join("; ")}.\n\n` : "")
      + t("Place the NERs the drawings state? The team's annotations first, then the TEC.010 / TEC.020 (or TEC.003.01 / .02) rooms, then the \"Network Equipment Rack\" labels, then the NER block symbols — never on a key plan, never where a NER already is. Every one is named in sequence (NERc_01.1 …, NER_20.1 …) for you to rename. Running it again only adds what is still missing.");
    if (!confirm(q)) return;
    try {
      await placeJob.start(() => api.placeNers(p.id), async (j) => {
        if (j.state === "error") { ctx.notify(j.error, "err"); return; }
        if (j.state !== "done") return;
        await ctx.reload();
        const r = j.result || {};
        ctx.notify(`${r.created ?? 0} ${t("NER(s) placed")} (${r.annotations ?? 0} ${t("from the team's annotations")}, ${r.rooms_found ?? 0} ${t("NER room(s) read")}, ${r.same_as_marker ?? 0} ${t("already had a NER beside them")}). ${t("Check them in the Drawing viewer and on the NERs screen; delete any duplicate there.")}`);
      });
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  const apply = async (page: number, confirm: boolean) => {
    const raw = denoms[page];
    try {
      const body: any = { confirm };
      if (raw && /^[\d.]+$/.test(raw.trim())) body.denominator = Number(raw);
      else if (raw) body.text = raw;
      else {
        const pg = p.pages.find((x: any) => x.number === page);
        if (!pg?.metres_per_unit) { ctx.notify(t("Nothing to confirm on this page yet."), "err"); return; }
        body.denominator = (pg.metres_per_unit * 72) / 0.0254;
      }
      await api.setScale(p.id, page, body);
      await ctx.reload();
      ctx.notify(`${t("Page")} ${sheetNo(p, page)}: scale ${confirm ? "confirmed" : "set"}.`);
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  // Every page that holds a scale — detected, or typed in earlier — and is
  // not confirmed yet. That is what the one button confirms; a page with no
  // scale at all is skipped and named, because the only honest thing to do
  // with it is to keep blocking its results until somebody types the ratio.
  const confirmable = p.pages.filter((pg: any) => pg.metres_per_unit && !pg.scale_confirmed);
  const noScale = p.pages.filter((pg: any) => !pg.metres_per_unit);

  const confirmAll = async () => {
    if (!confirm(`${t("Confirm the scale on")} ${confirmable.length} ${t("page(s) at once?")}\n\n` +
                 t("Each page keeps the value shown in its Applied column. Pages with no scale are left alone."))) return;
    try {
      const r = await api.confirmAllScales(p.id);
      await ctx.reload();
      ctx.notify(`${r.confirmed.length} ${t("page(s) confirmed")}` +
                 (r.skipped_no_scale.length
                   ? ` · ${r.skipped_no_scale.length} ${t("page(s) have no scale to confirm")}: ${r.skipped_no_scale.join(", ")}`
                   : ""));
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  return (
    <>
    <div className="card">
      <h2>{t("Scale per page")}</h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        {t("Detected scales are proposals only. Definitive coverage results stay blocked until each page is confirmed. Type a ratio or a scale text in the row to override what was detected.")}
        {" "}
        {t("The Key plan tick marks a page that only illustrates — a key enlarged plan, the drawing index, the venue information, the abbreviations: no space is read off it and Place NERs from the drawing skips it (you can still draw on it by hand). Ticked by default from the title block; change it and press Apply.")}
      </p>
      {dirty.length > 0 && (
        <div className="row" style={{ marginBottom: 8, alignItems: "center" }}>
          <button className="primary" disabled={applying} onClick={applyKeyPlans}>
            {t("Apply key-plan ticks")} ({dirty.length})
          </button>
          <button className="small" disabled={applying} onClick={() => setTicks({})}>{t("Undo ticks")}</button>
          <span className="small muted" style={{ marginBottom: 0 }}>
            {t("A page ticked loses its spaces; a page unticked gets its spaces read.")}
          </span>
        </div>
      )}
      {/* One button for the whole book, beside the row buttons — not instead
          of them. A 75-page book was 75 identical clicks. */}
      <div className="row" style={{ marginBottom: 10, alignItems: "center" }}>
        <button className="primary" disabled={confirmable.length === 0} onClick={confirmAll}
                title={t("Confirms the detected (or typed) scale on every page that has one")}>
          {t("Confirm all pages")}{confirmable.length ? ` (${confirmable.length})` : ""}
        </button>
        <span className="small muted" style={{ marginBottom: 0 }}>
          {p.pages.filter((pg: any) => pg.scale_confirmed).length} {t("of")} {p.pages.length} {t("confirmed")}
          {noScale.length > 0 && (
            <> · <span style={{ color: "var(--warn)" }}>
              {noScale.length} {t("page(s) with no scale — type one in the row")}
            </span></>
          )}
        </span>
      </div>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <ss.Th k="page" get={(pg: any) => pg.number}>{t("Page")}</ss.Th><ss.Th k="key_plan" get={(pg: any) => keyOf(pg)}>{t("Key plan")}</ss.Th><ss.Th k="sheet_title" get={(pg: any) => `${pg.sheet_number || ""} ${pg.sheet_title || ""}`.trim()}>{t("Sheet / level")}</ss.Th><ss.Th k="scale_label" get={(pg: any) => (pg.candidates || []).map((c: any) => c.label).join(", ")}>{t("Detected")}</ss.Th><ss.Th k="metres_per_unit">{t("Applied")}</ss.Th>
              <ss.Th k="source" get={(pg: any) => pg.scale_source}>{t("Source")}</ss.Th><ss.Th k="status" get={(pg: any) => pg.scale_confirmed}>{t("Status")}</ss.Th><th>{t("Set (ratio or text)")}</th><th />
            </tr>
          </thead>
          <tbody>
            {ss.apply(p.pages).map((pg: any) => (
              <tr key={pg.number} className={keyOf(pg) ? "row-key" : ""}>
                <td>{sheetNo(p, pg.number)}{pg.needs_ocr && <div className="pill" style={{ color: "var(--warn)" }}>{t("OCR needed")}</div>}</td>
                <td>
                  <label className={`small key-plan-toggle ${keyOf(pg) ? "on" : ""}`}
                         title={t("A key plan only illustrates: its rooms are not read and Place NERs from the drawing skips it. Read off the drawing title in the title block; untick it if the title block was read wrong.")}>
                    <input type="checkbox" style={{ width: "auto" }} checked={keyOf(pg)}
                           onChange={(e) => setTicks({ ...ticks, [pg.number]: e.target.checked })} />
                    {keyOf(pg) ? "KEY" : ""}{pg.number in ticks && ticks[pg.number] !== !!pg.key_plan ? " *" : ""}
                  </label>
                </td>
                <td className="small">{pg.sheet_number ? <span className="mono">{pg.sheet_number}</span> : null}{pg.sheet_number && pg.sheet_title ? " · " : ""}{pg.sheet_title || (pg.sheet_number ? "" : "—")}{pg.level ? ` · ${pg.level}` : ""}</td>
                <td className="small mono">
                  {(pg.candidates || []).map((c: any) => c.label).join(", ") || "—"}
                </td>
                <td className="mono">{pg.scale_label || "—"}</td>
                <td className="small muted">{pg.scale_source || "—"}</td>
                <td>
                  <span className="pill" style={{ color: pg.scale_confirmed ? "var(--ok)" : "var(--warn)" }}>
                    {pg.scale_confirmed ? "confirmed" : "not confirmed"}
                  </span>
                </td>
                <td style={{ width: 170 }}>
                  <input placeholder={`200  or  1/8" = 1'-0"`} value={denoms[pg.number] || ""}
                         onChange={(e) => setDenoms({ ...denoms, [pg.number]: e.target.value })} />
                </td>
                {/* One button, not two.
                    "Set" wrote the scale without confirming it and "Confirm"
                    wrote the same value and confirmed it, so the only thing
                    "Set" could produce was a page that had been given a scale
                    and still blocked the coverage results — a state nobody
                    chooses on purpose. Confirm applies whatever is in the field
                    (or the detected value, when the field is empty) and marks
                    the page confirmed, which is what both buttons were being
                    used for. */}
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="small primary" onClick={() => apply(pg.number, true)}>{t("Confirm")}</button>
                </td>
              </tr>
            ))}
            {p.pages.length === 0 && <tr><td colSpan={9} className="muted">{t("Import a drawing first.")}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>

    {/* The NER placing step (2.14.0). Marcelo, 29 Sep 2026: "seria melhor a
        ferramenta só posicionar NER depois de eu marcar e desmarcar páginas
        key e confirmar scale". */}
    <div className="card">
      <h2>{t("NERs from the drawing")}</h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        {t("After the key plans are ticked and the scales confirmed, place the NERs the drawings state: the team's own annotations first, then the coded rooms — TEC.010 (critical) / TEC.020 (non-critical), or TEC.003.01 / .02 on older books — then the \"Network Equipment Rack\" labels (with or without the \"(NER)\", with their arrows when there are any), then the NER block symbols (the small square with NER inside, read by its shape; non-critical, criticality to confirm). Never on a key plan; a candidate within 12 m of a NER already on the sheet is taken as that NER. Every NER is named in sequence — NERc_01.1, NERc_02.1 … / NER_20.1, NER_21.1 … — with what the sheet printed kept as \"printed as\"; rename them on the NERs screen. Running it again only adds what is still missing; a duplicate is yours to delete in the Drawing viewer.")}
      </p>
      <div className="row" style={{ alignItems: "center" }}>
        <button className="primary" disabled={placeJob.busy || !(p.drawings || []).length} onClick={placeNers}>
          {t("Place NERs from the drawing")}
        </button>
        <span className="small muted" style={{ marginBottom: 0 }}>
          {keyCount} {t("key plan(s)")} · {detailUnconfirmed} {t("detail page(s) with no confirmed scale")}
          {nerPass.length > 0 && (
            <> · {t("last placed")} {nerPass.map((x: any) => `${x.at?.replace("T", " ") || "?"} (${x.created} ${t("created")})`).join("; ")}</>
          )}
        </span>
      </div>
      <div style={{ marginTop: 10 }}>
        <JobBar job={placeJob.job} onCancel={placeJob.cancel} onDismiss={placeJob.clear} />
      </div>
    </div>

    </>
  );
}

/**
 * "page 13 · VFF-410B8-CSL-O" — the page number and, when the title block was
 * read, the drawing number beside it. The number is how the design team names
 * a sheet; the page is how this tool does. Both, so either reader finds it.
 */
export function pageName(p: any, page: number | null | undefined): string {
  if (page == null) return "";
  const pg = p?.pages?.find((x: any) => x.number === page);
  const sheet = pg?.sheet_number ? ` · ${pg.sheet_number}` : "";
  // Several books (4.1.0): ``page`` is a sheet key; the book's series and
  // its own page number say where it is ("400 page 6 · VAA-411B3-VBV-O").
  if (p?.multi_book && pg) return `${bookSeries(p, pg.drawing_id)} ${t("page")} ${pg.page}${sheet}`;
  return `${t("page")} ${page}${sheet}`;
}

/** A sheet key as a short number: the page number in a one-book project —
 *  unchanged — and ``400·6`` (series · page in that book) with several. */
export function sheetNo(p: any, key: number | null | undefined): string {
  if (key == null) return "";
  if (!p?.multi_book) return String(key);
  const pg = p?.pages?.find((x: any) => x.number === key);
  return pg ? `${bookSeries(p, pg.drawing_id)}·${pg.page}` : String(key);
}

/** A book's series — ``400`` for ``VAA_400_XXX_OP.pdf`` — or its file name
 *  when the name carries none (4.1.0). The same rule as ``app.sheets``. */
/** The sheet a person typed (4.2.0): a page number in a one-book project; with
 *  several books the series and the page as the pager shows them ("600 12",
 *  "600 · Page 12", "600 p12") or the drawing number ("VBB-612-GLF-O").
 *  ``null`` when no sheet answers to it. */
export function sheetFromText(p: any, text: string): number | null {
  const raw = String(text || "").trim();
  if (!raw || !p?.pages?.length) return null;
  const byNumber = p.pages.find((pg: any) =>
    pg.sheet_number && String(pg.sheet_number).toUpperCase() === raw.toUpperCase());
  if (byNumber) return byNumber.number;
  if (!p.multi_book) {
    const n = Number(raw);
    return p.pages.some((pg: any) => pg.number === n) ? n : null;
  }
  const m = raw.match(/^(\S+?)\s*(?:[·.\-]|\s)\s*(?:page|p\.?|pg)?\s*(\d+)$/i);
  if (!m) return null;
  const series = m[1].toUpperCase();
  const page = Number(m[2]);
  const hit = p.pages.find((pg: any) =>
    String(bookSeries(p, pg.drawing_id)).toUpperCase() === series && pg.page === page);
  return hit ? hit.number : null;
}

export function bookSeries(p: any, drawingId: number | null | undefined): string {
  const d = p?.drawings?.find((x: any) => x.id === drawingId);
  const stem = String(d?.filename || "").replace(/\.pdf$/i, "");
  const m = stem.match(/(?:^|[_\-\s])(\d{3})(?:[_\-\s]|$)/);
  return m ? m[1] : stem;
}

/**
 * A hand-off between screens (2.12.0): the Exceptions step and the Drawing
 * viewer open *Demand per space* already searched for one space, and the
 * Exceptions step opens the viewer searched for one name. Module state, read
 * once by the screen that opens — there is one browser tab and one person.
 */
export const HANDOFF: {
  demand: string;
  viewer: string;
  /** A sheet to open the viewer on, and the point to centre and ring there
   *  (4.4.0) — what a page button on NERs and TEAP × drawing sends. */
  viewerAt: { page: number; x?: number | null; y?: number | null } | null;
} = { demand: "", viewer: "", viewerAt: null };

/** Open the Drawing viewer on this sheet, centred on this point when given. */
export function openViewerAt(ctx: Ctx, page: number, x?: number | null, y?: number | null) {
  HANDOFF.viewerAt = { page, x, y };
  ctx.go("viewer");
}

/**
 * A page number that is also the way to the sheet (4.4.0). Marcelo, 22 Oct
 * 2026: the page shown on NERs and on TEAP × drawing should take him straight
 * to the drawing, "assim a movimentação entre telas ficaria mais ágil".
 */
export function PageButton({ ctx, page, x, y, pill = false, long = false, title }: {
  ctx: Ctx; page: number | null | undefined; x?: number | null; y?: number | null;
  pill?: boolean; title?: string;
  /** The page's full name ("400 page 6 · VAA-411B3…") instead of the short
   *  number — where the name was shown before it became a button (4.6.0). */
  long?: boolean;
}) {
  if (page == null) return <>—</>;
  const p = ctx.project;
  return (
    <button type="button" className={`linkish${long ? "" : " mono"} page-link${pill ? " pill" : ""}`}
            title={title || `${t("Open")} ${pageName(p, page)} ${t("in the Drawing viewer")}`}
            onClick={() => openViewerAt(ctx, page, x, y)}>
      {pill ? "p. " : ""}{long ? pageName(p, page) : sheetNo(p, page)}
    </button>
  );
}

/** Open the Drawing viewer searched for this name. */
export function openInViewer(ctx: Ctx, query: string) {
  HANDOFF.viewer = query;
  ctx.go("viewer");
}
export function ReportsPanel({ ctx }: { ctx: Ctx }) {
  const srp = useSort<any>();
  const p = ctx.project;
  const [files, setFiles] = useState<{ name: string; url: string }[]>([]);
  const [stray, setStray] = useState<any[]>([]);
  //: The legend box is 320 pt wide, and on a dense CAD sheet it lands on top of
  //  the plan. Turning it off drops the box and the swatches — never the limits,
  //  the scale or the disclaimer, which move to a thin footnote instead.
  const [legend, setLegend] = useState(true);
  const reportJob = useJob();
  if (!p) return null;

  /**
   * Build a report.
   *
   * The marked-up PDF is minutes of work on a full venue book, so it runs as a
   * job with a progress bar naming the page it is on. It used to run inside the
   * request: the single-worker server could serve nothing else while it ran,
   * and the button gave no sign of life — which is indistinguishable, from the
   * outside, from a server that has died. The workbook and the CSV are quick,
   * so they stay synchronous.
   */
  const finish = async (r: any, fmt: string) => {
    setFiles((f) => [{ name: r.file, url: r.download }, ...f.filter((x) => x.name !== r.file)]);
    if (r.off_page_count) {
      setStray(r.off_page || []);
      ctx.notify(
        `${r.off_page_count} ${t("item(s) sit outside their page — re-import the drawing")}`,
        "err");
    } else {
      setStray([]);
      ctx.notify(`${fmt.toUpperCase()} — ${r.file}`);
    }
  };

  const make = async () => {
    try {
      await reportJob.start(() => api.report(p.id, "pdf", true, legend, false), async (j) => {
        if (j.state === "error") { ctx.notify(j.error, "err"); return; }
        if (j.state !== "done") return;
        await finish(j.result, "pdf");
      });
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  //: The composite map PDF (5.0.0), beside the marked-up one, never instead.
  const makeComposite = async () => {
    try {
      await reportJob.start(() => api.compositeReport(p.id, legend), async (j) => {
        if (j.state === "error") { ctx.notify(j.error, "err"); return; }
        if (j.state !== "done") return;
        await finish(j.result, "pdf");
      });
    } catch (e: any) { ctx.notify(e.message, "err"); }
  };

  return (
    <>
      <div className="card">
        <h2>{t("Report (PDF)")}</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          {t("The marked-up PDF is the book with everything you drew on it — NERs with their reach circles, TERs, IDFs, COWs, DIVA nodes, Wi-Fi points and named areas — and the legend. The composite map PDF has one page per composite map you built.")}
        </p>
        {/* The scenario picker is gone with the As-Is vs Proposed screen.
            There is one design — the drawing, with whatever you have placed on
            it — so every export is of that, and a control offering to export
            "the other one" had nothing to offer. */}
        <div className="row">
          <button className="primary" disabled={reportJob.busy}
                  onClick={() => void make()}>{t("Marked-up PDF")}</button>
          <label style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 0 }}
                 title={t("The legend box is 320 pt wide and on a dense sheet it sits over the drawing. Turned off, the limits, the scale and the disclaimer stay as two small lines along the foot of the page (three with the no-coverage warning).")}>
            <input type="checkbox" checked={legend} style={{ width: "auto" }}
                   onChange={(e) => setLegend(e.target.checked)} />
            {t("with legend")}
          </label>
          <button disabled={reportJob.busy}
                  title={t("One page per composite map built (Composite map card below): the sheets of a level as one map, every NER with its reach and the Wi-Fi points. The legend on the first page only.")}
                  onClick={() => void makeComposite()}>{t("Composite map PDF")}</button>
        </div>
        <div style={{ marginTop: 10 }}>
          <JobBar job={reportJob.job} onCancel={reportJob.cancel}
                  onDismiss={reportJob.clear} />
        </div>
        <p className="muted small" style={{ marginBottom: 0 }}>
          {t("The marked-up PDF marks every page of the book, so on a large venue it takes a minute or two. It runs in the background — the progress bar names the page it is on, and the rest of the app stays usable.")}
        </p>
        {files.length > 0 && (
          <ul style={{ marginTop: 12 }}>
            {files.map((f) => (
              <li key={f.name}><a href={f.url} download>{f.name}</a></li>
            ))}
          </ul>
        )}
        {stray.length > 0 && (
          <div className="warn-box" style={{ marginTop: 12 }}>
            <strong>{t("Positions outside the page")}</strong>
            <p className="small" style={{ margin: "6px 0" }}>
              {t("These were marked where they are stored, not moved. A drawing imported before the page-rotation correction keeps the old coordinates — re-import it and they will fall into place.")}
            </p>
            <table className="small">
              <thead>
                <tr><srp.Th k="kind">{t("Kind")}</srp.Th><srp.Th k="label">{t("Label")}</srp.Th><srp.Th k="page">{t("Page")}</srp.Th>
                  <srp.Th k="x">X</srp.Th><srp.Th k="y">Y</srp.Th>
                  <srp.Th k="page_width">{t("Page size")}</srp.Th></tr>
              </thead>
              <tbody>
                {srp.apply(stray).slice(0, 20).map((s: any, i: number) => (
                  <tr key={i}>
                    <td>{s.kind}</td><td>{s.label}</td><td>{sheetNo(p, s.page)}</td>
                    <td>{s.x}</td><td>{s.y}</td>
                    <td>{s.page_width} × {s.page_height}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <CompositeCard ctx={ctx} />

      {/* The audit log used to be printed here, under the exports. It is the
          record of who changed what and why — an administration matter, not a
          report — and it now lives on the Account & security screen with the
          accounts and the sessions. */}
    </>
  );
}

