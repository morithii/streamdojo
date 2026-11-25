// =============================================================================
// Educator dashboard (educators group only - the API enforces this, the UI just hides the link).
//
//   #/teach                    drafts waiting for review + published lessons
//   #/teach/review/<id>        WATCH the lesson, edit ClipSensei's objectives, publish or reject
//   #/teach/insights/<id>      what's sticking, what isn't, who needs help (+ ClipSensei's note)
//
// The educator sees the answer keys here; learners never can (separate table, separate IAM).
// =============================================================================
import { el, sensei } from "./dom.js";
import { api } from "./api.js";
import { lessonPlayer } from "./player.js";
import { deleteControl } from "./remove.js";

const MAX_OBJECTIVES = 5;
const enc = encodeURIComponent;
const percent = (rate) => (rate === null || rate === undefined ? "–" : `${Math.round(rate * 100)}%`);

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
export async function renderTeach(app) {
  const [{ lessons: drafts }, { rows }] = await Promise.all([api("/lessons"), api("/catalogue")]);
  const published = rows.flatMap((row) => row.lessons.map((lesson) => ({ ...lesson, topicName: row.topicName })));

  app.replaceChildren(
    el("section", { className: "teach" },
      el("div", { className: "teach-head" },
        el("div", {},
          el("p", { className: "eyebrow" }, "Educator"),
          el("h1", {}, "Your dojo"),
        ),
        sensei(drafts.length
          ? `${drafts.length} lesson${drafts.length === 1 ? "" : "s"} waiting for your review. I drafted the questions - you have the final say.`
          : "Nothing waiting for review. Check how your published lessons are sticking."),
      ),
      el("h2", {}, "Drafts to review"),
      drafts.length
        ? el("div", { className: "teach-list" }, ...drafts.map((d) =>
            el("article", { className: "teach-item" },
              el("div", {},
                el("h3", {}, d.title || "Untitled lesson", " ", safetyTag(d.moderationStatus)),
                el("p", { className: "meta" }, `${d.objectiveCount || 0} objective(s) drafted by ClipSensei · subtitles: ${(d.captionStatus || "").toLowerCase() || "–"}`),
              ),
              el("a", { className: "btn", href: `#/teach/review/${enc(d.videoId)}` }, "Review & publish"),
            )))
        : el("p", { className: "meta" }, "No drafts right now. New uploads appear here once ClipSensei has drafted their objectives."),
      el("h2", {}, "Published lessons"),
      published.length
        ? el("div", { className: "teach-list" }, ...published.map((l) =>
            el("article", { className: "teach-item" },
              l.thumbnailUrl ? el("img", { src: l.thumbnailUrl, alt: "", className: "teach-thumb" }) : null,
              el("div", {},
                el("h3", {}, l.title),
                el("p", { className: "meta" }, l.topicName || ""),
              ),
              el("div", { className: "practice-actions" },
                el("a", { className: "btn ghost", href: `#/teach/review/${enc(l.videoId)}` }, "Edit objectives"),
                el("a", { className: "btn", href: `#/teach/insights/${enc(l.videoId)}` }, "Insights"),
              ),
            )))
        : el("p", { className: "meta" }, "Nothing published yet."),
    ),
  );
}

// ---------------------------------------------------------------------------
// Review: edit, add, remove objectives, then publish
// ---------------------------------------------------------------------------
function field(label, control, help) {
  return el("label", { className: "field" }, el("span", {}, label), control, help ? el("small", {}, help) : null);
}

