/* Gipfelbuch-Vermittler (Cloudflare Worker)
   /overpass  – Kartendaten (Gipfel, Hütten, Wege …): einmal beim Overpass-Server holen, 7 Tage merken,
                alle weiteren gleichen Anfragen kommen sofort aus dem Speicher. Mehrere Overpass-Server als Ersatz.
   /status    – kurze Prüfung, ob der Vermittler läuft.
   /karte/*, /fonts/* – eigener Kartenserver (siehe karte.js). */
import { karte, schrift } from "./karte.js";
const EPS = [
  "https://overpass-api.de/api/interpreter",
  "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
  "https://overpass.private.coffee/api/interpreter",
];
const TTL = 7 * 24 * 3600; // Sekunden
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Expose-Headers": "X-Cache",
};
async function sha(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
const json = (body, status = 200, extra = {}) =>
  new Response(body, { status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", ...extra } });

async function overpass(req, env, ctx) {
  const url = new URL(req.url);
  const q = req.method === "POST" ? new URLSearchParams(await req.text()).get("data") : url.searchParams.get("data");
  if (!q || q.length > 20000) return json('{"error":"Abfrage fehlt oder zu lang"}', 400);
  const key = "op:" + (await sha(q));
  if (env.CACHE) {
    const hit = await env.CACHE.get(key);
    if (hit) return json(hit, 200, { "X-Cache": "HIT", "Cache-Control": "public, max-age=3600" });
  }
  // alle Server gleichzeitig fragen, die erste gute Antwort gewinnt (max. 25 s)
  const errs = [];
  const ctls = EPS.map(() => new AbortController());
  const one = async (ep, i) => {
    const r = await fetch(ep, {
      method: "POST",
      body: "data=" + encodeURIComponent(q),
      headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Gipfelbuch/1.0 (https://aktivvereinquerfeldein.github.io/gipfelbuch/)" },
      signal: ctls[i].signal,
    });
    const t = await r.text();
    if (r.ok && t.trimStart()[0] === "{" && !/"remark":\s*"runtime error/.test(t)) return t;
    errs.push(new URL(ep).hostname + ":" + r.status);
    throw new Error("bad");
  };
  const timer = setTimeout(() => ctls.forEach((c) => c.abort()), 25000);
  try {
    const t = await Promise.any(EPS.map(one));
    ctls.forEach((c) => c.abort());
    if (env.CACHE && t.length < 20_000_000) ctx.waitUntil(env.CACHE.put(key, t, { expirationTtl: TTL }).catch(() => {}));
    return json(t, 200, { "X-Cache": "MISS", "Cache-Control": "public, max-age=3600" });
  } catch (e) {
    /* alle fehlgeschlagen */
  } finally {
    clearTimeout(timer);
  }
  return json(JSON.stringify({ error: "Kartendaten-Server ausgelastet", details: errs }), 503);
}

// KI-Klassen wie in karte/fotos_klassen.py (Pruefer) – muss mit dem täglichen Prüf-Lauf zusammenpassen
const KI_KRITERIEN = {
  gipfel: "the mountain peak, summit or summit cross itself",
  aussicht: "a panoramic view, landscape, valley or mountain lake seen from up high",
  huette: "an alpine hut, mountain refuge or chalet building from outside",
  weg: "a hiking trail, footpath, forest road or path",
  wegweiser: "a yellow hiking signpost with arrows next to a path",
  tier: "a close-up of an animal (cow, bird, marmot, ...)",
  ort: "a village, town street, building interior or ski lift station",
  blume: "a close-up or macro photo of a flower or blossom",
  gesicht: "a selfie or close-up portrait of a person's face",
  fuesse: "feet, shoes, hiking boots or legs seen from above",
  essen: "food, cake or a drink on a table",
  schrift: "a sign board with text, a map, document or screenshot",
};
const KI_AUSSORTIEREN = new Set(["blume", "gesicht", "fuesse", "essen", "schrift"]);

// Foto sofort prüfen (Cloudflare Workers AI, clef-flash – kostenlos bis 10 000 "Neuronen"/Tag, Antwort in <1 s).
// Ergebnis kommt in dieselbe Tabelle gb_fotos wie der tägliche Prüf-Lauf (karte/fotos_ki.py), gleiche Klassen.
async function kiPruefen(env, buf, type) {
  if (!env.AI) return null;
  try {
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    const r = await env.AI.run("@cf/cloudflare/clef-flash", {
      model: "clef-flash",
      state: "A photo uploaded to a hiking app (Gipfelbuch).",
      images: [`data:${type};base64,${b64}`],
      questions: {
        klasse: { type: "choice", instructions: "What does this photo mainly show?", criteria: KI_KRITERIEN },
      },
    });
    const a = r?.answers?.klasse;
    const klasse = a?.choice;
    if (!klasse) return null;
    return { klasse, ok: !KI_AUSSORTIEREN.has(klasse), werte: a.probabilities || null };
  } catch (e) {
    return null; // KI nicht erreichbar: Foto trotzdem speichern, Prüfung holt der nächtliche Lauf nach
  }
}

// Foto hochladen: nur für angemeldete Mitglieder (Supabase-Anmeldung wird geprüft), max. 2 MB, nur Bilder
async function foto(req, env, ctx) {
  if (!env.FOTOS) return json('{"error":"Fotospeicher fehlt"}', 503);
  const auth = req.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return json('{"error":"nicht angemeldet"}', 401);
  const u = await fetch(env.SUPABASE_URL + "/auth/v1/user", { headers: { Authorization: auth, apikey: env.SUPABASE_KEY } });
  if (!u.ok) return json('{"error":"Anmeldung ungültig"}', 401);
  const user = await u.json();
  const type = (req.headers.get("Content-Type") || "").split(";")[0];
  if (!/^image\/(jpeg|webp|png)$/.test(type)) return json('{"error":"nur Bilder erlaubt"}', 415);
  const buf = await req.arrayBuffer();
  if (buf.byteLength > 2_000_000) return json('{"error":"Bild zu groß"}', 413);
  const ext = type.split("/")[1].replace("jpeg", "jpg");
  const key = `${user.id}/${Date.now()}-${crypto.randomUUID().slice(0, 8)}.${ext}`;
  await env.FOTOS.put(key, buf, { httpMetadata: { contentType: type } });
  // KI gleich prüfen, bevor das Foto einem Gipfel/einer Hütte zugeteilt wird (clef-flash antwortet in <1 s).
  // Ergebnis nach Supabase schreiben, mit der Anmeldung des Mitglieds selbst (keine eigenen Schlüssel-Rechte nötig).
  const ki = await kiPruefen(env, buf, type);
  if (ki) {
    await fetch(env.SUPABASE_URL + "/rest/v1/gb_fotos", {
      method: "POST",
      headers: { apikey: env.SUPABASE_KEY, Authorization: auth, "Content-Type": "application/json", Prefer: "return=minimal,resolution=merge-duplicates" },
      body: JSON.stringify([{ id: "g" + key, quelle: "mitglied", klasse: ki.klasse, ok: ki.ok, werte: ki.werte, nutzer: user.id, oeffentlich: false }]),
    }).catch(() => {});
  }
  return json(JSON.stringify({ key, url: "/fotos/" + key, ki: ki ? { klasse: ki.klasse, ok: ki.ok } : null }));
}

// eigenes Foto löschen (nur Fotos im eigenen Ordner)
async function fotoLoeschen(req, env) {
  const auth = req.headers.get("Authorization") || "";
  if (!env.FOTOS || !auth.startsWith("Bearer ")) return json('{"error":"nicht angemeldet"}', 401);
  const u = await fetch(env.SUPABASE_URL + "/auth/v1/user", { headers: { Authorization: auth, apikey: env.SUPABASE_KEY } });
  if (!u.ok) return json('{"error":"Anmeldung ungültig"}', 401);
  const user = await u.json();
  const key = new URL(req.url).searchParams.get("key") || "";
  if (!key.startsWith(user.id + "/")) return json('{"error":"nicht dein Foto"}', 403);
  await env.FOTOS.delete(key);
  return json('{"ok":true}');
}


// Höhenmodell (für Relief, Höhenlinien, Hangneigung): einmal aus den USA (AWS) holen,
// dann im Cloudflare-Speicher in Europa (R2) und im Netz-Zwischenspeicher behalten → schnell für alle.
async function dem(req, env, ctx, p) {
  const m = p.match(/^\/dem\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.png$/);
  if (!m || +m[1] > 15) return new Response("ungültig", { status: 400, headers: CORS });
  const cache = caches.default;
  const ck = new Request("https://dem.gipfelbuch/" + m[1] + "/" + m[2] + "/" + m[3] + ".png");
  const hit = await cache.match(ck);
  if (hit) return hit;
  const H = { ...CORS, "Content-Type": "image/png", "Cache-Control": "public, max-age=31536000, immutable" };
  const key = `dem/${m[1]}/${m[2]}/${m[3]}.png`;
  let body = null, src = "R2";
  if (env.FOTOS) { const o = await env.FOTOS.get(key); if (o) body = await o.arrayBuffer(); }
  if (!body) {
    src = "AWS";
    // AWS hängt gelegentlich: nach 1,5 s eine zweite Anfrage starten (andere Adresse), die schnellere gewinnt
    const u1 = `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${m[1]}/${m[2]}/${m[3]}.png`;
    const u2 = `https://elevation-tiles-prod.s3.amazonaws.com/terrarium/${m[1]}/${m[2]}/${m[3]}.png`;
    const ctl = [new AbortController(), new AbortController()];
    const get = async (u, i, wait) => {
      if (wait) await new Promise((r) => setTimeout(r, wait));
      const r = await fetch(u, { signal: ctl[i].signal });
      if (!r.ok) throw new Error("status " + r.status);
      return r.arrayBuffer();
    };
    const kill = setTimeout(() => ctl.forEach((c) => c.abort()), 12000);
    try {
      body = await Promise.any([get(u1, 0, 0), get(u2, 1, 1500)]);
    } catch (e) {
      return new Response("Höhenmodell nicht verfügbar", { status: 502, headers: CORS });
    } finally {
      clearTimeout(kill);
      ctl.forEach((c) => c.abort());
    }
    // nicht mehr dauerhaft in R2 ablegen (Speicher sparen, weltweit nutzbar): der Netz-Zwischenspeicher von Cloudflare reicht
  }
  const res = new Response(body, { headers: { ...H, "X-Cache": src } });
  ctx.waitUntil(cache.put(ck, res.clone()).catch(() => {}));
  return res;
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    const p = new URL(req.url).pathname;
    if (p === "/overpass" || p === "/api/overpass") return overpass(req, env, ctx);
    if (p === "/api/foto" && req.method === "POST") return foto(req, env, ctx);
    if (p === "/api/foto" && req.method === "DELETE") return fotoLoeschen(req, env);
    if (p.startsWith("/dem/") && req.method === "GET") return dem(req, env, ctx, p);
    if (p.startsWith("/karte/") && req.method === "GET") return karte(req, env, ctx, p);
    if (p.startsWith("/fonts/") && req.method === "GET") return schrift(req, env, ctx, p);
    if (p === "/status") return json(JSON.stringify({ ok: true, cache: !!env.CACHE, fotos: !!env.FOTOS }));
    // Fotos aus dem eigenen Speicher ausliefern (Hochladen kommt mit dem Feed, nur für angemeldete Nutzer)
    if (p.startsWith("/fotos/") && env.FOTOS && req.method === "GET") {
      const key = decodeURIComponent(p.slice(7));
      const obj = await env.FOTOS.get(key);
      if (!obj) return new Response("Foto nicht gefunden", { status: 404, headers: CORS });
      const h = new Headers(CORS);
      obj.writeHttpMetadata(h);
      h.set("ETag", obj.httpEtag);
      h.set("Cache-Control", "public, max-age=31536000, immutable");
      return new Response(obj.body, { headers: h });
    }
    if (env.ASSETS) return env.ASSETS.fetch(req);
    return new Response("Gipfelbuch-Vermittler läuft.", { headers: { ...CORS, "Content-Type": "text/plain; charset=utf-8" } });
  },
};
