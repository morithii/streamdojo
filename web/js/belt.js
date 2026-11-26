// =============================================================================
// The belt: ONE component for every belt in StreamDojo
//   beltBadge(belt, {size})              icon + "Green belt" label (read-only: leaderboard, grading screens)
//   beltButton(belt, topicId, {size})    the same badge as a button: opens "Your belt" (learners only)
//   BELTS, beltName                      the levels, in order, and their display names
//
// The picture is a character in a white karate gi wearing the belt (web/img/belts/, from
// frontend/src/assets/belts/). Every belt sits in the same fixed frame, so all seven line up.
// Black belt is the mastery belt: its frame has a gold border (and a soft gold glow that is
// switched off in high-contrast mode). The picture itself is never changed.
//
// This file only DRAWS belts and SHOWS progress. Every rule (who may grade, pass marks, what counts
// as remembered) is decided by the API in src/marking/grading.py and read from GET /learn/grading/{topic}.
// =============================================================================
import { el } from "./dom.js";
import { api } from "./api.js";

// Levels, names and the belt -> picture mapping live in belt-levels.js (no browser code, so it's testable)
import { BELTS, beltName, beltLevel, beltImage, beltAlt, BELT_IMAGE_SIZE } from "./belt-levels.js";
export { BELTS, beltName, beltImage };

// The picture, in a fixed-size frame. object-fit: contain (CSS) keeps the whole character visible.
function beltIcon(level) {
  return el("span", { className: "belt-art" },
    el("img", {
      src: beltImage(level), alt: beltAlt(level),
      width: BELT_IMAGE_SIZE.width, height: BELT_IMAGE_SIZE.height,
      decoding: "async", draggable: false,
    }));
}

// Read-only badge. size: "sm" (cards, table rows), "md" (panels, dialog), "lg" (grading screens)
export function beltBadge(belt = "WHITE", { size = "sm" } = {}) {
  const level = beltLevel(belt);
  return el("span", { className: `belt belt--${size} belt--${level.toLowerCase()}` },
    beltIcon(level),
    // The picture's alt text already says "Green belt", so screen readers skip the visible label (no double reading)
    el("span", { className: "belt-label", ariaHidden: "true" }, `${beltName(level)} belt`),
  );
}

// The learner's own belt for one course, as a button that opens "Your belt"
export function beltButton(belt = "WHITE", topicId, { size = "sm", topicName = "" } = {}) {
  const button = el("button", {
    type: "button", className: "belt-button",
    // the name starts with the visible text, so voice control ("click White belt") works
    ariaLabel: `${beltName(belt)} belt${topicName ? ` in ${topicName}` : ""}: show your progress to the next belt`,
    ariaHasPopup: "dialog", ariaExpanded: "false",
  },
    beltBadge(belt, { size }),
    el("span", { className: "belt-more", ariaHidden: "true" }, "›"),
  );
  button.addEventListener("click", (event) => {
    event.preventDefault();          // the course card around it is a link: don't follow it
    event.stopPropagation();
    openBeltDialog(button, topicId);
  });
  return button;
}

// ---------------------------------------------------------------------------
// "Your belt" dialog
// ---------------------------------------------------------------------------
const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function meter(label, done, total) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  return el("div", { className: `belt-meter ${done >= total ? "met" : ""}` },
    el("div", { className: "belt-meter-head" },
      el("span", {}, label),
      el("strong", {}, `${done} of ${total}`, done >= total ? el("span", { className: "tick", ariaHidden: "true" }, " ✓") : "")),
    el("div", { className: "bar", role: "progressbar", ariaLabel: label, ariaValueNow: String(done), ariaValueMin: "0", ariaValueMax: String(total) },
      el("span", { style: `width: ${percent}%` })),
  );
}

