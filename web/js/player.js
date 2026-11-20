// =============================================================================
// Video playback: adaptive HLS (360p/720p/1080p chosen automatically by bandwidth).
// Chrome, Edge and Firefox need hls.js; Safari and iPhone play HLS natively.
// Only one stream plays at a time - stopPlayer() runs whenever the page changes.
// =============================================================================
let current = null;

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
