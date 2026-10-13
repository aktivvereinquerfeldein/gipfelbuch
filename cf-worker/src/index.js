/* Gipfelbuch-Vermittler (Cloudflare Worker)
   /overpass  – Kartendaten (Gipfel, Hütten, Wege …): einmal beim Overpass-Server holen, 7 Tage merken,
                alle weiteren gleichen Anfragen kommen sofort aus dem Speicher. Mehrere Overpass-Server als Ersatz.
   /status    – kurze Prüfung, ob der Vermittler läuft. */
const EPS = [
  "https://overpass-api.de/api/interpreter",
  "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
  "https://overpass.private.coffee/api/interpreter",
];
const TTL = 7 * 24 * 3600; // Sekunden
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
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
  const errs = [];
  for (const ep of EPS) {
    try {
      const r = await fetch(ep, {
        method: "POST",
        body: "data=" + encodeURIComponent(q),
        headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Gipfelbuch/1.0 (https://aktivvereinquerfeldein.github.io/gipfelbuch/)" },
        signal: AbortSignal.timeout(50000),
      });
      const t = await r.text();
      if (r.ok && t.trimStart()[0] === "{" && !/"remark":\s*"runtime error/.test(t)) {
        if (env.CACHE && t.length < 20_000_000) ctx.waitUntil(env.CACHE.put(key, t, { expirationTtl: TTL }).catch(() => {}));
        return json(t, 200, { "X-Cache": "MISS", "Cache-Control": "public, max-age=3600" });
      }
      errs.push(new URL(ep).hostname + ":" + r.status);
    } catch (e) {
      errs.push(new URL(ep).hostname + ":" + (e.name || "Fehler"));
    }
  }
  return json(JSON.stringify({ error: "Kartendaten-Server ausgelastet", details: errs }), 503);
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    const p = new URL(req.url).pathname;
    if (p === "/overpass") return overpass(req, env, ctx);
    if (p === "/status") return json(JSON.stringify({ ok: true, cache: !!env.CACHE }));
    return new Response("Gipfelbuch-Vermittler läuft.", { headers: { ...CORS, "Content-Type": "text/plain; charset=utf-8" } });
  },
};
