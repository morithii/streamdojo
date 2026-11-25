// =============================================================================
// Gradings: the belt exam for a whole course (one belt per grading).
//   beltBadge(belt)            a small coloured belt, used on course cards and pages
//   gradingPanel(topicId)      the "Your belt" box on a course page: eligibility, cooldown, start button
//   #/grading/<topicId>        the exam itself: rules -> one question at a time -> result + sensei verdict
// The API (src/marking/grading.py) decides everything that matters - eligibility, timing,
// marking and the belt award. This page only shows what it says and sends the answers.
// During the exam there are no hints, no rewatching and no per-question feedback;
// the breakdown appears on the result screen once the whole grading is marked.
// =============================================================================
import { el, sensei } from "./dom.js";
import { api } from "./api.js";

const enc = encodeURIComponent;

// Belt order and colours - same order as BELTS in grading.py
export const BELTS = ["WHITE", "YELLOW", "ORANGE", "GREEN", "BLUE", "BROWN", "BLACK"];
const BELT_COLOUR = {
  WHITE: "#f4f4f4", YELLOW: "#f5d547", ORANGE: "#f39a3b", GREEN: "#3fae5a",
  BLUE: "#3b7dd8", BROWN: "#8a5a3b", BLACK: "#111111",
};

// "GREEN" -> "Green"
export const beltName = (belt) => belt ? belt.charAt(0) + belt.slice(1).toLowerCase() : "";

// A small belt: a coloured bar with a knot. The text label keeps it readable without colour.
export function beltBadge(belt = "WHITE", size = "small") {
  return el("span", { className: `belt ${size}`, title: `${beltName(belt)} belt` },
    el("span", { className: "belt-bar", style: `--belt: ${BELT_COLOUR[belt] || BELT_COLOUR.WHITE}`, ariaHidden: "true" },
      el("span", { className: "knot" })),
    el("span", { className: "belt-label" }, `${beltName(belt)} belt`),
  );
}

