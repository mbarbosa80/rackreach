import { useEffect } from "react";
import type { JobState } from "../lib/useJob";
import { t } from "../lib/i18n";

/** How long a finished job's bar stays before it closes itself (5.2.0). */
const DONE_LINGER_MS = 4000;

/**
 * Progress for a long operation, with the stage in words and a cancel button.
 *
 * The stage text matters as much as the bar: "reading page 12 of 34" tells the
 * user the tool is working and roughly how long is left, which a spinner never
 * does. When a job fails the error stays on screen instead of vanishing.
 */
export function JobBar({ job, onCancel, onDismiss }: {
  job: JobState | null;
  onCancel?: () => void;
  onDismiss?: () => void;
}) {
  // A finished job closes itself: what it found is already in the notice.
  // An error stays until it is dismissed.
  const doneAt = job && !job.running && job.state === "done" ? job.id || "done" : null;
  useEffect(() => {
    if (!doneAt || !onDismiss) return;
    const h = window.setTimeout(onDismiss, DONE_LINGER_MS);
    return () => window.clearTimeout(h);
  }, [doneAt]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!job) return null;

  const pct = Math.round((job.progress || 0) * 100);
  const tone =
    job.state === "error" ? "err" : job.state === "done" ? "ok" : "";

  return (
    // Over the page, never in its flow (5.2.0): the bar used to open and close
    // inside the screen and push the worklist down and up under the pointer.
    <div role="status" className={`banner jobbar ${tone}`} style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ flex: "0 0 auto" }}>
          {job.state === "error" ? t("Failed")
            : job.state === "cancelled" ? t("Cancelled")
            : job.state === "done" ? t("Finished")
            : t("Working")}
        </strong>
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}>
          {/* The server's last stage word is "finished" once it is done —
              beside "Finished" it read as a stutter. */}
          {job.error || (job.stage && !["finished", "done", "cancelled", job.state]
            .includes(job.stage.toLowerCase()) ? job.stage : "") || (job.running ? "…" : "")}
        </span>
        {job.running && <span className="mono">{pct}%</span>}
        {job.running && onCancel && (
          <button className="small danger" onClick={onCancel}>{t("Cancel")}</button>
        )}
        {!job.running && onDismiss && (
          <button className="small" onClick={onDismiss}>{t("Dismiss")}</button>
        )}
      </div>
      {job.running && (
        <div style={{ height: 6, background: "#0d1017", borderRadius: 4, overflow: "hidden" }}>
          <div
            style={{
              width: `${Math.max(3, pct)}%`,
              height: "100%",
              background: "var(--accent)",
              transition: "width 0.35s ease",
            }}
          />
        </div>
      )}
    </div>
  );
}
