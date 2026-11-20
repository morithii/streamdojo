// =============================================================================
// StreamDojo web app (Step 15): sign-in + the Netflix-style home page.
// Step 16 adds the player, the practice screen and the educator dashboard.
//
// Safety: data from the API is always inserted with textContent / attributes,
// never innerHTML, so a lesson title can't inject script into the page (XSS).
// =============================================================================
import { initAuth, signIn, signOut, currentUser } from "./auth.js";
import { api, setApiBase } from "./api.js";

const app = document.getElementById("app");
const account = document.getElementById("account");

// Tiny helper to build elements: el("p", { className: "x" }, "text", childElement)
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function formatDuration(ms) {
  if (!ms) return "";
  const minutes = Math.round(ms / 60000);
  return minutes < 1 ? "< 1 min" : `${minutes} min`;
}

function renderAccount() {
  const user = currentUser();
  account.replaceChildren(
    user
      ? el("span", { className: "who" }, user.name, el("small", {}, user.groups.join(" · ")))
      : "",
    user
      ? el("button", { className: "btn ghost", onclick: signOut }, "Sign out")
      : el("button", { className: "btn", onclick: signIn }, "Sign in"),
  );
}

function renderLanding() {
  app.replaceChildren(
    el("section", { className: "landing" },
      el("h1", {}, "Watch a lesson. Practise with ClipSensei. ", el("span", {}, "See what stuck.")),
      el("p", {}, "Short video lessons, then active recall: you answer from memory, ClipSensei marks each idea with evidence from your own words, and spaced reviews bring back what's fading."),
      el("button", { className: "btn big", onclick: signIn }, "Sign in to start"),
    ),
  );
}

function lessonCard(lesson) {
  return el("article", { className: "card", tabIndex: 0, title: lesson.title },
    lesson.thumbnailUrl
      ? el("img", { src: lesson.thumbnailUrl, alt: "", loading: "lazy" })
      : el("div", { className: "thumb-missing" }),
    el("div", { className: "card-text" },
      el("h3", {}, lesson.title || "Untitled lesson"),
      el("p", {}, formatDuration(lesson.durationMs)),
    ),
  );
}

async function renderHome() {
  const { featured, rows } = await api("/catalogue");
  if (!featured) {
    app.replaceChildren(el("p", { className: "empty" }, "No lessons published yet. Educators publish lessons once ClipSensei's objectives are reviewed."));
    return;
  }
  app.replaceChildren(
    el("section", { className: "hero", style: `--hero: url("${encodeURI(featured.thumbnailUrl || "")}")` },
      el("div", { className: "hero-text" },
        el("p", { className: "eyebrow" }, featured.topicName || "New lesson"),
        el("h1", {}, featured.title),
        el("p", {}, `${formatDuration(featured.durationMs)} · practise with ClipSensei after watching`),
      ),
    ),
    ...rows.map((row) =>
      el("section", { className: "row" },
        el("h2", {}, row.topicName),
        el("div", { className: "row-cards" }, ...row.lessons.map(lessonCard)),
      ),
    ),
  );
}

function renderError(error) {
  app.replaceChildren(el("p", { className: "error" }, error.message || "Something went wrong."));
}

async function start() {
  try {
    const config = await (await fetch("/config.json", { cache: "no-store" })).json();
    setApiBase(config.apiBase);
    await initAuth(config);
    renderAccount();
    if (currentUser()) {
      await renderHome();
    } else {
      renderLanding();
    }
  } catch (error) {
    renderError(error);
  }
}

start();
