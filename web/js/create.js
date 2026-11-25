// =============================================================================
// Creator page (#/create) - creators group only (the API enforces this).
//
// Uploading a video takes two requests:
//   1. POST /videos            -> our API creates the lesson and returns a signed upload form
//   2. POST <S3 url> (form)    -> the browser sends the file STRAIGHT to S3 with that form
// The video never passes through Lambda or API Gateway, and S3 itself enforces the 2 GB limit
// and the file type written into the signed form. EventBridge then starts the pipeline.
// =============================================================================
import { el, sensei } from "./dom.js";
import { api } from "./api.js";

const MAX_BYTES = 2 * 1024 ** 3;
const TYPES = { "video/mp4": "MP4", "video/quicktime": "MOV" };
let pollTimer = null;

// ---------------------------------------------------------------------------
// Progress tracker: exactly where a video is in the pipeline, and for how long.
// The pipeline records `stage` plus the time each stage started; MediaConvert reports a live percentage
// every 10 seconds. Each step is "done", "active", "waiting" (on a person), "todo", "skip" or "failed".
// ---------------------------------------------------------------------------
const LANGUAGES = [["en", "EN"], ["fr", "FR"], ["es", "ES"], ["it", "IT"], ["el", "EL"]];
const STALL_MINUTES = 60;                  // nothing in the pipeline should take this long
const PHASES = { PROBING: "Reading the video", TRANSCODING: "Transcoding", UPLOADING: "Saving the files" };

// 45 -> "45s", 125 -> "2m 05s", 3720 -> "1h 02m"
export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}
const between = (from, to) => (from && to ? duration((Date.parse(to) - Date.parse(from)) / 1000) : "");
function since(from) {                     // a live "2m 05s" counter, refreshed every second below
  if (!from) return "";
  const node = el("span", { className: "since" }, duration((Date.now() - Date.parse(from)) / 1000));
  node.setAttribute("data-since", from);
  return node;
}
const took = (from, to) => (from && to ? ` · took ${between(from, to)}` : "");

function languageChips(v) {
  const have = new Set(v.subtitleLanguages || []);
  return el("span", { className: "langs" }, ...LANGUAGES.map(([code, label]) =>
    el("span", { className: `lang ${have.has(code) ? "have" : ""}`, title: have.has(code) ? `${label} ready` : `${label} in progress` },
      have.has(code) ? `${label} ✓` : label)));
}

