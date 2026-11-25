// =============================================================================
// Dojo stars and points - shown after every answer, totalled in the header.
// The API decides the score (marking); this file only displays it.
// =============================================================================
import { el } from "./dom.js";
import { api } from "./api.js";

let total = null;                                   // cached running total for this browser session
let loading = null;

// The header chip: "★ 42". Loads the total once from /me, then updates live after each scored answer.
export function dojoChip() {
  const count = el("span", { className: "dojo-count" }, total == null ? "–" : String(total));
  const chip = el("span", { className: "dojo-chip", title: "Your dojo points" }, el("span", { ariaHidden: "true" }, "★ "), count,
    el("span", { className: "visually-hidden" }, " dojo points"));
  if (total == null) {
    loading = loading || api("/me").then((me) => { total = me.dojoPoints || 0; }).catch(() => { total = 0; });
    loading.then(() => { count.textContent = String(total); });
  }
  return chip;
}

export function setDojoTotal(value) {
  if (value == null) return;
  total = value;
  for (const node of document.querySelectorAll(".dojo-count")) node.textContent = String(total);
}

// ★★☆ - three stars, filled up to `stars`
export function starRow(stars) {
  const row = el("span", { className: "stars", role: "img", ariaLabel: `${stars} of 3 dojo stars` });
  row.setAttribute("aria-label", `${stars} of 3 dojo stars`);
  for (let i = 1; i <= 3; i += 1) row.append(el("span", { className: i <= stars ? "star on" : "star", ariaHidden: "true" }, "★"));
  return row;
}

// The line under the stars: what this answer earned, and why
export function pointsLine(result) {
  if (result.dojoPoints > 0) {
    const why = result.multiplier === 3 ? " ×3 - you remembered it after 2+ weeks"
      : result.multiplier === 2 ? " ×2 - you remembered it after a few days" : "";
    return el("p", { className: "points-earned" }, el("strong", {}, `+${result.dojoPoints} dojo points`), why);
  }
  if (result.stars > 0 && result.counted === false) {
    return el("p", { className: "meta" }, "Early practice earns stars but no points - come back when it's due.");
  }
  return null;                                       // retries and "I don't remember": no points, no fuss
}
