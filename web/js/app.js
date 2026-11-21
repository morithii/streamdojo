// =============================================================================
// StreamDojo web app. Pages are chosen by the part of the address after "#":
//   #/                 My courses (see courses.js)
//   #/course/<topic>   a course's curriculum: lessons 1, 2, 3...
//   #/watch/<id>       a lesson: player + course sidebar + previous/next
//   #/practise/<id>    practise a lesson with ClipSensei (active recall)
//   #/reviews          today's spaced-repetition reviews
//   #/create           upload lessons and follow them through the pipeline (see create.js)
//   #/teach...         the educator dashboard (see teach.js)
// =============================================================================
import { initAuth, signIn, signOut, currentUser } from "./auth.js";
import { api, setApiBase } from "./api.js";
import { el, formatDuration, sensei } from "./dom.js";
import { attachStream, stopPlayer } from "./player.js";
import { runPractice } from "./practice.js";
import { renderTeach, renderReview, renderInsights } from "./teach.js";
import { renderCreate } from "./create.js";
import { renderHome, renderCourse, loadCourses, curriculum, progressBar, watchHref } from "./courses.js";
import { bell } from "./bell.js";

const app = document.getElementById("app");
const account = document.getElementById("account");
const go = (hash) => () => { location.hash = hash; };

// Top-right navigation: one button per role the user has, then Sign out.
// Everyone signed in can learn; Creator and Educator appear only for those Cognito groups.
function renderAccount() {
  const user = currentUser();
  if (!user) {
    account.replaceChildren(el("button", { className: "btn", onclick: signIn }, "Sign in"));
    return;
  }
  const page = location.hash.split("/")[1] || "";
  const section = page === "create" ? "create" : page === "teach" ? "teach" : "learn";
  const link = (key, href, label) => el("a", {
    className: `btn ghost nav ${section === key ? "active" : ""}`, href,
    ariaCurrent: section === key ? "page" : null,
  }, label);
  account.replaceChildren(
    link("learn", "#/", "Learner"),
    user.groups.includes("creators") ? link("create", "#/create", "Creator") : "",
    user.groups.includes("educators") ? link("teach", "#/teach", "Educator") : "",
    bell(),
    el("button", { className: "btn ghost", onclick: signOut }, "Sign out"),
  );
}

function renderLanding() {
  app.replaceChildren(
    el("section", { className: "landing" },
      el("div", {},
        el("h1", {}, "Watch a lesson. Practise with ClipSensei. ", el("span", {}, "See what stuck.")),
        el("p", {}, "Short video lessons, then active recall: you answer from memory, ClipSensei marks each idea with evidence from your own words, and spaced reviews bring back what's fading."),
        el("button", { className: "btn big", onclick: signIn }, "Sign in to start"),
      ),
      sensei("Welcome to the dojo.", "large"),
    ),
  );
}

// ---------------------------------------------------------------------------
// Reviews due (shown on My courses)
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------
function showSubtitles(video, lang) {
  for (const track of video.textTracks) {
    track.mode = track.language === lang ? "showing" : "disabled";
  }
}

async function renderWatch(videoId) {
  const [lesson, courses] = await Promise.all([api(`/catalogue/${encodeURIComponent(videoId)}`), loadCourses()]);
  const course = courses.find((c) => c.lessons.some((l) => l.videoId === videoId));
  const index = course ? course.lessons.findIndex((l) => l.videoId === videoId) : -1;
  const previous = index > 0 ? course.lessons[index - 1] : null;
  const next = course && index < course.lessons.length - 1 ? course.lessons[index + 1] : null;
  const video = el("video", { controls: true, playsInline: true, preload: "metadata", poster: lesson.thumbnailUrl || "" });
  for (const sub of lesson.subtitles) {
    video.append(el("track", { kind: "subtitles", src: sub.url, srclang: sub.lang, label: sub.label, default: sub.lang === "en" }));
  }
  const picker = el("select", { className: "picker", ariaLabel: "Subtitles", onchange: (e) => showSubtitles(video, e.target.value) },
    el("option", { value: "off" }, "Subtitles off"),
    ...lesson.subtitles.map((sub) => el("option", { value: sub.lang, selected: sub.lang === "en" }, sub.label)),
  );
  const practise = el("button", { className: "btn", onclick: go(`#/practise/${encodeURIComponent(videoId)}`) }, "Practise with ClipSensei");
  const coach = sensei("Show me what you have learned.");
  video.addEventListener("ended", () => {
    coach.querySelector(".bubble").textContent = "Lesson complete. Ready when you are.";
    practise.focus();
  });

  // Lesson page: player and actions on the left, the course's curriculum on the right
  app.replaceChildren(
    el("section", { className: "lesson-page" },
      el("div", { className: "lesson-main" },
        el("a", { className: "back", href: course ? `#/course/${encodeURIComponent(course.topicId)}` : "#/" },
          `← ${course ? course.topicName : "My courses"}`),
        el("div", { className: "player frame" }, video),
        el("div", { className: "watch-bar" },
          el("div", {},
            el("p", { className: "eyebrow" }, index >= 0 ? `Lesson ${index + 1} of ${course.lessons.length}` : lesson.topicName || ""),
            el("h1", {}, lesson.title),
            el("p", { className: "meta" }, `${formatDuration(lesson.durationMs)} · ${lesson.objectiveCount || 0} key idea${lesson.objectiveCount === 1 ? "" : "s"} to practise`),
          ),
          el("div", { className: "watch-actions" }, el("label", {}, "Subtitles ", picker), practise, coach),
        ),
        el("nav", { className: "lesson-nav", ariaLabel: "Lesson navigation" },
          previous ? el("a", { className: "btn ghost", href: watchHref(previous.videoId) }, `← Lesson ${index}: ${previous.title}`) : el("span"),
          next ? el("a", { className: "btn", href: watchHref(next.videoId) }, `Next: Lesson ${index + 2} →`) : el("span"),
        ),
      ),
      course ? el("aside", { className: "lesson-aside" },
        el("h2", {}, "Course content"),
        progressBar(course.doneCount, course.lessonCount),
        curriculum(course, videoId),
      ) : null,
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
  runPractice(app, { queue, kind: "PRACTICE", heading: `Practise: ${lesson.title}`, doneHref: watchHref(videoId), doneLabel: "Back to the lesson" });
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
  renderAccount();                                        // highlight the section we're in
  window.scrollTo(0, 0);
  try {
    if (!currentUser()) return renderLanding();
    const [, page, id, sub] = location.hash.split("/");     // "#/watch/abc" -> ["#", "watch", "abc"]
    if (page === "course" && id) return await renderCourse(app, decodeURIComponent(id));
    if (page === "watch" && id) return await renderWatch(decodeURIComponent(id));
    if (page === "practise" && id) return await renderPractise(decodeURIComponent(id));
    if (page === "reviews") return await renderReviews();
    if (page === "teach" && id === "review" && sub) return await renderReview(app, decodeURIComponent(sub));
    if (page === "teach" && id === "insights" && sub) return await renderInsights(app, decodeURIComponent(sub));
    if (page === "teach") return await renderTeach(app);
    if (page === "create") return await renderCreate(app, currentUser());
    return await renderHome(app, reviewsBanner);
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
