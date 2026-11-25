// =============================================================================
// "Delete video" - shared by the Creator page and the Educator review page.
// One click opens an inline confirmation (no browser pop-up) that says exactly what will be removed.
// The API hides the video at once; a cleanup function then removes its files and learner data.
// =============================================================================
import { el } from "./dom.js";
import { api } from "./api.js";

// askReason: educators can say why (the creator is told in their bell); onDone runs after a successful delete
export function deleteControl(videoId, title, { askReason = false, onDone, disabledReason = "" } = {}) {
  const status = el("p", { className: "status", role: "status" });
  const reason = askReason ? el("textarea", { rows: 2, maxLength: 500, placeholder: "Optional: why it's being removed (the creator sees this)" }) : null;
  const panel = el("div", { className: "delete-confirm", hidden: true });
  const open = el("button", { type: "button", className: "btn ghost small danger", disabled: Boolean(disabledReason), title: disabledReason },
    "Delete video");

  const confirmButton = el("button", { type: "button", className: "btn small danger solid" }, "Yes, delete it");
  const cancel = el("button", { type: "button", className: "btn ghost small" }, "Cancel");
  panel.append(
    el("p", {}, el("strong", {}, `Delete “${title || "this video"}”?`), " This removes the video, its subtitles, questions and answer keys, ",
      "and every learner's progress on it. It can't be undone."),
    reason ? el("label", { className: "field" }, el("span", {}, "Reason"), reason) : "",
    el("div", { className: "delete-actions" }, confirmButton, cancel),
    status);

  open.onclick = () => { panel.hidden = false; open.hidden = true; confirmButton.focus(); };
  cancel.onclick = () => { panel.hidden = true; open.hidden = false; status.textContent = ""; open.focus(); };
  confirmButton.onclick = async () => {
    confirmButton.disabled = true;
    status.textContent = "Deleting…";
    try {
      await api(`/videos/${encodeURIComponent(videoId)}`, { method: "DELETE", body: reason ? { reason: reason.value.trim() } : {} });
      status.textContent = "Deleted.";
      if (onDone) onDone();
    } catch (error) {
      status.textContent = error.message;           // e.g. "still being processed" - try again later
      confirmButton.disabled = false;
    }
  };
  return el("div", { className: "delete-control" }, open, panel);
}
