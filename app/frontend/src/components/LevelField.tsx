/**
 * The level of a NER (5.8.0 - asked by two analysts, 17 Nov 2026:
 * "a manual drop down like indoor/outdoor"). One field, used on the NERs
 * screen and in the Drawing viewer's Inspector: a list of the project's
 * levels (the ones its sheets state) and free text for any other ("B1",
 * "Mezzanine"). What is typed is kept as chosen by hand; empty gives the NER
 * back the level of its sheet. A NER drawn on sheets of two different levels
 * says so under the field.
 */
import { useEffect, useMemo, useState } from "react";
import { t } from "../lib/i18n";

/** A level as something to compare: "LEVEL 02" = "Level 2" = "LEVEL2" = "L2"
 *  = "2"; "LEVEL -1" = "L-1" (a basement) stays apart from "LEVEL 1". Same
 *  rule as the server's ``level_key`` (5.9.0, F2-2). */
export function levelKey(v: string | null | undefined): string {
  let s = String(v || "").trim().toUpperCase().replace(/\s+/g, " ");
  if (!s) return "";
  s = s.replace(/^(?:(?:LEVEL|LVL|FLOOR|NIVEL|NÍVEL)(?:\s*:\s*|-(?=[A-Z]?\d)|\s+|(?=-?[A-Z]?\d))|L(?=-?\d))/, "").replace(/ /g, "");
  const m = s.match(/^(-?)([A-Z]?)0*(\d+)([A-Z]?)$/);
  return m ? `${m[1]}${m[2]}${Number(m[3])}${m[4]}` : s;
}

/** The levels the project's sheets state, one spelling each, in reading order. */
export function projectLevels(p: any, extra: (string | null | undefined)[] = []): string[] {
  const seen = new Map<string, string>();
  for (const v of [...(p?.pages || []).map((pg: any) => pg.level), ...extra]) {
    const s = String(v || "").trim();
    if (s && !seen.has(levelKey(s))) seen.set(levelKey(s), s);
  }
  const coll = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  return [...seen.values()].sort((a, b) => coll.compare(levelKey(a), levelKey(b)));
}

export function LevelField({ id, value, source, sheetLevel, pageLevels = [], levels = [], disabled, onSave }: {
  id: string; value: string | null | undefined; source?: string | null; sheetLevel?: string | null;
  pageLevels?: string[]; levels?: string[]; disabled?: boolean;
  onSave: (v: string | null) => Promise<any> | void;
}) {
  const [v, setV] = useState<string>(value || "");
  // 5.8.0-rc2 (tester F-1): after every save the field shows what was stored
  // ("2" -> "LEVEL 02"; emptied -> the sheet's level), even when the stored
  // value equals the one before.
  const [tick, setTick] = useState(0);
  useEffect(() => setV(value || ""), [value, source, tick]);
  const distinct = useMemo(() => {
    const m = new Map<string, string>();
    for (const l of pageLevels) if (l && !m.has(levelKey(l))) m.set(levelKey(l), l);
    return [...m.values()];
  }, [pageLevels]);
  const listId = `levels-${id}`;
  const commit = () => {
    if (v.trim() === (value || "").trim()) return;
    void Promise.resolve(onSave(v.trim() ? v.trim() : null))
      .catch(() => undefined).finally(() => setTick((n) => n + 1));
  };
  return (
    <div className="level-field">
      <input list={listId} value={v} disabled={disabled} placeholder={sheetLevel || t("level?")}
             title={t("The NER's level: pick one the sheets state or type another (B1, Mezzanine). Empty gives it back the level of its sheet. Goes to the NERs sheet of the workbook.")}
             onChange={(e) => setV(e.target.value)} onBlur={commit}
             onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      <datalist id={listId}>
        {levels.map((l) => <option key={l} value={l} />)}
      </datalist>
      {source === "manual" && <small className="muted">{t("by you")}</small>}
      {distinct.length > 1 && (
        <small className="warn" title={t("Drawn on sheets of different levels. The level shown is the one chosen or the one of its main sheet.")}>
          {t("drawn on")} {distinct.join(" / ")}
        </small>
      )}
    </div>
  );
}
