/* Gipfelbuch – eigener Kartenserver (Prompt 0)
   Alle Kartendaten liegen im R2-Speicher (Binding FOTOS, Ordner karte/). Fehlt etwas oder liegt ein Gebiet
   außerhalb des eigenen Bereichs (vorerst Österreich + Südtirol), gibt es den bisherigen Weg als Ersatz:
   Grundkarte von OpenFreeMap, Schriften von fonts.openmaptiles.org; bei Zellen/Hangneigung/Höhenlinien
   antwortet der Server mit 404 und die App holt die Daten wie bisher (Overpass bzw. selbst berechnet).

   Wege:
   /karte/status                      – ist der eigene Kartenserver bereit? (Version, Gebiet)
   /karte/tiles.json                  – TileJSON der Grundkarte (OpenMapTiles-Schema, wie OpenFreeMap)
   /karte/tiles/z/x/y.pbf             – Grundkarte: eigene Kachel (PMTiles in R2) oder OpenFreeMap als Ersatz
   /karte/zellen/<ebene>/<ix>_<iy>.json – Wege, Hütten, Parkplätze … je Zelle (gleiches Format wie Overpass)
   /karte/hoehenlinien/z/x/y.pbf      – vorab berechnete Höhenlinien (PMTiles)
   /karte/hang/z/x/y.png              – vorab berechnete Hangneigung
   /fonts/<schrift>/<bereich>.pbf     – Schriften (einmal geholt, dann aus R2) */

const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "X-Quelle" };
const OFM_TILEJSON = "https://tiles.openfreemap.org/planet";
const LEER_PNG = () => Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAABFUlEQVR42u3BMQEAAADCoPVP7WsIoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAeAMBPAAB2ClDBAAAAABJRU5ErkJggg=="), c => c.charCodeAt(0)); // 256×256 durchsichtig
const MAXAGE_TILE = "public, max-age=86400, stale-while-revalidate=604800";

// ---------- kleine Helfer ----------
const mem = { akt: null, aktT: 0, ofm: null, ofmT: 0, pm: new Map() };

async function aktuell(env) {
  if (mem.akt && Date.now() - mem.aktT < 300000) return mem.akt;
  let a = null;
  try { const o = await env.FOTOS.get("karte/aktuell.json"); if (o) a = await o.json(); } catch (e) {}
  mem.akt = a; mem.aktT = Date.now();
  return a;
}

function inRing(x, y, r) { let ins = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1]; if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) ins = !ins; } return ins; }
// Gebiet: Liste von Polygonen [[ring],[loch]…], bereits um ca. 1 km nach innen verkleinert
function inGebiet(a, lon, lat) { return (a.gebiet || []).some(p => inRing(lon, lat, p[0]) && !p.slice(1).some(h => inRing(lon, lat, h))); }
function tile2lon(x, z) { return x / 2 ** z * 360 - 180; }
function tile2lat(y, z) { const n = Math.PI - 2 * Math.PI * y / 2 ** z; return 180 / Math.PI * Math.atan(Math.sinh(n)); }
function kachelImGebiet(a, z, x, y) {
  if (z < (a.minzoomEigen ?? 8)) return false;
  const w = tile2lon(x, z), e = tile2lon(x + 1, z), n = tile2lat(y, z), s = tile2lat(y + 1, z);
  return inGebiet(a, w, n) && inGebiet(a, e, n) && inGebiet(a, w, s) && inGebiet(a, e, s) && inGebiet(a, (w + e) / 2, (n + s) / 2);
}
// Zellen-Abdeckung: je Zellgröße Zeilen iy -> [[ix_von, ix_bis], …] (nur Zellen ganz im Gebiet)
function zelleAbgedeckt(a, size, ix, iy) {
  const rows = a.zellen && a.zellen[String(size)]; const r = rows && rows[iy];
  return !!r && r.some(([v, b]) => ix >= v && ix <= b);
}

