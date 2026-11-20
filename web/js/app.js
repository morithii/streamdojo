// =============================================================================
// StreamDojo web app: sign-in, the Netflix-style home page and the lesson player.
// Pages are chosen by the part of the address after "#":
//   #/               home (catalogue rows)
//   #/watch/<id>     the player with subtitles
// Coming next: the practice screen and the educator dashboard.
//
// Safety: data from the API is always inserted with textContent / attributes,
// never innerHTML, so a lesson title can't inject script into the page (XSS).
// =============================================================================
import { initAuth, signIn, signOut, currentUser } from "./auth.js";
import { api, setApiBase } from "./api.js";

const app = document.getElementById("app");
let player = null;          // the current hls.js instance, destroyed when you leave the player
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

const watch = (videoId) => () => { location.hash = `#/watch/${encodeURIComponent(videoId)}`; };

function lessonCard(lesson) {
  const open = watch(lesson.videoId);
  return el("article", {
    className: "card", tabIndex: 0, title: lesson.title, role: "link", onclick: open,
    onkeydown: (e) => { if (e.key === "Enter") open(); },     // keyboard users can open it too
  },
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
        el("button", { className: "btn big", onclick: watch(featured.videoId) }, "▶ Watch"),
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

// ---------------------------------------------------------------------------
// The player: adaptive HLS video (360p/720p/1080p picked automatically by bandwidth)
// with subtitle tracks in every language the pipeline produced.
// ---------------------------------------------------------------------------
function attachStream(video, hlsUrl) {
  if (window.Hls && Hls.isSupported()) {
    player = new Hls({ capLevelToPlayerSize: true });   // don't download 1080p into a small player
    player.loadSource(hlsUrl);
    player.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = hlsUrl;                                  // Safari and iPhone play HLS natively
  } else {
    throw new Error("This browser can't play the video stream.");
  }
}

function showSubtitles(video, lang) {
  for (const track of video.textTracks) {
    track.mode = track.language === lang ? "showing" : "disabled";
  }
}

async function renderWatch(videoId) {
  const lesson = await api(`/catalogue/${encodeURIComponent(videoId)}`);
  const video = el("video", { controls: true, playsInline: true, preload: "metadata",
                              poster: lesson.thumbnailUrl || "", crossOrigin: "anonymous" });
  for (const sub of lesson.subtitles) {
    video.append(el("track", { kind: "subtitles", src: sub.url, srclang: sub.lang, label: sub.label,
                               default: sub.lang === "en" }));
  }
  const picker = el("select", { className: "picker", ariaLabel: "Subtitles",
                                onchange: (e) => showSubtitles(video, e.target.value) },
    el("option", { value: "off" }, "Subtitles off"),
    ...lesson.subtitles.map((sub) => el("option", { value: sub.lang, selected: sub.lang === "en" }, sub.label)),
  );
  app.replaceChildren(
    el("section", { className: "watch" },
      el("a", { className: "back", href: "#/" }, "← All lessons"),
      el("div", { className: "player" }, video),
      el("div", { className: "watch-bar" },
        el("div", {},
          el("p", { className: "eyebrow" }, lesson.topicName || ""),
          el("h1", {}, lesson.title),
          el("p", { className: "meta" }, `${formatDuration(lesson.durationMs)} · ${lesson.objectiveCount || 0} key ideas to practise`),
        ),
        el("div", { className: "watch-actions" },
          el("label", {}, "Subtitles ", picker),
          el("button", { className: "btn", disabled: true, title: "Arrives in the next step" }, "Practise with ClipSensei"),
        ),
      ),
    ),
  );
  attachStream(video, lesson.hlsUrl);
  video.textTracks.addEventListener("addtrack", () => showSubtitles(video, picker.value));
  showSubtitles(video, picker.value);
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
    window.addEventListener("hashchange", route);
    await route();
  } catch (error) {
    renderError(error);
  }
}

// Show the page that matches the address after "#"
async function route() {
  if (player) { player.destroy(); player = null; }        // stop streaming when leaving the player
  window.scrollTo(0, 0);
  try {
    if (!currentUser()) return renderLanding();
    const [, page, id] = location.hash.split("/");          // "#/watch/abc" -> ["#", "watch", "abc"]
    if (page === "watch" && id) return await renderWatch(decodeURIComponent(id));
    return await renderHome();
  } catch (error) {
    renderError(error);
  }
}

start();
