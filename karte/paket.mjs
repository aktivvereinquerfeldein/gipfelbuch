// Welt in Regionen, Phase 1: ein Kartenpaket je Region = EINE Vektorkachel je Ausschnitt statt mehrerer Quellen.
// Inhalt jeder Kachel (Ebenen unverändert, nur hintereinander gehängt):
//   - Grundkarte (OpenMapTiles-Ebenen aus grundkarte.pmtiles)
//   - Wege-Kacheln (Ebenen w, l, g … aus wege.pmtiles, ab Zoom 10)
//   - die bisher im Gerät zusammengebauten Ebenen, jetzt fertig eingebacken – mit GENAU den Funktionen aus index.html:
//       lcf_landcover  Zoom 10–12  (Wiesen/Äcker/Wald/Fels/Sand aus den vier Kindkacheln, bisher Protokoll lcf://)
//       wcf_water      Zoom 9–10   (Wasser aus Zoom 11, bisher wcf://)
//       wnc_water_name Zoom 13–14  (Seenamen mit Seegröße gbw/gbh, bisher wnc://; ab Zoom 15 rechnet weiter das Gerät)
// Aufruf: node paket.mjs <index.html> <grundkarte.pmtiles> <wege.pmtiles|-> <ausgabe.pmtiles>
import fs from "node:fs";
import zlib from "node:zlib";
import vm from "node:vm";
import { PmLeser, PmSchreiber, idZuZxy } from "./pmtiles_node.mjs";

const [, , HTML, GRUND, WEGE, AUS] = process.argv;
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
const ctx = vm.createContext({ TextDecoder, TextEncoder, Response, Blob, DecompressionStream, Uint8Array, ArrayBuffer, Promise, Math, Set, RegExp, console,
  mvtWorker: () => null, // im Skript ohne Hintergrund-Thread (gleiches Ergebnis)
  lcfHole: async (z, x, y) => { const t = G.kachel(z, x, y); return t ? t.buffer.slice(t.byteOffset, t.byteOffset + t.byteLength) : null; } });
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

const sch = new PmSchreiber(AUS);
const zahl = { lcf: 0, wcf: 0, wnc: 0, wege: 0 }; let n = 0; const t0 = Date.now();
for (const id of vereint(G.ids(), W ? W.ids() : null)) {
  const [z, x, y] = idZuZxy(id);
  const st = [roh(G.kachel(z, x, y))];
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
const erg = sch.fertig({ minz: Math.min(G.minz, W ? W.minz : 99), maxz: Math.max(G.maxz, W ? W.maxz : 0), bounds: G.bounds, center: G.center,
  meta: { ...mg, name: "qfe-active Kartenpaket", description: "Grundkarte + Wege + eingebackene Ebenen (lcf, wcf, wnc)", vector_layers: vl } });
console.log("fertig:", JSON.stringify({ ...erg, ...zahl, sekunden: Math.round((Date.now() - t0) / 1000) }));
fs.writeFileSync(AUS + ".json", JSON.stringify({ ...erg, ...zahl, vector_layers: vl }));
