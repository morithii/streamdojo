// =============================================================================
// Educator leaderboard: #/teach/leaderboard/<topicId>
// The whole class for one course, ranked by dojo points. Educators only: the API
// (GET /insights/leaderboard/{topicId}) returns 403 to anyone else - hiding the link is not the protection.
// Learners never see this page or each other's ranks.
//   - course picker (every published course)
//   - belt strip: how many learners hold each belt
//   - quick filters: Ready to grade / Falling behind / Failed last grading
//   - sort by any column
//   - "Hide names": initials only, for showing the board on a projector
// =============================================================================
import { el, sensei } from "./dom.js";
import { api } from "./api.js";
import { beltBadge, beltName, BELTS } from "./belt.js";

const enc = encodeURIComponent;
const HIDE_NAMES_KEY = "streamdojo.leaderboard.hideNames";

// Remembered per browser; storage can be unavailable (private mode), so always fall back
function hideNamesSaved() {
  try { return localStorage.getItem(HIDE_NAMES_KEY) === "1"; } catch { return false; }
}
function saveHideNames(on) {
  try { localStorage.setItem(HIDE_NAMES_KEY, on ? "1" : "0"); } catch { /* not saved: fine */ }
}

// "Sam Kariuki" -> "S. K."
const initials = (name) => name.split(/\s+/).filter(Boolean).map((part) => `${part[0].toUpperCase()}.`).join(" ");

// "2026-11-24T09:00:00Z" -> "yesterday" / "3 days ago"
function ago(iso) {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

// The columns: label, how to sort, and how to show a learner's value
const COLUMNS = [
  { key: "rank", label: "#", sort: (l) => l.rank },
  { key: "name", label: "Learner", sort: (l) => l.displayName.toLowerCase() },
  { key: "belt", label: "Belt", sort: (l) => -BELTS.indexOf(l.belt) },
  { key: "points", label: "Dojo points", sort: (l) => -l.dojoPoints },
  { key: "practised", label: "Ideas practised", sort: (l) => -l.practised },
  { key: "due", label: "Due reviews", sort: (l) => -l.dueReviews },
  { key: "active", label: "Last active", sort: (l) => -(l.lastActiveAt ? new Date(l.lastActiveAt).getTime() : 0) },
];

const FILTERS = [
  { key: "all", label: "Everyone", test: () => true },
  { key: "readyToGrade", label: "Ready to grade", test: (l) => l.readyToGrade },
  { key: "fallingBehind", label: "Falling behind", test: (l) => l.fallingBehind },
  { key: "failedLastGrading", label: "Failed last grading", test: (l) => l.failedLastGrading },
];

export async function renderLeaderboard(app, topicId) {
  const { rows } = await api("/catalogue");
  if (!rows.length) {
    app.replaceChildren(el("section", { className: "teach" },
      el("a", { className: "back", href: "#/teach" }, "← Your dojo"), el("h1", {}, "Leaderboard"),
      el("p", { className: "meta" }, "No published courses yet.")));
    return;
  }
  if (!topicId || !rows.some((r) => r.topicId === topicId)) {
    location.replace(`#/teach/leaderboard/${enc(rows[0].topicId)}`);   // default: the first course
    return;
  }
  const data = await api(`/insights/leaderboard/${enc(topicId)}`);
  const state = { sort: "rank", filter: "all", hideNames: hideNamesSaved() };

  // Course picker
  const picker = el("select", { className: "picker", ariaLabel: "Course" },
    ...rows.map((r) => el("option", { value: r.topicId, selected: r.topicId === topicId }, r.topicName)));
  picker.addEventListener("change", () => { location.hash = `#/teach/leaderboard/${enc(picker.value)}`; });

  // Hide names (initials only)
  const hide = el("input", { type: "checkbox", checked: state.hideNames });
  hide.addEventListener("change", () => { state.hideNames = hide.checked; saveHideNames(hide.checked); draw(); });

  const filterBar = el("div", { className: "filter-chips", role: "group", ariaLabel: "Show" });
  const tableArea = el("div", { className: "board-wrap" });

  function draw() {
    // Filter chips, each with its count
    filterBar.replaceChildren(...FILTERS.map((f) => {
      const count = f.key === "all" ? data.counts.learners : data.counts[f.key];
      const chip = el("button", { type: "button", className: `chip ${state.filter === f.key ? "on" : ""}`, ariaPressed: String(state.filter === f.key) },
        `${f.label} `, el("span", { className: "count" }, String(count)));
      chip.addEventListener("click", () => { state.filter = f.key; draw(); });
      return chip;
    }));

    const filter = FILTERS.find((f) => f.key === state.filter);
    const column = COLUMNS.find((c) => c.key === state.sort);
    const shown = data.learners.filter(filter.test)
      .sort((a, b) => (column.sort(a) > column.sort(b) ? 1 : column.sort(a) < column.sort(b) ? -1 : a.rank - b.rank));

    if (!shown.length) {
      tableArea.replaceChildren(el("p", { className: "meta" },
        data.learners.length ? "Nobody matches this filter right now." : "No learners have practised this course yet."));
      return;
    }
    const header = el("tr", {}, ...COLUMNS.map((c) => {
      const button = el("button", { type: "button", className: "sort" }, c.label, state.sort === c.key ? " ▾" : "");
      button.addEventListener("click", () => { state.sort = c.key; draw(); });
      return el("th", { scope: "col", ariaSort: state.sort === c.key ? "descending" : "none" }, button);
    }));
    tableArea.replaceChildren(el("table", { className: "board" },
      el("thead", {}, header),
      el("tbody", {}, ...shown.map((l) => el("tr", {},
        el("td", { className: "rank" }, String(l.rank)),
        el("td", {},
          el("strong", {}, state.hideNames ? initials(l.displayName) : l.displayName),
          ...[l.readyToGrade && "Ready to grade", l.fallingBehind && "Falling behind",
              l.failedLastGrading && `Failed ${beltName(l.lastGrading.targetBelt)} (${l.lastGrading.score}%)`]
            .filter(Boolean).map((tag) => el("span", { className: "tag" }, tag))),
        el("td", {}, beltBadge(l.belt, { size: "sm" })),
        el("td", { className: "num" }, String(l.dojoPoints)),
        el("td", { className: "num" }, `${l.practised} / ${l.ideas}`),
        el("td", { className: `num ${l.overdueReviews ? "warn" : ""}` }, String(l.dueReviews)),
        el("td", {}, ago(l.lastActiveAt)),
      ))),
    ));
  }

  const counts = data.beltCounts;
  app.replaceChildren(el("section", { className: "teach" },
    el("a", { className: "back", href: "#/teach" }, "← Your dojo"),
    el("div", { className: "teach-head" },
      el("div", {}, el("p", { className: "eyebrow" }, "Educator · only you can see this"), el("h1", {}, "Leaderboard")),
      sensei(data.counts.readyToGrade
        ? `${data.counts.readyToGrade} learner${data.counts.readyToGrade === 1 ? " is" : "s are"} ready for their next grading.`
        : "Points reward remembering after a gap, not just turning up."),
    ),
    el("div", { className: "board-controls" },
      el("label", {}, "Course ", picker),
      el("label", { className: "toggle" }, hide, " Hide names (initials only)"),
    ),
    el("div", { className: "belt-strip", ariaLabel: "Learners per belt" },
      ...BELTS.map((belt) => el("div", { className: `belt-count ${counts[belt] ? "" : "zero"}` },
        beltBadge(belt, { size: "sm" }), el("strong", {}, String(counts[belt] || 0))))),
    filterBar,
    tableArea,
  ));
  draw();
}