// "2026-11-28T09:00:00Z" -> "Sat 28 Nov, 09:00" in the learner's own time zone
function when(iso) {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------------------
// The "Your belt" panel on the course page
// ---------------------------------------------------------------------------
export async function gradingPanel(topicId) {
  let status;
  try {
    status = await api(`/learn/grading/${enc(topicId)}`);
  } catch {
    return null;                                   // no published lessons yet: show no panel
  }
  const { ideas, practised, remembered } = status.requirements;
  const box = el("div", { className: "grading-panel" },
    el("div", { className: "grading-head" },
      el("div", {}, el("p", { className: "eyebrow" }, "Your belt"), beltBadge(status.belt, "big")),
      status.nextBelt
        ? el("p", { className: "meta" }, `Next: ${beltName(status.nextBelt)} belt · pass mark ${status.passMark}% · ${status.questionCount} questions · 60 minutes`)
        : el("p", { className: "meta" }, "Black belt. ClipSensei has nothing left to grade here."),
    ),
  );
  if (!status.nextBelt) return box;

  // The two requirements, as ticks the learner can work towards
  box.append(el("ul", { className: "requirements" },
    el("li", { className: practised === ideas ? "met" : "" }, `Practised every idea: ${practised} of ${ideas}`),
    el("li", { className: remembered === ideas ? "met" : "" }, `Remembered after a gap of a few days: ${remembered} of ${ideas}`),
  ));

  const waiting = status.nextGradingAt && new Date(status.nextGradingAt) > new Date();
  if (status.inProgress) {
    box.append(el("a", { className: "btn", href: `#/grading/${enc(topicId)}` }, "Resume your grading"));
  } else if (status.canStart) {
    box.append(el("a", { className: "btn", href: `#/grading/${enc(topicId)}` }, `Grade for ${beltName(status.nextBelt)} belt`));
  } else if (waiting) {
    box.append(el("p", { className: "meta" }, `Next grading opens ${when(status.nextGradingAt)}.`));
  } else {
    box.append(el("p", { className: "meta" }, "Keep practising and doing your reviews. The grading unlocks when both ticks are green."));
  }
  if (status.lastResult) {
    const r = status.lastResult;
    box.append(el("p", { className: "meta last" },
      `Last grading: ${r.score}% for ${beltName(r.targetBelt)} - ${r.passed ? "passed" : "not this time"}.`));
  }
  return box;
}

// ---------------------------------------------------------------------------
// #/grading/<topicId>: the exam
// ---------------------------------------------------------------------------
export async function renderGrading(app, topicId) {
  const status = await api(`/learn/grading/${enc(topicId)}`);
  const back = el("a", { className: "back", href: `#/course/${enc(topicId)}` }, `← ${status.topicName}`);

  // Already sitting one (e.g. the page was refreshed): carry straight on
  if (status.inProgress) return runExam(app, topicId, status, status.inProgress, back);

  if (!status.canStart) {
    app.replaceChildren(el("section", { className: "grading" }, back,
      el("h1", {}, "Grading"),
      sensei(status.nextBelt ? "Not yet. Look at the course page to see what's still needed." : "You hold the black belt."),
    ));
    return;
  }

  // The rules, shown once before the clock starts
  const start = el("button", { className: "btn big", type: "button" }, `Start the ${beltName(status.nextBelt)} belt grading`);
  const message = el("p", { className: "status", role: "status" });
  start.addEventListener("click", async () => {
    start.disabled = true;
    message.textContent = "ClipSensei is choosing your questions...";
    try {
      const session = await api(`/learn/grading/${enc(topicId)}/start`, { method: "POST", body: {} });
      runExam(app, topicId, status, session, back);
    } catch (error) {
      message.textContent = error.message;           // e.g. the cooldown just started on another device
      start.disabled = false;
    }
  });
  app.replaceChildren(el("section", { className: "grading" }, back,
    el("div", { className: "page-head" },
      el("div", {}, el("p", { className: "eyebrow" }, "Grading"), el("h1", {}, status.topicName)),
      sensei(`Show me what you remember. ${status.passMark}% earns the ${beltName(status.nextBelt)} belt.`),
    ),
    el("div", { className: "belt-step" }, beltBadge(status.belt, "big"), el("span", { ariaHidden: "true" }, "→"), beltBadge(status.nextBelt, "big")),
    el("ul", { className: "rules" },
      el("li", {}, `${status.questionCount} questions from across the whole course, one at a time.`),
      el("li", {}, "60 minutes from when you start. Unanswered questions score zero."),
      el("li", {}, "No hints and no rewatching. If you don't remember, say so and move on."),
      el("li", {}, "Each answer is final once sent. Your results appear at the end."),
      el("li", {}, "If you don't pass, you can grade again after 3 days."),
    ),
    start, message,
  ));
}

// One question at a time until all are answered (or the time runs out)
function runExam(app, topicId, status, session, back) {
  const remaining = session.questions.filter((q) => !session.answered.includes(q.index));
  let position = session.questions.length - remaining.length;      // how many are already done
  const clock = el("span", { className: "clock", role: "timer" });
  const ticker = setInterval(tick, 1000);
  function tick() {
    const left = Math.max(0, new Date(session.expiresAt) - new Date());
    const m = Math.floor(left / 60000), s = Math.floor((left % 60000) / 1000);
    clock.textContent = `${m}:${String(s).padStart(2, "0")} left`;
    if (left === 0) { clearInterval(ticker); finishOnTimeout(); }
  }
  window.addEventListener("hashchange", () => clearInterval(ticker), { once: true });   // stop the clock when leaving

  async function finishOnTimeout() {
    // The server finishes expired gradings itself; reading the status makes it do so now
    const after = await api(`/learn/grading/${enc(topicId)}`);
    showResult(app, topicId, after.lastResult, back);
  }

  function ask() {
    const question = remaining.shift();
    if (!question) return finishOnTimeout();                        // nothing left (all answered elsewhere)
    position += 1;
    const answer = el("textarea", { className: "answer", rows: 6, placeholder: "Explain it in your own words..." });
    const send = el("button", { className: "btn", type: "button" }, "Send answer");
    const skip = el("button", { className: "btn ghost", type: "button" }, "I don't remember");
    const message = el("p", { className: "status", role: "status" });

    async function submit(dontRemember) {
      if (!dontRemember && answer.value.trim().length < 3) { message.textContent = "Write a little more, or choose 'I don't remember'."; return; }
      send.disabled = skip.disabled = answer.disabled = true;
      message.textContent = "ClipSensei is marking...";
      try {
        const reply = await api(`/learn/grading/${enc(topicId)}/answer`, {
          method: "POST", body: { gradingId: session.gradingId, index: question.index, answer: answer.value, dontRemember },
        });
        if (reply.finished) { clearInterval(ticker); return showResult(app, topicId, reply.result, back); }
        ask();
      } catch (error) {
        message.textContent = error.message;
        send.disabled = skip.disabled = answer.disabled = false;
      }
    }
    send.addEventListener("click", () => submit(false));
    skip.addEventListener("click", () => submit(true));

    app.replaceChildren(el("section", { className: "grading" }, back,
      el("div", { className: "exam-bar" },
        el("span", {}, `${beltName(session.targetBelt)} belt grading · question ${position} of ${session.questions.length}`),
        clock),
      el("div", { className: "bar exam-progress" }, el("span", { style: `width: ${Math.round(((position - 1) / session.questions.length) * 100)}%` })),
      el("p", { className: "meta" }, `From: ${question.lessonTitle}`),
      el("h2", { className: "prompt" }, question.prompt),
      answer,
      el("div", { className: "practice-actions" }, send, skip),
      message,
    ));
    answer.focus();
  }
  tick();
  ask();
}

// The result: score, belt (if earned), ClipSensei's verdict, and the breakdown per question
function showResult(app, topicId, result, back) {
  if (!result) { location.hash = `#/course/${enc(topicId)}`; return; }
  const headline = result.passed
    ? `You earned your ${beltName(result.targetBelt)} belt.`
    : `Not this time: ${result.score}%, and the ${beltName(result.targetBelt)} belt needs ${result.passMark}%.`;
  app.replaceChildren(el("section", { className: "grading" }, back,
    el("div", { className: `score ${result.passed ? "good" : ""}` },
      el("h1", {}, `${result.score}%`),
      el("p", {}, headline),
      result.passed ? beltBadge(result.targetBelt, "big") : el("p", { className: "meta" }, "You can grade again in 3 days. Your reviews will get you there."),
    ),
    sensei(result.verdict || (result.passed ? "Well earned." : "Keep training."), "big"),
    el("h2", {}, "Question by question"),
    el("ol", { className: "breakdown" }, ...result.questions.map((q) =>
      el("li", {},
        el("p", { className: "meta" }, q.lessonTitle),
        el("p", {}, el("strong", {}, q.prompt), ` - ${q.answered ? `${Math.round(q.share * 100)}%` : "not answered"}`),
        q.points.length ? el("ul", { className: "points" }, ...q.points.map((p) =>
          el("li", { className: `point ${p.verdict}` },
            el("span", { className: "icon", ariaHidden: "true" }, p.verdict === "covered" ? "✓" : p.verdict === "partial" ? "~" : "✗"),
            el("span", {}, p.point)))) : "",
      ))),
    el("a", { className: "btn", href: `#/course/${enc(topicId)}` }, "Back to the course"),
  ));
}