export function steps(v) {
  const uploaded = v.processingStatus !== "UPLOADING";
  const transcoded = v.processingStatus === "READY";
  const captionsDone = ["READY", "PARTIAL"].includes(v.captionStatus);
  const questionsDone = ["DRAFT_READY", "PUBLISHED", "REJECTED"].includes(v.objectivesStatus);
  const checked = Boolean(v.moderationStatus);
  const safetyRunning = captionsDone && !checked && !questionsDone && v.objectivesStatus !== "FAILED" && v.stage === "SAFETY_CHECK";
  const translating = v.stage === "TRANSLATING" || (v.subtitleLanguages || []).length > 1;

  // 1. Upload
  const upload = { label: "Upload", state: uploaded ? "done" : "active", detail: uploaded ? "Received" : ["Waiting for the file ", since(v.createdAt)] };

  // 2. HD video (MediaConvert) - live percentage
  const percent = v.transcodePercent;
  const video = v.processingStatus === "FAILED" ? { label: "HD video", state: "failed", detail: "Transcoding failed" }
    : transcoded ? { label: "HD video", state: "done", detail: `360p · 720p · 1080p${took(v.processingStartedAt, v.processedAt)}` }
    : uploaded ? { label: "HD video", state: "active", percent,
                   detail: [percent != null ? `${PHASES[v.transcodePhase] || "Transcoding"} · ${percent}% · ` : "Starting MediaConvert · ", since(v.processingStartedAt)] }
    : { label: "HD video", state: "todo", detail: "" };

  // 3. Subtitles (Transcribe, then Translate x4 in parallel)
  const subtitles = v.captionStatus === "FAILED" ? { label: "Subtitles", state: "failed", detail: "Transcription failed" }
    : captionsDone ? { label: "Subtitles", state: "done", detail: [languageChips(v), v.safetyCheckAt ? ` took ${between(v.processedAt, v.safetyCheckAt)}` : ""] }
    : transcoded ? { label: "Subtitles", state: "active",
                     detail: translating ? [languageChips(v), " Translating · ", since(v.translatingAt || v.processedAt)]
                                         : ["Transcribing English · ", since(v.processedAt)] }
    : { label: "Subtitles", state: "todo", detail: "" };

  // 4. Safety check (Rekognition) - started at upload, collected here
  const result = { CLEAN: "Passed", FLAGGED: "Flagged - an educator will review it", UNCHECKED: "Couldn't run - an educator will watch it" };
  const safety = checked ? { label: "Safety check", state: v.moderationStatus === "CLEAN" ? "done" : "waiting",
                             detail: `${result[v.moderationStatus] || v.moderationStatus}${took(v.safetyCheckAt, v.draftingAt)}` }
    : safetyRunning ? { label: "Safety check", state: "active", detail: ["Rekognition is checking · ", since(v.safetyCheckAt)] }
    : questionsDone || v.captionStatus === "FAILED" ? { label: "Safety check", state: "skip", detail: "Not run for this upload" }
    : { label: "Safety check", state: "todo", detail: "" };

  // 5. Questions (ClipSensei on Bedrock)
  const questions = v.objectivesStatus === "FAILED" ? { label: "Questions", state: "failed", detail: "ClipSensei couldn't draft questions" }
    : questionsDone ? { label: "Questions", state: "done", detail: `${v.objectiveCount} drafted by ClipSensei${took(v.draftingAt, v.draftReadyAt)}` }
    : captionsDone && !safetyRunning ? { label: "Questions", state: "active", detail: ["ClipSensei is drafting · ", since(v.draftingAt || v.safetyCheckAt)] }
    : { label: "Questions", state: "todo", detail: "" };

  // 6. A person decides
  const decision = v.objectivesStatus === "REJECTED"
    ? { label: "Not approved", state: "failed", detail: v.rejectionReason || "Sent back by an educator" }
    : { label: "Published", state: v.objectivesStatus === "PUBLISHED" ? "done" : questionsDone ? "waiting" : "todo",
        detail: v.objectivesStatus === "PUBLISHED" ? "Live for learners" : questionsDone ? "Waiting for an educator" : "" };

  const list = [upload, video, subtitles, safety, questions, decision];
  // Anything still "active" an hour after upload has stalled - say so instead of spinning forever
  const minutes = (Date.now() - Date.parse(v.createdAt)) / 60000;
  const current = list.find((step) => step.state === "active");
  if (current && minutes > STALL_MINUTES) {
    current.state = "failed";
    current.detail = "Stalled - try uploading again";
  }
  return list;
}

const isBusy = (v) => steps(v).some((step) => step.state === "active");

// One line above the steps: what's happening right now, and the total so far (or the total it took)
function summary(v) {
  const current = steps(v).find((step) => step.state === "active");
  if (current && v.processingStartedAt) {
    return el("p", { className: "pipeline-now", role: "status" }, el("strong", {}, `Now: ${current.label}`), " · processing for ", since(v.processingStartedAt));
  }
  if (v.draftReadyAt && v.processingStartedAt) {
    return el("p", { className: "pipeline-now done" }, `Processed in ${between(v.processingStartedAt, v.draftReadyAt)}`);
  }
  return null;
}

function progressBar(percent) {
  const bar = el("span", { className: "bar" }, el("span", { style: `width:${percent}%` }));
  bar.setAttribute("role", "progressbar");
  for (const [name, value] of [["aria-valuenow", percent], ["aria-valuemin", 0], ["aria-valuemax", 100], ["aria-label", "Transcoding progress"]]) {
    bar.setAttribute(name, String(value));
  }
  return bar;
}

function tracker(v) {
  const icon = { done: "✓", failed: "!", waiting: "…", active: "", todo: "", skip: "–" };
  return el("ol", { className: "steps", ariaLabel: `Progress for ${v.title}` },
    ...steps(v).map((step, i) =>
      el("li", { className: `step ${step.state}` },
        el("span", { className: "dot", ariaHidden: "true" }, step.state === "active" ? "" : icon[step.state] || String(i + 1)),
        el("strong", {}, step.label),
        el("small", {}, ...[].concat(step.detail)),
        step.state === "active" && step.percent != null ? progressBar(step.percent) : null,
      )));
}

// Live timers: every second, refresh each "since" counter on the page (no extra API calls)
setInterval(() => {
  for (const node of document.querySelectorAll(".since[data-since]")) {
    node.textContent = duration((Date.now() - Date.parse(node.dataset.since)) / 1000);
  }
}, 1000);