// Bereits gzip-komprimierte Daten (Markierung X-Gz) unverändert mit „Content-Encoding: gzip“ ausliefern.
// Im Netz-Zwischenspeicher liegen sie ohne diese Angabe, damit nichts doppelt komprimiert wird.
function gz(res) {
  if (res.headers.get("X-Gz") !== "1") return res;
  const h = new Headers(res.headers); h.delete("X-Gz"); h.set("Content-Encoding", "gzip");
  return new Response(res.body, { status: res.status, headers: h, encodeBody: "manual" });
}
async function edgeCache(req, ctx, key, make) {
  const cache = caches.default; const ck = new Request("https://karte.gipfelbuch/" + key);
  const hit = await cache.match(ck);
  if (hit) return gz(hit);
  const res = await make();
  if (res.status === 200 || res.status === 204) ctx.waitUntil(cache.put(ck, res.clone()).catch(() => {}));
  return gz(res);
}

// ---------- PMTiles v3 lesen (direkt aus R2, mit Bereichs-Abfragen) ----------
function hilbert(z, x, y) {
  let acc = 0; for (let a = 0; a < z; a++) acc += 4 ** a;
  const n = 2 ** z; let d = 0; let px = x, py = y;
  for (let s = n / 2; s >= 1; s /= 2) {
    const rx = (px & s) > 0 ? 1 : 0, ry = (py & s) > 0 ? 1 : 0;
    d += s * s * ((3 * rx) ^ ry);
    if (ry === 0) {
      if (rx === 1) { px = n - 1 - px; py = n - 1 - py; }
      const t = px; px = py; py = t;
    }
  }
  return acc + d;
}

function readVarint(buf, st) {
  let val = 0, shift = 0, b;
  do { b = buf[st.p++]; val += (b & 0x7f) * 2 ** shift; shift += 7; } while (b & 0x80);
  return val;
}
function u64(dv, o) { return dv.getUint32(o, true) + dv.getUint32(o + 4, true) * 4294967296; }

async function gunzip(u8) {
  const ds = new DecompressionStream("gzip");
  return new Uint8Array(await new Response(new Blob([u8]).stream().pipeThrough(ds)).arrayBuffer());
}
async function r2range(env, key, offset, length) {
  const o = await env.FOTOS.get(key, { range: { offset, length } });
  if (!o) throw new Error("fehlt: " + key);
  return new Uint8Array(await o.arrayBuffer());
}
function parseDir(u8) {
  const st = { p: 0 }; const n = readVarint(u8, st);
  const ids = new Array(n), run = new Array(n), len = new Array(n), off = new Array(n);
  let last = 0; for (let i = 0; i < n; i++) { last += readVarint(u8, st); ids[i] = last; }
  for (let i = 0; i < n; i++) run[i] = readVarint(u8, st);
  for (let i = 0; i < n; i++) len[i] = readVarint(u8, st);
  for (let i = 0; i < n; i++) { const v = readVarint(u8, st); off[i] = (v === 0 && i > 0) ? off[i - 1] + len[i - 1] : v - 1; }
  return { ids, run, len, off };
}
async function pmOpen(env, key) {
  let p = mem.pm.get(key);
  if (p) return p;
  p = (async () => {
    const h = await r2range(env, key, 0, 16384);
    const dv = new DataView(h.buffer, h.byteOffset, h.byteLength);
    if (String.fromCharCode(...h.slice(0, 7)) !== "PMTiles" || h[7] !== 3) throw new Error("kein PMTiles v3");
    const H = { rootOff: u64(dv, 8), rootLen: u64(dv, 16), metaOff: u64(dv, 24), metaLen: u64(dv, 32), leafOff: u64(dv, 40), leafLen: u64(dv, 48),
      dataOff: u64(dv, 56), dataLen: u64(dv, 64), internalComp: h[97], tileComp: h[98], tileType: h[99], minz: h[100], maxz: h[101] };
    let root = h.slice(H.rootOff, H.rootOff + H.rootLen);
    if (H.rootOff + H.rootLen > h.length) root = await r2range(env, key, H.rootOff, H.rootLen);
    H.root = parseDir(H.internalComp === 2 ? await gunzip(root) : root);
    H.leaves = new Map();
    return H;
  })();
  mem.pm.set(key, p);
  p.catch(() => mem.pm.delete(key));
  return p;
}
function findEntry(dir, id) {
  let lo = 0, hi = dir.ids.length - 1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (dir.ids[mid] < id) lo = mid + 1; else if (dir.ids[mid] > id) hi = mid - 1; else return mid; }
  return hi >= 0 ? hi : -1;
}
async function pmTile(env, key, z, x, y) {
  const H = await pmOpen(env, key);
  if (z < H.minz || z > H.maxz) return null;
  const id = hilbert(z, x, y);
  let dir = H.root;
  for (let depth = 0; depth < 4; depth++) {
    const i = findEntry(dir, id); if (i < 0) return null;
    if (dir.run[i] > 0) {
      if (id >= dir.ids[i] + dir.run[i]) return null;
      return { data: await r2range(env, key, H.dataOff + dir.off[i], dir.len[i]), comp: H.tileComp };
    }
    const lk = dir.off[i];
    let leaf = H.leaves.get(lk);
    if (!leaf) {
      let raw = await r2range(env, key, H.leafOff + dir.off[i], dir.len[i]);
      leaf = parseDir(H.internalComp === 2 ? await gunzip(raw) : raw);
      if (H.leaves.size > 64) H.leaves.delete(H.leaves.keys().next().value);
      H.leaves.set(lk, leaf);
    }
    dir = leaf;
  }
  return null;
}

