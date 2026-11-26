// Welt in Regionen, Phase 1: ein Kartenpaket je Region = EINE Vektorkachel je Ausschnitt statt mehrerer Quellen.
// Inhalt jeder Kachel (Ebenen unverändert, nur hintereinander gehängt):
//   - Grundkarte (OpenMapTiles-Ebenen aus grundkarte.pmtiles)
//   - Wege-Kacheln (Ebenen w, l, g … aus wege.pmtiles, ab Zoom 10)
//   - die bisher im Gerät zusammengebauten Ebenen, jetzt fertig eingebacken – mit GENAU den Funktionen aus index.html:
//       lcf_landcover  Zoom 10–12  (Wiesen/Äcker/Wald/Fels/Sand aus den vier Kindkacheln, bisher Protokoll lcf://)
//       wcf_water      Zoom 9–10   (Wasser aus Zoom 11, bisher wcf://)
//       wnc_water_name Zoom 13–14  (Seenamen mit Seegröße gbw/gbh, bisher wnc://; ab Zoom 15 rechnet weiter das Gerät)
// Grenzkacheln (nicht ganz im eigenen Gebiet) bekommen wie bisher am Server die Ersatzkarte (OpenFreeMap) als Basis, dazu Wege und die
// eingebackenen Ebenen aus genau den Kacheln, die das Gerät bisher dafür geholt hat (eigene im Gebiet, sonst Ersatzkarte). Unter Zoom 8 keine Paketkacheln.
// Aufruf: node paket.mjs <index.html> <grundkarte.pmtiles> <wege.pmtiles|-> <ausgabe.pmtiles> [aktuell.json]
import fs from "node:fs";
import zlib from "node:zlib";
import vm from "node:vm";
import { PmLeser, PmSchreiber, idZuZxy } from "./pmtiles_node.mjs";

const [, , HTML, GRUND, WEGE, AUS, AKT] = process.argv;
if (!AUS) { console.error("Aufruf: node paket.mjs <index.html> <grundkarte.pmtiles> <wege.pmtiles|-> <ausgabe.pmtiles>"); process.exit(1); }
const html = fs.readFileSync(HTML, "utf8");

// Funktion aus index.html herauslösen (Klammern zählen, Zeichenketten und Regex überspringen) – wie in wegekarte.mjs
function grab(start) {
  const i = html.indexOf(start); if (i < 0) throw new Error("nicht gefunden: " + start);
  let j = html.indexOf("{", i), d = 0, q = null;
  for (; j < html.length; j++) {
    const c = html[j];
    if (q) { if (c === "\\") { j++; continue; } if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === "`") { q = c; continue; }
    if (c === "/" && html[j + 1] === "/") { j = html.indexOf("\n", j); continue; }
    if (c === "/" && /[(,=:[!&|?{};]\s*$/.test(html.slice(Math.max(0, j - 20), j))) {
      for (j++; j < html.length && html[j] !== "/"; j++) { if (html[j] === "\\") j++; else if (html[j] === "[") { while (html[++j] !== "]") if (html[j] === "\\") j++; } }
      continue;
    }
    if (c === "{") d++; else if (c === "}") { d--; if (d === 0) return html.slice(i, j + 1); }
  }
  throw new Error("Ende nicht gefunden: " + start);
}
const teile = ["function pbfLesen(", "function pbfVarint(", "function pbfFeld(", "function pbfPacked(", "function mvtEbene(", "const lcfWert=",
  "async function entpacken(", "async function mvtAusKindern(", "function mvtMerge(", "async function wncKachel("].map(grab);
const G = new PmLeser(GRUND), W = WEGE && WEGE !== "-" ? new PmLeser(WEGE) : null;
// Gebietsprüfung genau wie im Worker (kachelImGebiet, 5×5 Punkte)
const A = AKT ? JSON.parse(fs.readFileSync(AKT, "utf8")) : null;
const inRing = (x, y, r) => { let ins = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1]; if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) ins = !ins; } return ins; };
const inGebiet = (lon, lat) => (A.gebiet || []).some(p => inRing(lon, lat, p[0]) && !p.slice(1).some(h => inRing(lon, lat, h)));
const t2lon = (x, z) => x / 2 ** z * 360 - 180, t2lat = (y, z) => { const n = Math.PI - 2 * Math.PI * y / 2 ** z; return 180 / Math.PI * Math.atan(Math.sinh(n)); };
const innenC = new Map();
function innen(z, x, y) { if (!A) return true; const k = `${z}/${x}/${y}`; let v = innenC.get(k); if (v !== undefined) return v;
  v = z >= (A.minzoomEigen ?? 8); const w = t2lon(x, z), e = t2lon(x + 1, z), n = t2lat(y, z), s = t2lat(y + 1, z);
  for (let i = 0; i <= 4 && v; i++) for (let j = 0; j <= 4 && v; j++) if (!inGebiet(w + (e - w) * i / 4, n + (s - n) * j / 4)) v = false;
  innenC.set(k, v); return v; }
