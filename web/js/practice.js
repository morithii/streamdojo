// =============================================================================
// Practice with ClipSensei: active recall + spaced repetition.
//
// The video is hidden on purpose. The learner answers each question FROM MEMORY,
// then ClipSensei marks every key idea separately and quotes the learner's own
// words as evidence. After that we show the model answer and a "rewatch" button
// that jumps the video to the exact moment the idea was taught.
//
//   kind "PRACTICE" - first practice right after watching a lesson
//   kind "REVIEW"   - spaced-repetition reviews that are due today
// =============================================================================
import { el, parseTimestamp, whenFromNow, sensei } from "./dom.js";
import { api } from "./api.js";
import { attachStream, stopPlayer } from "./player.js";

const VERDICTS = {
  covered: { icon: "✓", label: "Recalled" },
  partial: { icon: "◐", label: "Partly there" },
  missing: { icon: "✗", label: "Not yet" },
  uncertain: { icon: "?", label: "A teacher will check this" },
};

// Run a list of questions one after another, then show a summary.
//   queue: [{ videoId, objectiveId, promptIndex, prompt, timestamp, title? }]
export function runPractice(app, { queue, kind, heading, doneHref = "#/", doneLabel = "Back to my courses" }) {
  const firstTries = [];      // one result per question: the first attempt only (retries never count)
  let index = 0;

  function askQuestion() {
    stopPlayer();
    const item = queue[index];
    let usedHint = false;
    const answer = el("textarea", {
      className: "answer", rows: 6, maxLength: 2000,
      placeholder: "Write what you remember, in your own words. Full sentences work best.",
    });
    const hintBox = el("p", { className: "hint", hidden: true });
    const status = el("p", { className: "status", role: "status" });

    async function submit(extra = {}) {
      const dontRemember = Boolean(extra.dontRemember);
      const retry = Boolean(item.retry);         // after "Try again", EVERY button is a retry - even "I don't remember"
      if (!dontRemember && answer.value.trim().split(/\s+/).length < 3) {
        status.textContent = "Write a little more - at least a sentence - or choose \"I don't remember\".";
        return;
      }
      for (const button of buttons.querySelectorAll("button")) button.disabled = true;
      status.textContent = dontRemember ? "Fetching the answer…" : "ClipSensei is marking your answer…";
      try {
        const result = await api("/learn/attempts", {
          method: "POST",
          body: {
            videoId: item.videoId, objectiveId: item.objectiveId, promptIndex: item.promptIndex,
            answer: dontRemember ? "" : answer.value, usedHint, dontRemember,
            retry, kind,
          },
        });
        if (!retry) firstTries[index] = result;
        showResult(item, answer.value, result);
      } catch (error) {
        status.textContent = error.message;
        for (const button of buttons.querySelectorAll("button")) button.disabled = false;
      }
    }

    const buttons = el("div", { className: "practice-actions" },
      el("button", { className: "btn", onclick: () => submit() }, "Check my answer"),
      el("button", {
        className: "btn ghost",
        onclick: async (e) => {
          e.target.disabled = true;
          usedHint = true;                        // a hint means this recall wasn't unaided
          try {
            const { hint } = await api(`/learn/hint/${encodeURIComponent(item.videoId)}/${encodeURIComponent(item.objectiveId)}`);
            hintBox.textContent = `Hint: ${hint}`;
          } catch (error) {
            hintBox.textContent = error.message;
          }
          hintBox.hidden = false;
        },
      }, "Show a hint"),
      el("button", { className: "btn ghost", onclick: () => submit({ dontRemember: true }) }, "I don't remember"),
    );

    app.replaceChildren(
      el("section", { className: "practice" },
        el("p", { className: "eyebrow" }, `${heading} · question ${index + 1} of ${queue.length}`),
        item.title ? el("p", { className: "meta" }, item.title) : null,
        el("h1", { className: "prompt" }, item.prompt),
        sensei(item.retry
          ? "Try again with what you've just learned. This try won't change your review schedule."
          : "No peeking - the video is hidden on purpose. Pulling an idea out of memory is what makes it stick."),
        answer, hintBox, buttons, status,
      ),
    );
    answer.focus();
  }

  function showResult(item, answerText, result) {
    const covered = result.pointResults.filter((p) => p.verdict === "covered").length;
    const total = result.pointResults.length;
    const rewatchSpot = el("div", { className: "rewatch-player" });

    const schedule = result.nextReviewAt
      ? (result.counted === false
          ? `Early practice - your review stays ${whenFromNow(result.nextReviewAt)}.`
          : `ClipSensei will ask you about this again ${whenFromNow(result.nextReviewAt)}.`)
      : null;

    app.replaceChildren(
      el("section", { className: "practice" },
        el("p", { className: "eyebrow" }, `${heading} · question ${index + 1} of ${queue.length}`),
        el("h1", { className: "prompt" }, item.prompt),
        el("div", { className: `score ${result.recalledAll ? "good" : ""}` },
          el("img", { src: "/img/clipsensei.svg", alt: "", className: "score-sensei" }),
          el("div", {},
            el("strong", {}, result.recalledAll ? "You recalled every key idea." : `${covered} of ${total} key ideas recalled.`),
            result.feedback ? el("p", {}, result.feedback) : null,
          ),
        ),
        el("ul", { className: "points" },
          ...result.pointResults.map((p) => {
            const verdict = VERDICTS[p.verdict] || VERDICTS.uncertain;
            return el("li", { className: `point ${p.verdict}` },
              el("span", { className: "icon", ariaHidden: "true" }, verdict.icon),
              el("div", {},
                el("p", { className: "point-text" }, p.point),
                el("p", { className: "verdict" }, verdict.label),
                p.evidence ? el("blockquote", {}, `Your words: “${p.evidence}”`) : null,
              ),
            );
          }),
        ),
        result.needsReview ? el("p", { className: "meta" }, "ClipSensei wasn't fully sure about this mark, so your teacher will double-check it.") : null,
        el("details", { className: "model", open: !result.recalledAll },
          el("summary", {}, "Model answer"),
          el("p", {}, result.modelAnswer),
        ),
        rewatchSpot,
        schedule ? el("p", { className: "meta" }, schedule) : null,
        el("div", { className: "practice-actions" },
          el("button", {
            className: "btn ghost",
            onclick: async (e) => {
              e.target.disabled = true;
              const lesson = await api(`/catalogue/${encodeURIComponent(item.videoId)}`);
              const video = el("video", { controls: true, playsInline: true, autoplay: true });
              rewatchSpot.replaceChildren(video);
              attachStream(video, lesson.hlsUrl, parseTimestamp(result.rewatchAt));
            },
          }, `▶ Rewatch from ${result.rewatchAt}`),
          result.recalledAll ? null : el("button", {
            className: "btn ghost",
            onclick: () => { queue[index] = { ...item, retry: true }; askQuestion(); },
          }, "Try again"),
          el("button", {
            className: "btn",
            onclick: () => { index += 1; index < queue.length ? askQuestion() : showSummary(); },
          }, index + 1 < queue.length ? "Next question" : "Finish"),
        ),
      ),
    );
  }

  function showSummary() {
    stopPlayer();
    const answered = firstTries.filter(Boolean);
    const full = answered.filter((r) => r.recalledAll).length;
    const noun = answered.length === 1 ? "question" : "questions";
    app.replaceChildren(
      el("section", { className: "practice" },
        el("p", { className: "eyebrow" }, heading),
        el("h1", {}, full === answered.length
          ? `Every key idea recalled. Well done.`
          : `${full} of ${answered.length} ${noun} fully recalled on the first try.`),
        el("p", { className: "meta" }, "Ideas you missed come back tomorrow; ideas you recalled come back later and later (1 → 3 → 7 → 14 → 30 days)."),
        el("a", { className: "btn", href: doneHref }, doneLabel),
      ),
    );
  }

  if (!queue.length) {
    app.replaceChildren(el("section", { className: "practice" },
      el("h1", {}, "Nothing to practise right now."),
      el("a", { className: "btn", href: "#/" }, "Back to lessons")));
    return;
  }
  askQuestion();
}