// ---------- OpenFreeMap als Ersatz ----------
async function ofmTemplate() {
  if (mem.ofm && Date.now() - mem.ofmT < 6 * 3600e3) return mem.ofm;
  const r = await fetch(OFM_TILEJSON, { cf: { cacheTtl: 3600 } });
  const j = await r.json(); mem.ofm = j.tiles[0]; mem.ofmT = Date.now();
  return mem.ofm;
}
async function ofmTile(z, x, y) {
  let t, r;
  try { t = await ofmTemplate(); } catch (e) { return new Response("Kartenkachel nicht verfügbar", { status: 502, headers: CORS }); }
  try { r = await fetch(t.replace("{z}", z).replace("{x}", x).replace("{y}", y), { cf: { cacheTtl: 86400, cacheEverything: true } }); } catch (e) { return new Response("Kartenkachel nicht verfügbar", { status: 502, headers: CORS }); }
  const h = new Headers(CORS); h.set("Content-Type", "application/x-protobuf"); h.set("Cache-Control", MAXAGE_TILE); h.set("X-Quelle", "openfreemap");
  if (r.status === 204 || r.status === 404) return new Response(null, { status: 204, headers: h });
  if (!r.ok) return new Response("Kartenkachel nicht verfügbar", { status: 502, headers: CORS });
  return new Response(r.body, { headers: h });
}

function tileResponse(t, quelle) {
  const h = new Headers(CORS); h.set("Content-Type", "application/x-protobuf"); h.set("Cache-Control", MAXAGE_TILE); h.set("X-Quelle", quelle);
  if (t.comp === 2) h.set("X-Gz", "1");
  return new Response(t.data, { headers: h });
}