// Ersatzkarte (OpenFreeMap) für Grenzkacheln: vorab in einen Ordner laden
const OFM_DIR = AUS + ".ofm";
const ofmDatei = (z, x, y) => `${OFM_DIR}/${z}_${x}_${y}.pbf`;
function basis(z, x, y) { if (innen(z, x, y)) return G.kachel(z, x, y); const f = ofmDatei(z, x, y); return fs.existsSync(f) && fs.statSync(f).size ? fs.readFileSync(f) : null; }
const ctx = vm.createContext({ TextDecoder, TextEncoder, Response, Blob, DecompressionStream, Uint8Array, ArrayBuffer, Promise, Math, Set, RegExp, console,
  mvtWorker: () => null, // im Skript ohne Hintergrund-Thread (gleiches Ergebnis)
  lcfHole: async (z, x, y) => { const t = basis(z, x, y); return t ? t.buffer.slice(t.byteOffset, t.byteOffset + t.byteLength) : null; } });
vm.runInContext(teile.join(";\n") + ";\nthis.API={mvtAusKindern,wncKachel,mvtMerge};", ctx);
const { mvtAusKindern, wncKachel } = ctx.API;
const abort = { signal: { aborted: false } };

// Ebene umbenennen (Kachel aus mvtMerge/wncKachel hat genau eine Ebene)
function vi(a, v) { while (v >= 128) { a.push((v & 127) | 128); v = Math.floor(v / 128); } a.push(v); }
function rv(b, s) { let v = 0, m = 1, c; do { c = b[s.p++]; v += (c & 127) * m; m *= 128; } while (c >= 128); return v; }
function umbenennen(buf, neu) {
  const b = new Uint8Array(buf); if (!b.length) return Buffer.alloc(0);
  const s = { p: 0 }; const out = [];
  while (s.p < b.length) {
    const k = rv(b, s), f = k >> 3, w = k & 7;
    if (w !== 2) throw new Error("Kachelaufbau");
    const n = rv(b, s), L = b.subarray(s.p, s.p + n); s.p += n;
    if (f !== 3) continue;
    const lay = []; const t = { p: 0 };
    while (t.p < L.length) {
      const st = t.p, k2 = rv(L, t), f2 = k2 >> 3, w2 = k2 & 7;
      if (w2 === 0) rv(L, t); else if (w2 === 2) { const n2 = rv(L, t); t.p += n2; } else if (w2 === 5) t.p += 4; else if (w2 === 1) t.p += 8;
      if (f2 === 1) { const nb = Buffer.from(neu); vi(lay, (1 << 3) | 2); vi(lay, nb.length); for (const c of nb) lay.push(c); }
      else for (let i = st; i < t.p; i++) lay.push(L[i]);
    }
    vi(out, (3 << 3) | 2); vi(out, lay.length); for (const c of lay) out.push(c);
  }
  return Buffer.from(out);
}
const roh = t => t ? (t[0] === 0x1f && t[1] === 0x8b ? zlib.gunzipSync(t) : t) : Buffer.alloc(0);

// beide Kachelfolgen sind nach Nummer sortiert: zusammen durchlaufen
function* vereint(a, b) {
  let x = a.next(), y = b ? b.next() : { done: true };
  while (!x.done || !y.done) {
    if (y.done || (!x.done && x.value < y.value)) { yield x.value; x = a.next(); }
    else if (x.done || y.value < x.value) { yield y.value; y = b.next(); }
    else { yield x.value; x = a.next(); y = b.next(); }
  }
}

