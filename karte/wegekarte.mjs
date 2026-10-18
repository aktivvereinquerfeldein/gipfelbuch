// Gipfelbuch-Kartenserver: Wege als Vektorkacheln vorbereiten (wie bergfex/komoot).
// Liest die Wege-Zellen (sac, hike, mtb, minor) und wandelt jedes Element mit GENAU der Funktion aus der App (elToFeat in
// index.html) in ein Kartenobjekt um. Dazu wie in der App (pushData): markierte Wanderwege (onr0) und Klettersteig-Gruppen
// mit Beschriftung (gk, vfl). Ausgabe: eine Zeile je Objekt (GeoJSON), getrennt nach Zoombereich:
//   <ausgabe>-niedrig.geojsonl (schmale Straßen, Zoom 10–12) und <ausgabe>-hoch.geojsonl (Wege, Pfade, Routen, Zoom 12–14);
// daraus macht tippecanoe zwei Kachelsätze, tile-join fügt sie zu wege.pmtiles zusammen.
//
// Aufruf: node wegekarte.mjs <index.html> <zellen-ordner> <ausgabe>
import fs from "node:fs";
import zlib from "node:zlib";
import path from "node:path";
import vm from "node:vm";

const [, , HTML, ZELLEN, AUS] = process.argv;
if (!AUS) { console.error("Aufruf: node wegekarte.mjs <index.html> <zellen-ordner> <ausgabe>"); process.exit(1); }
const html = fs.readFileSync(HTML, "utf8");

// Funktion/Konstante aus index.html herauslösen (Klammern zählen), damit App und Karte immer gleich rechnen
function grab(start) {
  const i = html.indexOf(start); if (i < 0) throw new Error("nicht gefunden: " + start);
  let j = html.indexOf("{", i), d = 0, q = null;
  for (; j < html.length; j++) {
    const c = html[j];
    if (q) { if (c === "\\") { j++; continue; } if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === "`") { q = c; continue; }
    if (c === "/" && html[j + 1] === "/") { j = html.indexOf("\n", j); continue; }
    if (c === "/" && /[(,=:[!&|?{};]\s*$/.test(html.slice(Math.max(0, j - 20), j))) { // Regex-Literal überspringen
      for (j++; j < html.length && html[j] !== "/"; j++) { if (html[j] === "\\") j++; else if (html[j] === "[") { while (html[++j] !== "]") if (html[j] === "\\") j++; } }
      continue;
    }
    if (c === "{") d++; else if (c === "}") { d--; if (d === 0) return html.slice(i, j + 1); }
  }
  throw new Error("Ende nicht gefunden: " + start);
}
const code = [grab("function hav("), grab("const SACT="), grab("function axisLine("), grab("function vfGrade("), grab("function elToFeat(")].join(";\n");
const ctx = {}; vm.createContext(ctx); vm.runInContext(code + ";\nthis.elToFeat=elToFeat;", ctx);
const elToFeat = ctx.elToFeat;

function* zellen(k) {
  const d = path.join(ZELLEN, k); if (!fs.existsSync(d)) return;
  for (const f of fs.readdirSync(d)) {
    if (!/\.json(\.gz)?$/.test(f)) continue;
    let t = fs.readFileSync(path.join(d, f)); if (f.endsWith(".gz")) t = zlib.gunzipSync(t);
    const j = JSON.parse(t.toString("utf8")); for (const el of j.elements || []) yield el;
  }
}

const outN = fs.openSync(AUS + "-niedrig.geojsonl", "w"), outH = fs.openSync(AUS + "-hoch.geojsonl", "w"); let n = 0; const zahl = {};
function schreibe(f, oid) {
  const p = f.properties;
  const o = { type: "Feature", properties: { ...p, oid }, geometry: f.geometry };
  fs.writeSync(p.k === "minor" ? outN : outH, JSON.stringify(o) + "\n"); n++; zahl[p.k] = (zahl[p.k] || 0) + 1;
}

// 1) markierte Wanderwege zuerst (ihre Weg-IDs braucht die Markierung der Pfade, onr0)
const hikeIds = new Set(), seen = new Set();
for (const k of ["hike", "mtb", "minor"]) {
  for (const el of zellen(k)) {
    const id = k + el.type + el.id; if (seen.has(id)) continue; seen.add(id);
    const r = elToFeat(k, el); if (!r) continue;
    for (const x of (Array.isArray(r) ? r : [r])) { if (k === "hike" && el.type === "way") hikeIds.add(el.id); schreibe(x.f, el.id); }
  }
}
// 2) Wege und Pfade (T1–T6, Forstwege, Klettersteige, Downhill)
const vf = [];
for (const el of zellen("sac")) {
  const id = "sac" + el.type + el.id; if (seen.has(id)) continue; seen.add(id);
  const r = elToFeat("sac", el); if (!r) continue;
  const p = r.f.properties;
  if (p.k === "sac") p.onr0 = p.pth === 1 && !p.vf && ["", "T1", "T2", "T3", "T4"].includes(p.sac) && el.type === "way" && hikeIds.has(el.id) ? 1 : 0;
  if (p.k === "sac" && p.vf && r.f.geometry.type === "LineString") { vf.push({ f: r.f, oid: el.id }); continue; }
  schreibe(r.f, el.id);
}
// 3) Klettersteige gruppieren wie pushData in der App (gleicher Schlüssel gk) und je Gruppe eine Beschriftung (vfl)
const grp = {};
for (const { f } of vf) {
  const p = f.properties, c = f.geometry.coordinates, m = c[Math.floor(c.length / 2)];
  const key = (p.name || "?") + "|" + Math.round(m[0] * 20) + "|" + Math.round(m[1] * 20);
  const g = grp[key] || (grp[key] = { name: p.name, len: 0, pt: m, gr: new Set(), key }); p.gk = key; if (p.sac) g.gr.add(p.sac); if (p.len > g.len) { g.len = p.len; g.pt = m; }
}
for (const { f, oid } of vf) schreibe(f, oid);
const order = s => "ABCDEF".indexOf(s[0]) * 2 + (s.length > 1 ? 1 : 0);
for (const g of Object.values(grp)) {
  const gr = [...g.gr].sort((a, b) => order(a) - order(b));
  const diff = gr.length ? (gr.length > 1 && gr[0] !== gr[gr.length - 1] ? gr[0].split("/")[0] + "–" + gr[gr.length - 1].split("/").pop() : gr[0]) : "";
  const nm = g.name || "Klettersteig"; const name = diff ? nm + " (" + diff + ")" : nm; if (name === "Klettersteig") continue;
  schreibe({ type: "Feature", properties: { k: "vfl", gk: g.key, name }, geometry: { type: "Point", coordinates: g.pt } }, 0);
}
fs.closeSync(outN); fs.closeSync(outH);
console.log(`Wegekarte: ${n} Objekte`, JSON.stringify(zahl));