function objectiveForm(lesson, objective, onDone, seek) {
  const isNew = !objective.objectiveId;
  const status = el("p", { className: "status", role: "status" });
  const inputs = {
    objective: el("input", { value: objective.objective || "", placeholder: "Explain why …" }),
    timestamp: el("input", { value: objective.timestamp || "", placeholder: "mm:ss", className: "short" }),
    modelAnswer: el("textarea", { value: objective.modelAnswer || "", rows: 3 }),
    hint: el("input", { value: objective.hint || "" }),
  };
  const pointList = el("div", { className: "stack" });
  const addPoint = (text = "") => {
    const row = el("div", { className: "row-input" },
      el("input", { value: text, placeholder: "One complete idea a good answer must contain" }),
      el("button", { type: "button", className: "btn ghost small", ariaLabel: "Remove point",
                     onclick: () => { if (pointList.children.length > 2) row.remove(); } }, "✕"),
    );
    pointList.append(row);
  };
  const points = (objective.essentialPoints || []).map((p) => (typeof p === "string" ? p : p.text));
  (points.length ? points : ["", ""]).forEach(addPoint);
  const prompts = [0, 1, 2].map((i) => el("input", { value: (objective.prompts || [])[i] || "",
                                                      placeholder: ["Why …?", "What would happen if …?", "Explain to a colleague …"][i] }));

  async function save() {
    const body = {
      objective: inputs.objective.value, timestamp: inputs.timestamp.value.trim(),
      modelAnswer: inputs.modelAnswer.value, hint: inputs.hint.value,
      essentialPoints: [...pointList.querySelectorAll("input")].map((i) => i.value).filter((v) => v.trim()),
      prompts: prompts.map((i) => i.value),
    };
    status.textContent = "Saving…";
    try {
      if (isNew) {
        await api(`/lessons/${enc(lesson.videoId)}/objectives`, { method: "POST", body });
      } else {
        await api(`/lessons/${enc(lesson.videoId)}/objectives/${enc(objective.objectiveId)}`, { method: "PUT", body });
      }
      status.textContent = "Saved.";
      if (isNew) onDone();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function remove() {
    if (!window.confirm("Remove this objective and its answer key?")) return;
    try {
      await api(`/lessons/${enc(lesson.videoId)}/objectives/${enc(objective.objectiveId)}`, { method: "DELETE" });
      onDone();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  return el("article", { className: `objective-card ${isNew ? "new" : ""}` },
    el("div", { className: "objective-head" },
      el("h3", {}, isNew ? "New objective" : `Objective ${objective.objectiveId.replace("obj", "")}`),
      isNew ? el("span", { className: "tag" }, "written by you") : el("span", { className: "tag" }, objective.status === "PUBLISHED" ? "live" : "draft"),
    ),
    field("Objective", inputs.objective, "What the learner should be able to do, starting with a verb."),
    field("Taught at",
      el("div", { className: "row-input" }, inputs.timestamp,
        seek ? el("button", { type: "button", className: "btn ghost small", onclick: () => seek(inputs.timestamp.value) }, "▶ Play from here") : null),
      "Where in the video - check it really is taught there. Learners get a \"Rewatch from\" button."),
    el("fieldset", { className: "field" },
      el("legend", {}, "Key ideas a good answer must contain (2–4, each a different idea)"),
      pointList,
      el("button", { type: "button", className: "btn ghost small",
                     onclick: () => { if (pointList.children.length < 4) addPoint(); } }, "+ Add key idea"),
    ),
    field("Model answer", inputs.modelAnswer, "Shown after marking. Should contain every key idea."),
    field("Hint", inputs.hint, "Nudges without giving the answer away."),
    el("fieldset", { className: "field" },
      el("legend", {}, "Three ways to ask it (first: practice; the others rotate in spaced reviews)"),
      ...prompts,
    ),
    el("div", { className: "practice-actions" },
      el("button", { className: "btn", onclick: save }, isNew ? "Add objective" : "Save changes"),
      !isNew && lesson.objectivesStatus === "DRAFT_READY" && lesson.objectives.length > 1
        ? el("button", { className: "btn ghost", onclick: remove }, "Remove")
        : null,
      isNew ? el("button", { className: "btn ghost", onclick: onDone }, "Cancel") : null,
    ),
    status,
  );
}

// ---------------------------------------------------------------------------
// Safety check (Amazon Rekognition): the AI flags, the educator decides
// ---------------------------------------------------------------------------
function safetyTag(status) {
  if (status === "FLAGGED") return el("span", { className: "tag bad" }, "⚠ flagged");
  if (status === "UNCHECKED") return el("span", { className: "tag warn" }, "not checked");
  if (status === "CLEAN") return el("span", { className: "tag good" }, "✓ safety check");
  return null;                                   // lessons uploaded before the safety check existed
}

function safetyPanel(lesson, seek) {
  const status = lesson.moderationStatus;
  if (!status) return null;
  if (status === "CLEAN") {
    return el("p", { className: "safety clean", role: "status" }, "✓ Safety check passed: ", lesson.moderationNote || "nothing unsafe found.");
  }
  if (status === "UNCHECKED") {
    return el("section", { className: "safety unchecked", role: "status" },
      el("h2", {}, "Safety check didn't run"),
      el("p", {}, lesson.moderationNote || "Please watch the whole video before publishing."));
  }
  const flags = lesson.moderationFlags || [];
  return el("section", { className: "safety flagged", role: "alert" },
    el("h2", {}, `⚠ Safety check flagged ${flags.length} moment${flags.length === 1 ? "" : "s"}`),
    el("p", {}, "Amazon Rekognition found content that may be unsuitable. It can be wrong, so watch each moment and decide. ",
      "You'll be asked to confirm before publishing."),
    el("ul", { className: "flags" }, ...flags.map((f) =>
      el("li", {},
        el("span", { className: "flag-label" }, f.parent ? `${f.parent} › ${f.label}` : f.label),
        el("span", { className: "meta" }, `${f.at}–${f.until} · ${f.confidence}% sure`),
        seek ? el("button", { type: "button", className: "btn ghost small", onclick: () => seek(f.at) }, "▶ Play from here") : null,
      ))),
  );
}

export async function renderReview(app, videoId) {
  const lesson = await api(`/lessons/${enc(videoId)}/objectives`);
  const reload = () => renderReview(app, videoId);

  // Watch before you approve: the same stream and subtitles the learner will get
  const player = lesson.hlsUrl ? lessonPlayer(lesson) : null;
  const seek = player ? (mmss) => {
    const match = /^(\d{1,3}):(\d{2})$/.exec((mmss || "").trim());
    if (!match) return;
    player.video.currentTime = Number(match[1]) * 60 + Number(match[2]);
    player.video.play().catch(() => {});        // browsers may block autoplay - the position is still set
    player.video.scrollIntoView({ behavior: "smooth", block: "center" });
  } : null;
  const watch = player
    ? el("section", { className: "review-watch" },
        el("div", { className: "player frame" }, player.video),
        el("div", { className: "watch-actions" }, el("label", {}, "Subtitles ", player.picker),
          el("span", { className: "meta" }, "Watch it through, then check each objective below against what is actually taught.")))
    : el("p", { className: "meta" }, "The video is still processing - refresh in a minute to watch it.");

  const list = el("div", { className: "stack" }, ...lesson.objectives.map((o) => objectiveForm(lesson, o, reload, seek)));
  const isDraft = lesson.objectivesStatus === "DRAFT_READY";

  const addButton = el("button", {
    className: "btn ghost",
    disabled: lesson.objectives.length >= MAX_OBJECTIVES,
    onclick: () => { addButton.remove(); list.append(objectiveForm(lesson, {}, reload, seek)); },
  }, lesson.objectives.length >= MAX_OBJECTIVES ? `Maximum ${MAX_OBJECTIVES} objectives` : "+ Add an objective ClipSensei missed");

  const topic = el("input", { placeholder: "Topic row on the home page, e.g. Cloud Career Skills" });
  const publishStatus = el("p", { className: "status", role: "status" });
  // Reject: the creator is told why (the reason is required) and uploads a fixed video
  const reason = el("textarea", { rows: 3, maxLength: 500, placeholder: "e.g. The audio is too quiet after 02:10 - please re-record that part." });
  const rejectStatus = el("p", { className: "status", role: "status" });
  const rejectPanel = isDraft
    ? el("section", { className: "publish reject" },
        el("h2", {}, "Or send it back"),
        el("p", { className: "meta" }, "The creator gets a notification with your reason and can upload a fixed version."),
        el("label", { className: "field" }, el("span", {}, "Reason for the creator"), reason),
        el("button", {
          className: "btn ghost danger",
          onclick: async () => {
            if (reason.value.trim().length < 5) return void (rejectStatus.textContent = "Tell the creator why (at least 5 characters).");
            rejectStatus.textContent = "Sending back…";
            try {
              await api(`/lessons/${enc(videoId)}/reject`, { method: "POST", body: { reason: reason.value } });
              location.hash = "#/teach";
            } catch (error) {
              rejectStatus.textContent = error.message;
            }
          },
        }, "Reject and notify the creator"),
        rejectStatus,
      )
    : null;

  const flagged = lesson.moderationStatus === "FLAGGED";
  const confirmSafety = el("input", { type: "checkbox", id: "confirm-safety" });
  const publishPanel = isDraft
    ? el("section", { className: "publish" },
        el("h2", {}, "Publish"),
        el("p", { className: "meta" }, "Learners see this lesson only after you publish. Save each objective first."),
        field("Topic", topic),
        flagged
          ? el("label", { className: "confirm-safety", htmlFor: "confirm-safety" }, confirmSafety,
              el("span", {}, "I've watched the flagged moments and this lesson is suitable for learners."))
          : null,
        el("button", {
          className: "btn big",
          onclick: async () => {
            if (flagged && !confirmSafety.checked) {
              publishStatus.textContent = "Tick the box to confirm you've watched the flagged moments.";
              confirmSafety.focus();
              return;
            }
            publishStatus.textContent = "Publishing…";
            try {
              await api(`/lessons/${enc(videoId)}/publish`, { method: "POST", body: { topic: topic.value, confirmSafetyReview: flagged && confirmSafety.checked } });
              location.hash = "#/teach";
            } catch (error) {
              publishStatus.textContent = error.message;
            }
          },
        }, "Publish lesson"),
        publishStatus,
      )
    : null;

  // Lesson title: the uploader's title may not match what the video teaches
  const titleInput = el("input", { value: lesson.title || "", className: "title-input", ariaLabel: "Lesson title" });
  const titleStatus = el("span", { className: "status", role: "status" });
  // Place in the course: blank = in the order lessons were published
  const positionInput = el("input", { type: "number", min: 1, max: 999, className: "short", placeholder: "auto",
                                      value: lesson.position ?? "", ariaLabel: "Lesson number in the course" });
  const saveTitle = async () => {
    titleStatus.textContent = "Saving…";
    const body = { title: titleInput.value };
    if (positionInput.value) body.position = Number(positionInput.value);
    try {
      await api(`/lessons/${enc(videoId)}`, { method: "PUT", body });
      titleStatus.textContent = "Saved.";
    } catch (error) {
      titleStatus.textContent = error.message;
    }
  };
  const suggestion = lesson.suggestedTitle && lesson.suggestedTitle !== lesson.title
    ? el("p", { className: "meta" }, "ClipSensei suggests: ", el("strong", {}, lesson.suggestedTitle), " ",
        el("button", { className: "btn ghost small", onclick: () => { titleInput.value = lesson.suggestedTitle; saveTitle(); } }, "Use this"))
    : null;

  app.replaceChildren(
    el("section", { className: "teach" },
      el("a", { className: "back", href: "#/teach" }, "← Educator dashboard"),
      el("p", { className: "eyebrow" }, isDraft ? "Review ClipSensei's draft" : "Edit live objectives"),
      el("div", { className: "row-input" }, titleInput,
        el("button", { className: "btn ghost", onclick: saveTitle }, "Save")),
      el("label", { className: "field position-field" },
        el("span", {}, `Lesson number in ${lesson.topicName ? `"${lesson.topicName}"` : "its course"}`),
        positionInput,
        el("small", {}, "Leave blank to keep the order lessons were published in. Lower numbers come first.")),
      titleStatus,
      suggestion,
      safetyPanel(lesson, seek),
      watch,
      sensei(isDraft
        ? "I drafted these from the transcript. Check each key idea is a different idea - that's what makes the marking fair."
        : "These are live. Edits apply to the next attempt; learners' schedules are kept."),
      list,
      addButton,
      el("div", { className: "decision" }, publishPanel, rejectPanel),
      el("section", { className: "danger-zone" },
        el("h2", {}, "Remove this lesson"),
        el("p", { className: "meta" }, "For duplicates, wrong uploads or unsuitable content. Learners lose access straight away."),
        deleteControl(videoId, lesson.title, { askReason: true, onDone: () => { location.hash = "#/teach"; } })),
    ),
  );
  if (player) player.start();                   // attach the stream once the <video> is on the page
}

// ---------------------------------------------------------------------------
// Insights
// ---------------------------------------------------------------------------
// ClipSensei's note comes as "- **Bold:** text" lines: render the bold safely, as text nodes
function noteLine(line) {
  const parts = line.replace(/^-\s*/, "").split("**");
  return el("li", {}, ...parts.map((part, i) => (i % 2 ? el("strong", {}, part) : part)));
}

export async function renderInsights(app, videoId) {
  const data = await api(`/insights/lessons/${enc(videoId)}`);
  const noteLines = (data.summary || "").split("\n").map((l) => l.trim()).filter(Boolean);

  app.replaceChildren(
    el("section", { className: "teach" },
      el("a", { className: "back", href: "#/teach" }, "← Educator dashboard"),
      el("p", { className: "eyebrow" }, `Insights · ${data.topicName || ""}`),
      el("h1", {}, data.title),
      el("div", { className: "note" },
        el("img", { src: "/img/clipsensei.svg", alt: "", className: "score-sensei" }),
        el("div", {},
          el("strong", {}, "ClipSensei's note for you"),
          noteLines.length ? el("ul", {}, ...noteLines.map(noteLine)) : el("p", { className: "meta" }, "No summary available right now - the numbers below are still up to date."),
        ),
      ),
      el("h2", {}, "What's sticking"),
      ...data.objectives.map((o) =>
        el("article", { className: "objective-card" },
          el("div", { className: "objective-head" },
            el("h3", {}, o.objective),
            el("span", { className: "tag" }, `taught at ${o.timestamp}`),
          ),
          el("div", { className: "stats" },
            el("div", {}, el("strong", {}, percent(o.unaidedRecallRate)), el("span", {}, "recalled unaided")),
            el("div", {}, el("strong", {}, String(o.attempts)), el("span", {}, "counted attempts")),
            el("div", {}, el("strong", {}, String(o.hintsUsed)), el("span", {}, "hints used")),
            el("div", {}, el("strong", {}, String(o.dontRemember)), el("span", {}, "\"I don't remember\"")),
          ),
          o.thinEvidence ? el("p", { className: "meta" }, "Fewer than 5 attempts so far - treat these percentages as early signals.") : null,
          el("ul", { className: "miss-list" }, ...o.points.map((p) =>
            el("li", {},
              el("div", { className: "miss-label" }, el("span", {}, p.text), el("strong", {}, `${percent(p.missRate)} missed`)),
              el("div", { className: "bar" }, el("span", { style: `width: ${Math.round((p.missRate || 0) * 100)}%` })),
            ))),
        )),
      el("h2", {}, "Who needs help"),
      data.learners.length
        ? el("table", { className: "learners" },
            el("thead", {}, el("tr", {}, ...["Learner", "Ideas practised", "Overdue", "Still practising", "Longest interval", ""].map((h) => el("th", {}, h)))),
            el("tbody", {}, ...data.learners.map((l) =>
              el("tr", {},
                el("td", {}, l.displayName),
                el("td", {}, String(l.practised)),
                el("td", {}, String(l.overdue)),
                el("td", {}, String(l.needsPractice)),
                el("td", {}, `${l.longestIntervalDays} day${l.longestIntervalDays === 1 ? "" : "s"}`),
                el("td", {}, l.needsHelp ? el("span", { className: "tag warn" }, "needs help") : el("span", { className: "tag" }, "on track")),
              ))),
          )
        : el("p", { className: "meta" }, "No learner has practised this lesson yet."),
    ),
  );
}