// Kachelliste (ab Zoom 8) und benötigte Ersatzkacheln bestimmen
const IDS = [...vereint(G.ids(), W ? W.ids() : null)].filter(id => idZuZxy(id)[0] >= 8);
if (A) {
  const brauche = new Set(), dazu = (z, x, y) => { if (!innen(z, x, y)) brauche.add(`${z}/${x}/${y}`); };
  const kinder = (z, x, y) => [[0, 0], [1, 0], [0, 1], [1, 1]].map(([a, b]) => [z + 1, 2 * x + a, 2 * y + b]);
  for (const id of IDS) { const [z, x, y] = idZuZxy(id); dazu(z, x, y);
    if (z >= 10 && z <= 13) for (const k of kinder(z, x, y)) dazu(...k);          // lcf (10–12) und wnc (13)
    if (z === 10) for (const k of kinder(z, x, y)) dazu(...k);                     // wcf 10 aus Zoom 11
    if (z === 9) for (const k of kinder(z, x, y)) for (const kk of kinder(...k)) dazu(...kk); } // wcf 9 aus Zoom 11
  fs.mkdirSync(OFM_DIR, { recursive: true });
  const tj = await (await fetch("https://tiles.openfreemap.org/planet")).json(); const T = tj.tiles[0];
  const liste = [...brauche].filter(k => !fs.existsSync(ofmDatei(...k.split("/").map(Number)))); let fertig = 0, fehler = 0;
  console.log(`Ersatzkarte: ${brauche.size} Grenzkacheln, zu laden ${liste.length} (${T})`);
  const arbeiter = async () => { for (;;) { const k = liste.pop(); if (!k) return; const [z, x, y] = k.split("/").map(Number);
    for (let v = 0; v < 5; v++) { try { const r = await fetch(T.replace("{z}", z).replace("{x}", x).replace("{y}", y));
        if (r.status === 204 || r.status === 404) { fs.writeFileSync(ofmDatei(z, x, y), ""); break; }
        if (!r.ok) throw new Error("HTTP " + r.status); fs.writeFileSync(ofmDatei(z, x, y), Buffer.from(await r.arrayBuffer())); break;
      } catch (e) { if (v === 4) { fehler++; console.log("Ersatzkachel fehlt", k, String(e)); } await new Promise(o => setTimeout(o, 2000 * (v + 1))); } }
    if (++fertig % 5000 === 0) console.log(`  ${fertig}/${brauche.size}`); } };
  await Promise.all(Array.from({ length: 24 }, arbeiter));
  if (fehler) { console.error(`${fehler} Ersatzkacheln nicht geladen – Abbruch (Paket wäre an der Grenze unvollständig)`); process.exit(2); }
}
const sch = new PmSchreiber(AUS); const zahl0 = { grenze: 0 };
const zahl = { lcf: 0, wcf: 0, wnc: 0, wege: 0 }; let n = 0; const t0 = Date.now();
for (const id of IDS) {
  const [z, x, y] = idZuZxy(id);
  if (!innen(z, x, y)) zahl0.grenze++;
  const st = [roh(basis(z, x, y))];
  if (W && z >= 10) { const w = W.kachel(z, x, y); if (w) { st.push(roh(w)); zahl.wege++; } }
  if (z >= 10 && z <= 12) { const b = umbenennen(await mvtAusKindern(z, x, y, abort, (zz, xx, yy) => ctx.lcfHole(zz, xx, yy), "landcover", /^(grass|farmland|wood|rock|sand)$/), "lcf_landcover"); if (b.length) { st.push(b); zahl.lcf++; } }
  if (z >= 9 && z <= 10) { const hole = (zz, xx, yy) => zz >= 11 ? ctx.lcfHole(zz, xx, yy) : mvtAusKindern(zz, xx, yy, abort, hole, "water", null);
    const b = umbenennen(await mvtAusKindern(z, x, y, abort, hole, "water", null), "wcf_water"); if (b.length) { st.push(b); zahl.wcf++; } }
  if (z >= 13 && z <= 14) { const b = umbenennen(await wncKachel(z, x, y, abort), "wnc_water_name"); if (b.length) { st.push(b); zahl.wnc++; } }
  const alles = Buffer.concat(st);
  if (alles.length) sch.add(id, zlib.gzipSync(alles, { level: 6 }));
  if (++n % 100000 === 0) console.log(`${n} Kacheln, ${Math.round((Date.now() - t0) / 1000)} s`, JSON.stringify(zahl));
}
const mg = G.meta(), mw = W ? W.meta() : {};
const vl = [...(mg.vector_layers || []), ...(mw.vector_layers || []),
  { id: "lcf_landcover", minzoom: 10, maxzoom: 12, fields: { class: "String", subclass: "String" } },
  { id: "wcf_water", minzoom: 9, maxzoom: 10, fields: { class: "String" } },
  { id: "wnc_water_name", minzoom: 13, maxzoom: 14, fields: { name: "String", gbw: "Number", gbh: "Number" } }];
const huelle = W ? [Math.min(G.bounds[0], W.bounds[0]), Math.min(G.bounds[1], W.bounds[1]), Math.max(G.bounds[2], W.bounds[2]), Math.max(G.bounds[3], W.bounds[3])] : G.bounds;
const erg = sch.fertig({ minz: 8, maxz: Math.max(G.maxz, W ? W.maxz : 0), bounds: huelle, center: G.center,
  meta: { ...mg, name: "qfe-active Kartenpaket", description: "Grundkarte + Wege + eingebackene Ebenen (lcf, wcf, wnc)", vector_layers: vl } });
Object.assign(zahl, zahl0);
console.log("fertig:", JSON.stringify({ ...erg, ...zahl, sekunden: Math.round((Date.now() - t0) / 1000) }));
fs.writeFileSync(AUS + ".json", JSON.stringify({ ...erg, ...zahl, huelle, vector_layers: vl }));
