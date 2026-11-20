// =============================================================================
// StreamDojo web app. Pages are chosen by the part of the address after "#":
//   #/                 home: reviews due + catalogue rows
//   #/watch/<id>       the player with subtitles
//   #/practise/<id>    practise a lesson with ClipSensei (active recall)
//   #/reviews          today's spaced-repetition reviews
// Coming next: the educator dashboard.
// =============================================================================
import { initAuth, signIn, signOut, currentUser } from "./auth.js";
import { api, setApiBase } from "./api.js";
import { el, formatDuration } from "./dom.js";
import { attachStream, stopPlayer } from "./player.js";
import { runPractice } from "./practice.js";

const app = document.getElementById("app");
const account = document.getElementById("account");
const go = (hash) => () => { location.hash = hash; };
const watch = (videoId) => go(`#/watch/${encodeURIComponent(videoId)}`);

function renderAccount() {
  const user = currentUser();
  account.replaceChildren(
    user ? el("span", { className: "who" }, user.name, el("small", {}, user.groups.join(" · "))) : "",
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

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------
function lessonCard(lesson) {
  const open = watch(lesson.videoId);
  return el("article", {
    className: "card", tabIndex: 0, title: lesson.title, role: "link", onclick: open,
    onkeydown: (e) => { if (e.key === "Enter") open(); },     // keyboard users can open it too
  },
    lesson.thumbnailUrl ? el("img", { src: lesson.thumbnailUrl, alt: "", loading: "lazy" }) : el("div", { className: "thumb-missing" }),
    el("div", { className: "card-text" },
      el("h3", {}, lesson.title || "Untitled lesson"),
      el("p", {}, formatDuration(lesson.durationMs)),
    ),
  );
}

function reviewsBanner(reviews) {
  if (!reviews.length) return null;
  return el("section", { className: "due" },
    el("div", {},
      el("h2", {}, `${reviews.length} review${reviews.length === 1 ? "" : "s"} due today`),
      el("p", {}, "A quick recall now, right as it starts to fade, is what moves an idea into long-term memory."),
    ),
    el("button", { className: "btn big", onclick: go("#/reviews") }, "Start reviews"),
  );
}

async function renderHome() {
  const [{ featured, rows }, { reviews }] = await Promise.all([api("/catalogue"), api("/learn/reviews/due")]);
  if (!featured) {
    app.replaceChildren(
      reviewsBanner(reviews) || "",
      el("p", { className: "empty" }, "No lessons published yet. Educators publish lessons once ClipSensei's objectives are reviewed."),
    );
    return;
  }
  app.replaceChildren(
    el("section", { className: "hero", style: `--hero: url("${encodeURI(featured.thumbnailUrl || "")}")` },
      el("div", { className: "hero-text" },
        el("p", { className: "eyebrow" }, featured.topicName || "New lesson"),
        el("h1", {}, featured.title),
        el("p", {}, `${formatDuration(featured.durationMs)} · practise with ClipSensei after watching`),
        el("button", { className: "btn big", onclick: watch(featured.videoId) }, "▶ Watch"),
      ),
    ),
    reviewsBanner(reviews) || "",
    ...rows.map((row) =>
      el("section", { className: "row" },
        el("h2", {}, row.topicName),
        el("div", { className: "row-cards" }, ...row.lessons.map(lessonCard)),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------
function showSubtitles(video, lang) {
  for (const track of video.textTracks) {
    track.mode = track.language === lang ? "showing" : "disabled";
  }
}

async function renderWatch(videoId) {
  const lesson = await api(`/catalogue/${encodeURIComponent(videoId)}`);
  const video = el("video", { controls: true, playsInline: true, preload: "metadata", poster: lesson.thumbnailUrl || "" });
  for (const sub of lesson.subtitles) {
    video.append(el("track", { kind: "subtitles", src: sub.url, srclang: sub.lang, label: sub.label, default: sub.lang === "en" }));
  }
  const picker = el("select", { className: "picker", ariaLabel: "Subtitles", onchange: (e) => showSubtitles(video, e.target.value) },
    el("option", { value: "off" }, "Subtitles off"),
    ...lesson.subtitles.map((sub) => el("option", { value: sub.lang, selected: sub.lang === "en" }, sub.label)),
  );
  const practise = el("button", { className: "btn", onclick: go(`#/practise/${encodeURIComponent(videoId)}`) }, "Practise with ClipSensei");
  video.addEventListener("ended", () => { practise.textContent = "Lesson finished - practise now"; practise.focus(); });

  app.replaceChildren(
    el("section", { className: "watch" },
      el("a", { className: "back", href: "#/" }, "← All lessons"),
      el("div", { className: "player" }, video),
      el("div", { className: "watch-bar" },
        el("div", {},
          el("p", { className: "eyebrow" }, lesson.topicName || ""),
          el("h1", {}, lesson.title),
          el("p", { className: "meta" }, `${formatDuration(lesson.durationMs)} · ${lesson.objectiveCount || 0} key idea${lesson.objectiveCount === 1 ? "" : "s"} to practise`),
        ),
        el("div", { className: "watch-actions" }, el("label", {}, "Subtitles ", picker), practise),
      ),
    ),
  );
  attachStream(video, lesson.hlsUrl);
  video.textTracks.addEventListener("addtrack", () => showSubtitles(video, picker.value));
  showSubtitles(video, picker.value);
}

// ---------------------------------------------------------------------------
// Practice and reviews
// ---------------------------------------------------------------------------
async function renderPractise(videoId) {
  const lesson = await api(`/learn/lessons/${encodeURIComponent(videoId)}/practice`);
  const queue = lesson.prompts.map((p) => ({ ...p, videoId }));
  runPractice(app, { queue, kind: "PRACTICE", heading: `Practise: ${lesson.title}` });
}

async function renderReviews() {
  const [{ reviews }, { rows }] = await Promise.all([api("/learn/reviews/due"), api("/catalogue")]);
  const titles = Object.fromEntries(rows.flatMap((r) => r.lessons).map((l) => [l.videoId, l.title]));
  const queue = reviews.map((r) => ({ ...r, title: titles[r.videoId] }));
  runPractice(app, { queue, kind: "REVIEW", heading: "Today's reviews" });
}

function renderError(error) {
  app.replaceChildren(el("p", { className: "error" }, error.message || "Something went wrong."));
}

// Show the page that matches the address after "#"
async function route() {
  stopPlayer();                                           // stop streaming when leaving a page
  window.scrollTo(0, 0);
  try {
    if (!currentUser()) return renderLanding();
    const [, page, id] = location.hash.split("/");          // "#/watch/abc" -> ["#", "watch", "abc"]
    if (page === "watch" && id) return await renderWatch(decodeURIComponent(id));
    if (page === "practise" && id) return await renderPractise(decodeURIComponent(id));
    if (page === "reviews") return await renderReviews();
    return await renderHome();
  } catch (error) {
    renderError(error);
  }
}

async function start() {
  try {
    const config = await (await fetch("/config.json", { cache: "no-store" })).json();
    setApiBase(config.apiBase);
    await initAuth(config);
    renderAccount();
    window.addEventListener("hashchange", route);
    await route();
  } catch (error) {
    renderError(error);
  }
}

start();