// ---------- Wege ----------
export async function karte(req, env, ctx, p) {
  const url = new URL(req.url);
  if (!env.FOTOS) return new Response("Speicher fehlt", { status: 503, headers: CORS });

  if (p === "/karte/status") {
    const a = await aktuell(env);
    return new Response(JSON.stringify(a ? { ok: true, version: a.version, erzeugt: a.erzeugt, gebiete: a.gebiete, zellenVersion: a.zellenVersion || 1, hang: !!a.hang, hoehenlinien: !!a.hoehenlinien, wege: !!a.wege } : { ok: false }),
      { headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=300" } });
  }

  if (p === "/karte/tiles.json") {
    const a = await aktuell(env);
    const base = url.origin + "/karte/tiles/{z}/{x}/{y}.pbf?v=" + encodeURIComponent(a?.version || "ofm"); // Version in der Adresse: nach jeder Aktualisierung neue Kacheln am Gerät
    return new Response(JSON.stringify({
      tilejson: "3.0.0", name: "Gipfelbuch", version: a?.version || "ofm", scheme: "xyz", tiles: [base], minzoom: 0, maxzoom: 14,
      bounds: [-180, -85.0511, 180, 85.0511], vector_layers: a?.vector_layers || [],
      attribution: '<a href="https://openmaptiles.org/" target="_blank">© OpenMapTiles</a> Daten © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    }), { headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=300" } });
  }

  let m = p.match(/^\/karte\/tiles\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.pbf$/);
  if (m) {
    const z = +m[1], x = +m[2], y = +m[3];
    if (z > 14 || x >= 2 ** z || y >= 2 ** z) return new Response("ungültig", { status: 400, headers: CORS });
    const a = await aktuell(env);
    const own = a && a.basis && kachelImGebiet(a, z, x, y);
    return edgeCache(req, ctx, `tiles/${own ? a.version : "ofm"}/${z}/${x}/${y}`, async () => {
      if (own) {
        try {
          const t = await pmTile(env, a.basis + "grundkarte.pmtiles", z, x, y);
          if (t) return tileResponse(t, "eigen");
          return new Response(null, { status: 204, headers: { ...CORS, "Cache-Control": MAXAGE_TILE, "X-Quelle": "eigen-leer" } });
        } catch (e) { /* Ersatz unten */ }
      }
      return ofmTile(z, x, y);
    });
  }

  m = p.match(/^\/karte\/zellen\/([a-z0-9]{2,8})\/(-?\d{1,5})_(-?\d{1,5})\.json$/);
  if (m) {
    const [, k, ix, iy] = m; const a = await aktuell(env);
    if (!a || !a.basis || !a.cell || !a.cell[k]) return new Response("nicht im eigenen Gebiet", { status: 404, headers: CORS });
    if (!zelleAbgedeckt(a, a.cell[k], +ix, +iy)) return new Response("nicht im eigenen Gebiet", { status: 404, headers: CORS });
    return edgeCache(req, ctx, `zellen/${a.version}/${k}/${ix}_${iy}`, async () => {
      const h = new Headers(CORS); h.set("Content-Type", "application/json; charset=utf-8"); h.set("Cache-Control", "public, max-age=86400"); h.set("X-Quelle", "eigen");
      const o = await env.FOTOS.get(`${a.basis}zellen/${k}/${ix}_${iy}.json.gz`);
      if (!o) return new Response('{"version":0.6,"elements":[]}', { headers: h }); // im Gebiet, aber leer
      h.set("X-Gz", "1");
      return new Response(o.body, { headers: h });
    });
  }

  // Abdeckung der Wege-Zellen (für die App: wo die Wege-Vektorkacheln vollständig sind)
  if (p === "/karte/abdeckung.json") {
    const a = await aktuell(env);
    return new Response(JSON.stringify(a ? { version: a.version, cell: a.cell || {}, zellen: a.zellen || {} } : {}),
      { headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=300" } });
  }

  // Wege als Vektorkacheln (wie bergfex/komoot): Wanderwege, T1–T6, MTB, schmale Straßen, Klettersteige
  m = p.match(/^\/karte\/wege\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.pbf$/);
  if (m) {
    const z = +m[1], x = +m[2], y = +m[3]; const a = await aktuell(env);
    if (!a || !a.wege) return new Response("fehlt", { status: 404, headers: CORS });
    if (z > 14 || x >= 2 ** z || y >= 2 ** z) return new Response("ungültig", { status: 400, headers: CORS });
    return edgeCache(req, ctx, `wege/${a.wege}/${z}/${x}/${y}`, async () => {
      try {
        const t = await pmTile(env, a.wege, z, x, y);
        if (t) return tileResponse(t, "eigen");
        return new Response(null, { status: 204, headers: { ...CORS, "Cache-Control": MAXAGE_TILE } });
      } catch (e) { return new Response("Fehler", { status: 503, headers: CORS }); }
    });
  }

  m = p.match(/^\/karte\/hoehenlinien\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.pbf$/);
  if (m) {
    const z = +m[1], x = +m[2], y = +m[3]; const a = await aktuell(env);
    if (!a || !a.hoehenlinien || !kachelImGebiet({ ...a, minzoomEigen: 0 }, z, x, y)) return new Response("nicht im eigenen Gebiet", { status: 404, headers: CORS });
    return edgeCache(req, ctx, `hl/${a.hoehenlinien}/${z}/${x}/${y}`, async () => {
      try {
        const t = await pmTile(env, a.hoehenlinien, z, x, y);
        if (t) return tileResponse(t, "eigen");
        return new Response(null, { status: 204, headers: { ...CORS, "Cache-Control": MAXAGE_TILE } });
      } catch (e) { return new Response("Fehler", { status: 404, headers: CORS }); }
    });
  }

  m = p.match(/^\/karte\/hang\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.png$/);
  if (m) {
    const a = await aktuell(env);
    if (!a || !a.hang) return new Response("fehlt", { status: 404, headers: CORS });
    return edgeCache(req, ctx, `hang/${a.hang}/${m[1]}/${m[2]}/${m[3]}`, async () => {
      const o = await env.FOTOS.get(`${a.hang}${m[1]}/${m[2]}/${m[3]}.png`);
      if (!o) { // nichts Steiles in dieser Kachel: im eigenen Gebiet leere Kachel, sonst rechnet die App selbst
        if (kachelImGebiet({ ...a, minzoomEigen: 0 }, +m[1], +m[2], +m[3])) return new Response(LEER_PNG(), { headers: { ...CORS, "Content-Type": "image/png", "Cache-Control": "public, max-age=2592000" } });
        return new Response("fehlt", { status: 404, headers: CORS });
      }
      return new Response(o.body, { headers: { ...CORS, "Content-Type": "image/png", "Cache-Control": "public, max-age=2592000" } });
    });
  }

  return new Response("unbekannt", { status: 404, headers: CORS });
}

// Schriften: einmal bei fonts.openmaptiles.org holen, dann aus R2 (und Netz-Zwischenspeicher)
export async function schrift(req, env, ctx, p) {
  const m = p.match(/^\/fonts\/([^/]{1,80})\/(\d{1,5}-\d{1,5})\.pbf$/);
  if (!m) return new Response("ungültig", { status: 400, headers: CORS });
  const stack = decodeURIComponent(m[1]);
  if (!/^[A-Za-z0-9 ,]+$/.test(stack)) return new Response("ungültig", { status: 400, headers: CORS });
  return edgeCache(req, ctx, `fonts/${stack}/${m[2]}`, async () => {
    const key = `fonts/${stack}/${m[2]}.pbf`;
    const H = { ...CORS, "Content-Type": "application/x-protobuf", "Cache-Control": "public, max-age=31536000, immutable" };
    if (env.FOTOS) { const o = await env.FOTOS.get(key); if (o) return new Response(o.body, { headers: { ...H, "X-Quelle": "eigen" } }); }
    let r; try { r = await fetch(`https://fonts.openmaptiles.org/${encodeURIComponent(stack)}/${m[2]}.pbf`); } catch (e) { return new Response("Schrift nicht verfügbar", { status: 502, headers: CORS }); }
    if (!r.ok) return new Response("Schrift fehlt", { status: 404, headers: CORS });
    const buf = await r.arrayBuffer();
    if (env.FOTOS) ctx.waitUntil(env.FOTOS.put(key, buf, { httpMetadata: { contentType: "application/x-protobuf" } }).catch(() => {}));
    return new Response(buf, { headers: { ...H, "X-Quelle": "openmaptiles" } });
  });
}
