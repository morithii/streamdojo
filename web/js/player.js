// =============================================================================
// Video playback: adaptive HLS (360p/720p/1080p chosen automatically by bandwidth).
// Chrome, Edge and Firefox need hls.js; Safari and iPhone play HLS natively.
// Only one stream plays at a time - stopPlayer() runs whenever the page changes.
// =============================================================================
import { el } from "./dom.js";

let current = null;

export function showSubtitles(video, lang) {
  for (const track of video.textTracks) {
    track.mode = track.language === lang ? "showing" : "disabled";
  }
}

// A <video> with its subtitle tracks and a language picker. Call start() once it is on the page.
export function lessonPlayer(lesson) {
  const subs = lesson.subtitles || [];
  const video = el("video", { controls: true, playsInline: true, preload: "metadata", poster: lesson.thumbnailUrl || "" });
  for (const sub of subs) {
    video.append(el("track", { kind: "subtitles", src: sub.url, srclang: sub.lang, label: sub.label, default: sub.lang === "en" }));
  }
  const picker = el("select", { className: "picker", ariaLabel: "Subtitles", onchange: (e) => showSubtitles(video, e.target.value) },
    el("option", { value: "off" }, "Subtitles off"),
    ...subs.map((sub) => el("option", { value: sub.lang, selected: sub.lang === "en" }, sub.label)),
  );
  const start = () => {
    attachStream(video, lesson.hlsUrl);
    video.textTracks.addEventListener("addtrack", () => showSubtitles(video, picker.value));
    showSubtitles(video, picker.value);
  };
  return { video, picker, start };
}

export function attachStream(video, hlsUrl, startAt = 0) {
  stopPlayer();
  if (window.Hls && Hls.isSupported()) {
    current = new Hls({ capLevelToPlayerSize: true, startPosition: startAt });  // don't fetch 1080p for a small player
    current.loadSource(hlsUrl);
    current.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = hlsUrl;
    video.addEventListener("loadedmetadata", () => { video.currentTime = startAt; }, { once: true });
  } else {
    throw new Error("This browser can't play the video stream.");
  }
}

export function stopPlayer() {
  if (current) {
    current.destroy();
    current = null;
  }
}
