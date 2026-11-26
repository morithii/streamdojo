// Belt -> picture mapping (web/js/belt-levels.js). Run: node --test tests/web/belt-levels.test.mjs
// Also run by pytest (tests/test_belt_images.py), so CI's `pytest -q` covers it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { BELTS, BELT_IMAGE, BELT_IMAGE_SIZE, beltImage, beltAlt, beltLevel, beltName } from "../../web/js/belt-levels.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

test("each belt maps to its own character picture", () => {
  const expected = {
    WHITE: "/img/belts/belt-white.png", YELLOW: "/img/belts/belt-yellow.png", ORANGE: "/img/belts/belt-orange.png",
    GREEN: "/img/belts/belt-green.png", BLUE: "/img/belts/belt-blue.png", BROWN: "/img/belts/belt-brown.png",
    BLACK: "/img/belts/belt-black.png",
  };
  for (const [belt, src] of Object.entries(expected)) assert.equal(beltImage(belt), src, belt);
  assert.equal(new Set(BELTS.map(beltImage)).size, 7, "no two belts share a picture");
  assert.deepEqual(Object.keys(BELT_IMAGE), BELTS);
});

test("unknown or missing belts fall back to White, as the badge always did", () => {
  for (const odd of [undefined, null, "", "green", "PURPLE"]) {
    assert.equal(beltLevel(odd), "WHITE");
    assert.equal(beltImage(odd), "/img/belts/belt-white.png");
  }
});

test("alt text names the belt", () => {
  assert.equal(beltAlt("YELLOW"), "Yellow belt");
  assert.equal(beltAlt("BLACK"), "Black belt");
  assert.equal(beltAlt(undefined), "White belt");
  assert.equal(beltName("BROWN"), "Brown");
});

test("belt order matches the grading logic in src/marking/grading.py", () => {
  const py = readFileSync(join(ROOT, "src", "marking", "grading.py"), "utf8");
  const line = py.match(/^BELTS\s*=\s*\[(.*)\]/m);
  assert.ok(line, "BELTS list not found in grading.py");
  assert.deepEqual(line[1].match(/"([A-Z]+)"/g).map((s) => s.slice(1, -1)), BELTS);
});

test("every mapped picture exists and has the declared size", () => {
  for (const belt of BELTS) {
    const file = join(ROOT, "web", beltImage(belt));
    assert.ok(existsSync(file), `${file} is missing`);
    const png = readFileSync(file);
    assert.equal(png.readUInt32BE(16), BELT_IMAGE_SIZE.width, `${belt} width`);   // PNG IHDR width
    assert.equal(png.readUInt32BE(20), BELT_IMAGE_SIZE.height, `${belt} height`); // PNG IHDR height
  }
});