// S3 upload with a progress bar. XMLHttpRequest (not fetch) because only XHR reports upload progress.
function uploadToS3(upload, file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const [name, value] of Object.entries(upload.fields)) form.append(name, value);
    form.append("file", file);                          // S3 requires the file to be the LAST field
    const xhr = new XMLHttpRequest();
    xhr.open("POST", upload.url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => (xhr.status === 204 || xhr.status === 201 ? resolve() : reject(new Error(`Upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Upload failed - check your connection and try again."));
    xhr.send(form);
  });
}

async function renderUploads(list, isEducator) {
  const { videos } = await api("/videos");
  list.replaceChildren(...(videos.length ? videos.map((v) =>
    el("article", { className: "upload-item" },
      el("div", { className: "upload-head" },
        el("div", {},
          el("h3", {}, v.title),
          el("p", { className: "meta" }, `Uploaded ${new Date(v.createdAt).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`),
        ),
        isEducator && v.objectivesStatus === "DRAFT_READY"
          ? el("a", { className: "btn", href: `#/teach/review/${encodeURIComponent(v.videoId)}` }, "Review & publish")
          : null,
      ),
      summary(v),
      tracker(v),
    )) : [el("p", { className: "meta" }, "No uploads yet.")]));
  // Keep checking every 5 seconds while anything is still moving through the pipeline
  clearTimeout(pollTimer);
  if (videos.some(isBusy) && location.hash.startsWith("#/create")) {
    pollTimer = setTimeout(() => renderUploads(list, isEducator).catch(() => {}), 5000);
  }
}

export async function renderCreate(app, user) {
  const isEducator = user.groups.includes("educators");
  const title = el("input", { placeholder: "e.g. Why a capstone project gets you hired", maxLength: 120 });
  const file = el("input", { type: "file", accept: "video/mp4,video/quicktime" });
  const bar = el("div", { className: "bar upload-bar", hidden: true }, el("span", { style: "width: 0%" }));
  const status = el("p", { className: "status", role: "status" });
  const list = el("div", { className: "teach-list" }, el("p", { className: "meta" }, "Loading your uploads…"));

  const button = el("button", {
    className: "btn big",
    onclick: async () => {
      const video = file.files[0];
      if (title.value.trim().length < 3) return void (status.textContent = "Give the lesson a title (at least 3 characters).");
      if (!video) return void (status.textContent = "Choose a video file.");
      if (!TYPES[video.type]) return void (status.textContent = "Only MP4 or MOV videos.");
      if (video.size > MAX_BYTES) return void (status.textContent = "Videos can be up to 2 GB.");
      button.disabled = true;
      try {
        status.textContent = "Preparing upload…";
        const created = await api("/videos", {
          method: "POST",
          body: { title: title.value.trim(), contentType: video.type, fileSizeBytes: video.size },
        });
        bar.hidden = false;
        await uploadToS3(created.upload, video, (fraction) => {
          bar.firstChild.style.width = `${Math.round(fraction * 100)}%`;
          status.textContent = `Uploading… ${Math.round(fraction * 100)}%`;
        });
        status.textContent = "Uploaded. Transcoding, subtitles and ClipSensei's questions take about 5 minutes - this list updates by itself.";
        title.value = "";
        file.value = "";
        await renderUploads(list, isEducator);
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = false;
        setTimeout(() => { bar.hidden = true; bar.firstChild.style.width = "0%"; }, 1500);
      }
    },
  }, "Upload lesson");

  app.replaceChildren(
    el("section", { className: "teach" },
      el("div", { className: "teach-head" },
        el("div", {},
          el("p", { className: "eyebrow" }, "Creator"),
          el("h1", {}, "Upload a lesson"),
        ),
        sensei("Upload a video. I'll stream it in HD, add subtitles in 5 languages and draft the practice questions."),
      ),
      el("section", { className: "upload" },
        el("label", { className: "field" }, el("span", {}, "Lesson title"), title),
        el("label", { className: "field" }, el("span", {}, "Video (MP4 or MOV, up to 2 GB)"), file),
        el("div", { className: "practice-actions" }, button),
        bar,
        status,
      ),
      el("h2", {}, "Your uploads"),
      list,
    ),
  );
  await renderUploads(list, isEducator);
}
