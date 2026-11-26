// =============================================================================
// Belt levels and their pictures - plain data, no browser APIs, so tests can import it in Node.
//   BELTS                 the levels, lowest to highest (same order as BELTS in src/marking/grading.py)
//   beltLevel(belt)       anything unknown counts as WHITE (the fallback the badge has always used)
//   beltName(belt)        "GREEN" -> "Green"
//   beltImage(belt)       the character picture for a belt, e.g. "/img/belts/belt-green.png"
//   beltAlt(belt)         the picture's alt text, e.g. "Green belt"
//
// The pictures in web/img/belts/ are optimised copies of the originals in frontend/src/assets/belts/:
// all seven cropped to the same box (so they stay aligned) and scaled to 192px tall. Not redrawn or recoloured.
// =============================================================================
export const BELTS = ["WHITE", "YELLOW", "ORANGE", "GREEN", "BLUE", "BROWN", "BLACK"];

export const beltLevel = (belt) => (BELTS.includes(belt) ? belt : "WHITE");

export const beltName = (belt) => (belt ? belt.charAt(0) + belt.slice(1).toLowerCase() : "");

// One picture per belt: WHITE -> /img/belts/belt-white.png ... BLACK -> /img/belts/belt-black.png
export const BELT_IMAGE = Object.freeze(
  Object.fromEntries(BELTS.map((belt) => [belt, `/img/belts/belt-${belt.toLowerCase()}.png`])),
);

export const beltImage = (belt) => BELT_IMAGE[beltLevel(belt)];

export const beltAlt = (belt) => `${beltName(beltLevel(belt))} belt`;

// Pixel size of the optimised pictures, so the browser reserves their space before they load
export const BELT_IMAGE_SIZE = Object.freeze({ width: 113, height: 192 });