function dialogBody(status) {
  const { ideas, practised, remembered } = status.requirements;
  const meaning = el("p", { className: "belt-meaning" },
    el("strong", {}, "What a belt means. "),
    "A belt shows knowledge you've kept: ideas you recalled after a time gap, then proved in a grading. "
    + "Watching lessons, finishing a course or turning up doesn't earn one on its own.");

  // Black belt: the top. No next level to show.
  if (!status.nextBelt) {
    return [
      el("div", { className: "belt-mastery" },
        beltBadge("BLACK", { size: "lg" }),
        el("p", {}, el("strong", {}, "Mastery. "), `Black belt is the highest belt in StreamDojo. You've reached it in ${status.topicName}.`)),
      meaning,
    ];
  }

  const next = beltName(status.nextBelt);
  const remaining = ideas - remembered;
  let nextStep;
  if (status.inProgress) {
    nextStep = el("a", { className: "btn", href: `#/grading/${encodeURIComponent(status.topicId)}` }, "Resume your grading");
  } else if (status.canStart) {
    nextStep = el("a", { className: "btn", href: `#/grading/${encodeURIComponent(status.topicId)}` }, `Grade for ${next} belt`);
  } else if (status.eligible && status.nextGradingAt) {
    nextStep = el("p", { className: "meta" }, `You're ready. Your next grading opens ${when(status.nextGradingAt)} (3 days after your last one).`);
  } else {
    nextStep = el("p", { className: "meta" }, practised < ideas
      ? `Next step: practise the ${ideas - practised} idea${ideas - practised === 1 ? "" : "s"} you haven't tried yet.`
      : `Next step: keep doing your reviews. ${remaining} idea${remaining === 1 ? "" : "s"} still to remember after a gap.`);
  }

  return [
    el("div", { className: "belt-next" },
      el("span", { className: "meta" }, "Next belt"), beltBadge(status.nextBelt, { size: "sm" })),
    meter("Ideas practised", practised, ideas),
    meter("Ideas remembered after a gap", remembered, ideas),
    el("div", { className: "belt-rule" },
      el("p", {}, el("strong", {}, `To earn your ${next} belt`)),
      el("ol", {},
        el("li", {}, `Practise all ${ideas} ideas in this course.`),
        el("li", {}, "Remember each one after a gap: recall it in full, without a hint, at a review a day or more later. ClipSensei then waits 3 days or more before asking again."),
        el("li", {}, `Pass the grading with ${status.passMark}% or more: ${status.questionCount} question${status.questionCount === 1 ? "" : "s"} from the whole course, 60 minutes.`)),
    ),
    nextStep,
    status.lastResult
      ? el("p", { className: "meta" }, `Last grading: ${status.lastResult.score}% for ${beltName(status.lastResult.targetBelt)} belt, ${status.lastResult.passed ? "passed" : "not passed"}.`)
      : "",
    meaning,
  ];
}

async function openBeltDialog(trigger, topicId) {
  const titleId = `belt-title-${Math.random().toString(36).slice(2, 8)}`;
  const close = el("button", { type: "button", className: "belt-close", ariaLabel: "Close" }, "×");
  const content = el("div", { className: "belt-dialog-body", ariaLive: "polite" }, el("p", { className: "meta" }, "Checking your progress..."));
  const title = el("h2", { id: titleId }, "Your belt");
  const dialog = el("dialog", { className: "belt-dialog" },
    el("div", { className: "belt-dialog-head" }, title, close),
    content);
  dialog.setAttribute("aria-labelledby", titleId);

  const finish = () => dialog.close();
  close.addEventListener("click", finish);
  dialog.addEventListener("click", (event) => { if (event.target === dialog) finish(); });   // tap outside the card
  dialog.addEventListener("close", () => {
    trigger.setAttribute("aria-expanded", "false");
    dialog.remove();
    trigger.focus();                                       // back to where the learner was
  });

  document.body.append(dialog);
  trigger.setAttribute("aria-expanded", "true");
  dialog.showModal();                                      // traps focus, Esc closes, rest of the page is inert
  close.focus();

  try {
    const status = await api(`/learn/grading/${encodeURIComponent(topicId)}`);
    title.textContent = `Your belt in ${status.topicName}`;
    content.replaceChildren(
      // at Black belt the mastery block shows the belt itself, so no separate "Current belt" row
      status.nextBelt ? el("div", { className: "belt-current" }, el("span", { className: "meta" }, "Current belt"), beltBadge(status.belt, { size: "md" })) : "",
      ...dialogBody(status));
  } catch (error) {
    content.replaceChildren(el("p", { className: "meta" }, `Couldn't load your belt progress: ${error.message}`));
  }
}
