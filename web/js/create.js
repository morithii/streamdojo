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

// One plain-English stage for the three status fields the pipeline writes
function stage(video) {
  if (video.processingStatus === "UPLOADING") return ["Waiting for upload", "warn"];
  if (video.processingStatus === "FAILED") return ["Processing failed", "bad"];
  if (video.processingStatus !== "READY") return ["Transcoding video…", "busy"];
  if (video.captionStatus === "PENDING") return ["Adding subtitles…", "busy"];
  if (video.captionStatus === "FAILED") return ["Subtitles failed", "bad"];        // no transcript, so no questions
  if (video.objectivesStatus === "PENDING") return ["ClipSensei is drafting questions…", "busy"];
  if (video.objectivesStatus === "FAILED") return ["Question drafting failed", "bad"];
  if (video.objectivesStatus === "DRAFT_READY") return ["Waiting for an educator to publish", "warn"];
  if (video.objectivesStatus === "PUBLISHED") return ["Live", "good"];
  return [video.objectivesStatus || "Processing…", "busy"];
}

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
  list.replaceChildren(...(videos.length ? videos.map((v) => {
    const [label, tone] = stage(v);
    return el("article", { className: "teach-item" },
      el("div", {},
        el("h3", {}, v.title),
        el("p", { className: "meta" }, `Uploaded ${new Date(v.createdAt).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`),
      ),
      el("span", { className: `tag ${tone}` }, label),
      isEducator && v.objectivesStatus === "DRAFT_READY"
        ? el("a", { className: "btn", href: `#/teach/review/${encodeURIComponent(v.videoId)}` }, "Review & publish")
        : null,
    );
  }) : [el("p", { className: "meta" }, "No uploads yet.")]));
  // Keep checking every 15 seconds while anything is still moving through the pipeline
  clearTimeout(pollTimer);
  if (videos.some((v) => stage(v)[1] === "busy") && location.hash.startsWith("#/create")) {
    pollTimer = setTimeout(() => renderUploads(list, isEducator).catch(() => {}), 15000);
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
