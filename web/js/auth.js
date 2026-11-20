// =============================================================================
// Sign-in with Cognito's hosted login page, using the OAuth "authorization code + PKCE" flow.
//
// Why PKCE: a website can't keep a client secret (anyone can read its JavaScript).
// Instead, each sign-in invents a one-time random "verifier", sends only its SHA-256
// hash ("challenge") to Cognito, and later proves it started the sign-in by revealing
// the verifier. A stolen ?code= is useless without it.
//
// Tokens live in sessionStorage: they disappear when the tab closes.
// The API never trusts this file - API Gateway verifies every token's signature itself.
// =============================================================================
let config;
const TOKENS = "streamdojo.tokens";
const PENDING = "streamdojo.pkce";

const redirectUri = () => `${location.origin}/`;

// Random URL-safe string, e.g. for the verifier and the anti-CSRF "state"
function randomString(bytes = 32) {
  const data = crypto.getRandomValues(new Uint8Array(bytes));
  return base64Url(data);
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256(text) {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
}

function saveTokens(json, previousRefresh) {
  const tokens = {
    idToken: json.id_token,
    refreshToken: json.refresh_token || previousRefresh,   // a refresh response has no new refresh token
    expiresAt: Date.now() + (json.expires_in - 60) * 1000, // renew a minute early
  };
  sessionStorage.setItem(TOKENS, JSON.stringify(tokens));
  return tokens;
}

async function tokenRequest(params) {
  const res = await fetch(`https://${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: config.clientId, ...params }),
  });
  if (!res.ok) throw new Error(`Sign-in failed (${res.status})`);
  return res.json();
}

// Call once on page load. If we've just come back from Cognito with ?code=..., swap it for tokens.
export async function initAuth(appConfig) {
  config = appConfig;
  const params = new URLSearchParams(location.search);
  if (!params.has("code")) return;
  const pending = JSON.parse(sessionStorage.getItem(PENDING) || "{}");
  sessionStorage.removeItem(PENDING);
  history.replaceState(null, "", location.pathname + location.hash);   // remove ?code= from the address bar
  if (!pending.verifier || params.get("state") !== pending.state) {
    throw new Error("Sign-in could not be verified. Please try again.");  // possible CSRF or an old link
  }
  saveTokens(await tokenRequest({
    grant_type: "authorization_code",
    code: params.get("code"),
    redirect_uri: redirectUri(),
    code_verifier: pending.verifier,
  }));
}

// Send the browser to Cognito's sign-in page
export async function signIn() {
  const verifier = randomString(48);
  const state = randomString(16);
  sessionStorage.setItem(PENDING, JSON.stringify({ verifier, state }));
  const url = new URL(`https://${config.cognitoDomain}/oauth2/authorize`);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri(),
    scope: "openid email profile",
    code_challenge_method: "S256",
    code_challenge: base64Url(await sha256(verifier)),
    state,
  });
  location.assign(url);
}

export function signOut() {
  sessionStorage.removeItem(TOKENS);
  const url = new URL(`https://${config.cognitoDomain}/logout`);
  url.search = new URLSearchParams({ client_id: config.clientId, logout_uri: redirectUri() });
  location.assign(url);
}

// A valid ID token, renewing it with the refresh token when it has expired; null if signed out
export async function getIdToken() {
  const tokens = JSON.parse(sessionStorage.getItem(TOKENS) || "null");
  if (!tokens) return null;
  if (Date.now() < tokens.expiresAt) return tokens.idToken;
  if (!tokens.refreshToken) return null;
  try {
    const json = await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refreshToken });
    return saveTokens(json, tokens.refreshToken).idToken;
  } catch {
    sessionStorage.removeItem(TOKENS);
    return null;
  }
}

// Name and groups for the header. Display only: decoding is not verifying.
export function currentUser() {
  const tokens = JSON.parse(sessionStorage.getItem(TOKENS) || "null");
  if (!tokens) return null;
  const payload = tokens.idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(decodeURIComponent(escape(atob(payload))));   // handles accents in names
  return { name: claims.name || claims.email, groups: claims["cognito:groups"] || [] };
}
