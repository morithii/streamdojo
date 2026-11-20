// =============================================================================
// Small helpers shared by every page.
// Safety: text from the API is always inserted as text (never innerHTML),
// so a lesson title or an answer can't inject script into the page (XSS).
// =============================================================================

// Build an element: el("p", { className: "x" }, "text", childElement)
export function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

export function formatDuration(ms) {
  if (!ms) return "";
  const minutes = Math.round(ms / 60000);
  return minutes < 1 ? "< 1 min" : `${minutes} min`;
}

// "01:15" -> 75 seconds
export function parseTimestamp(text) {
  const [minutes, seconds] = String(text || "0:00").split(":").map(Number);
  return (minutes || 0) * 60 + (seconds || 0);
}

// "2026-11-21T12:00:00Z" -> "tomorrow" / "in 3 days" / "today"
export function whenFromNow(iso) {
  const days = Math.round((Date.parse(iso) - Date.now()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}
