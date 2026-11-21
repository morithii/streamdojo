// =============================================================================
// The notification bell (top-right). Reads GET /notifications every 30 seconds while the tab is
// visible; a red badge shows how many are unread. Opening it marks them read; clicking one goes
// straight to the right page (the review page, your uploads, the lesson's insights...).
// The notifications themselves are written by the notifier Lambda when business events happen.
// =============================================================================
import { el } from "./dom.js";
import { api } from "./api.js";

const POLL_MS = 30000;
let root = null;                 // one bell for the whole session, re-used on every page
let timer = null;
let latest = { unread: 0, notifications: [] };

// A simple line-drawn bell that takes the button's text colour (a constant string, never user data)
function bellIcon() {
  const icon = el("span", { className: "bell-icon", ariaHidden: "true" });
  icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>'
    + '<path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  return icon;
}

function timeAgo(iso) {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function draw() {
  const { unread, notifications } = latest;
  const badge = root.querySelector(".badge");
  badge.textContent = unread > 9 ? "9+" : String(unread);
  badge.hidden = unread === 0;
  root.querySelector(".bell-button").ariaLabel = `Notifications, ${unread} unread`;
  root.querySelector(".bell-list").replaceChildren(...(notifications.length
    ? notifications.map((n) => el("li", {},
        el("a", { href: n.link || "#/", className: n.read ? "" : "unread", onclick: () => toggle(false) },
          el("span", {}, n.message),
          el("small", {}, timeAgo(n.createdAt)))))
    : [el("li", { className: "empty" }, "Nothing yet - you'll see reviews, decisions and completed quizzes here.")]));
}

async function refresh() {
  try {
    latest = await api("/notifications");
    draw();
  } catch {
    // signed out or offline: keep the last state, try again next time
  }
}

async function toggle(open = root.querySelector(".bell-panel").hidden) {
  const panel = root.querySelector(".bell-panel");
  panel.hidden = !open;
  root.querySelector(".bell-button").ariaExpanded = String(open);
  if (open && latest.unread) {
    await api("/notifications/read", { method: "POST" }).catch(() => {});
    latest = { unread: 0, notifications: latest.notifications };          // the list stays bold until it closes
    root.querySelector(".badge").hidden = true;
  } else if (!open) {
    latest.notifications = latest.notifications.map((n) => ({ ...n, read: true }));
    draw();
  }
}

export function bell() {
  if (!root) {
    root = el("div", { className: "bell" },
      el("button", { className: "btn ghost bell-button", ariaExpanded: "false", onclick: () => toggle() },
        bellIcon(),
        el("span", { className: "badge", hidden: true }, "0")),
      el("div", { className: "bell-panel", hidden: true, role: "dialog", ariaLabel: "Notifications" },
        el("h2", {}, "Notifications"),
        el("ul", { className: "bell-list" })),
    );
    document.addEventListener("click", (e) => {                // click anywhere else closes it
      if (!root.contains(e.target) && !root.querySelector(".bell-panel").hidden) toggle(false);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggle(false); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    draw();
    refresh();
    timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);   // no polling in background tabs
  }
  return root;
}

export function stopBell() {
  clearInterval(timer);
}
