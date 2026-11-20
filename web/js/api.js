// =============================================================================
// Calls the StreamDojo API through CloudFront at /api - the same address as the
// website, so the browser needs no CORS. Every call carries the signed-in user's ID token.
// =============================================================================
import { getIdToken, signIn } from "./auth.js";

let apiBase = "/api";

export function setApiBase(base) {
  apiBase = base;
}

export async function api(path, { method = "GET", body } = {}) {
  const token = await getIdToken();
  if (!token) {
    await signIn();                     // not signed in (or the session ended): go to sign-in
    return new Promise(() => {});       // the page is navigating away
  }
  const res = await fetch(`${apiBase}${path}`, {
    method,
    headers: { Authorization: token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || `Request failed (${res.status})`);
  return data;
}
