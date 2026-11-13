// Gipfelbuch-Kartenserver: Wege als Vektorkacheln vorbereiten (wie bergfex/komoot).
// Liest die Wege-Zellen (sac, hike, mtb, minor) und wandelt jedes Element mit GENAU der Funktion aus der App (elToFeat in
// index.html) in ein Kartenobjekt um. Dazu wie in der App (pushData): markierte Wanderwege (onr0) und Klettersteig-Gruppen
// mit Beschriftung (gk, vfl). Ausgabe: eine Zeile je Objekt (GeoJSON), getrennt nach Zoombereich:
//   <ausgabe>-niedrig.geojsonl (schmale Straßen, Zoom 10–12) und <ausgabe>-hoch.geojsonl (Wege, Pfade, Routen, Zoom 12–14; seit v2000 ab Zoom 11 für das Einblenden ab 11,5);
// daraus macht tippecanoe zwei Kachelsätze, tile-join fügt sie zu wege.pmtiles zusammen.
// Ab v1940 zusätzlich: lange durchgehende Wege früher zeigen (Marcel, 30.10.2026):
//   k=lg   (niedrig, ab Zoom 11)  Güterwege/Servicestraßen, die zu einer zusammenhängenden Kette von mindestens 4 km gehören (nur die
//                                 Hauptlinie und lange Seitenäste, keine kleinen Ausläufer)
//   k=svc  (hoch, ab Zoom 12)     durchgehende Servicestraßen ab 1,5 km (z. B. Zug–Formarinsee), im Kartenserver sonst erst ab Zoom 13
//   k=lake (niedrig und hoch)     Namen großer Seen (ab ca. 5 ha) schon ab Zoom 11
//
// v2052: kt (Forststraßen) = Ketten-Stufe 1/2, sonst 1 – nur echte kurze Ableger bekommen kt 0 (siehe ABLEGER). Dafür optional
// <wegenden.txt> aus karte/wegenden.py (Anschlüsse an den Enden aller Forststraßen, auch an Landesstraßen usw.).
//
// Aufruf: node wegekarte.mjs <index.html> <zellen-ordner> <ausgabe> [wegenden.txt]
import fs from "node:fs";
import zlib from "node:zlib";
import path from "node:path";
import vm from "node:vm";

const [, , HTML, ZELLEN, AUS, WEGENDEN] = process.argv;
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

// ---- lange durchgehende Wege (Güterwege/Forststraßen und Servicestraßen) ----
// Graph aus allen Wegstücken (Track + Service), die an Kreuzungen zusammenhängen. Je Zusammenhang wird die längste Strecke
// (Hauptlinie) bestimmt, danach die längsten Seitenäste. Nur Strecken ab T_SVC Metern zählen; ab T_LG Metern schon ab Zoom 11.
const T_SVC = 1500, T_LG = 4000;
function ketten() {
  const t0 = Date.now(); const R = Math.PI / 180;
  const wid = [], wsvc = [], wst = [], wlen = []; const K = [], C = []; const seenW = new Set();
  const HB = 1 << 27, MASK = HB - 1, cnt = new Uint8Array(HB);
  const hash = key => { const lo = key % 4294967296, hi = Math.floor(key / 4294967296); let h = Math.imul(lo | 0, 0x9E3779B1) ^ Math.imul(hi | 0, 0x85EBCA6B); h ^= h >>> 15; h = Math.imul(h, 0x2C1B3C6D); h ^= h >>> 12; return (h >>> 0) & MASK; };
  for (const [kind, svc] of [["sac", false], ["svc", true]]) {
    for (const el of zellen(kind)) {
      if (el.type !== "way" || !el.geometry || el.geometry.length < 2) continue;
      const t = el.tags || {}; if (!svc && (t.highway !== "track" || t.via_ferrata_scale)) continue;
      const id = el.id; if (seenW.has(id)) continue; seenW.add(id);
      wid.push(id); wsvc.push(svc); wst.push(K.length); let cum = 0, pl = null, pn = null;
      for (const g of el.geometry) {
        if (pl !== null) { const dx = (g.lon - pn) * 111320 * Math.cos(g.lat * R), dy = (g.lat - pl) * 110540; cum += Math.hypot(dx, dy); }
        pl = g.lat; pn = g.lon;
        const key = (Math.round(g.lon * 1e5) + 3e6) * 1e7 + Math.round(g.lat * 1e5);
        K.push(key); C.push(cum); const h = hash(key); if (cnt[h] < 2) cnt[h]++;
      }
      wlen.push(cum);
    }
  }
  wst.push(K.length); const NW = wid.length;
  console.log(`Ketten: ${NW} Wegstücke, ${K.length} Punkte (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  // Knoten = Wegenden und Punkte, an denen sich Wege treffen; Kanten = Stücke dazwischen
  const nodeMap = new Map(); const nodeOf = key => { let v = nodeMap.get(key); if (v === undefined) { v = nodeMap.size; nodeMap.set(key, v); } return v; };
  const eu = [], ev = [], elen = [], ew = [];
  for (let w = 0; w < NW; w++) {
    const a = wst[w], b = wst[w + 1]; let last = -1, lastC = 0;
    for (let j = a; j < b; j++) {
      if (!(j === a || j === b - 1 || cnt[hash(K[j])] >= 2)) continue;
      const nd = nodeOf(K[j]);
      if (last >= 0 && nd !== last) { eu.push(last); ev.push(nd); elen.push(C[j] - lastC); ew.push(w); }
      last = nd; lastC = C[j];
    }
  }
  const N = nodeMap.size, E = eu.length; nodeMap.clear(); K.length = 0; C.length = 0;
  const off = new Int32Array(N + 1); for (let e = 0; e < E; e++) { off[eu[e] + 1]++; off[ev[e] + 1]++; } for (let i = 0; i < N; i++) off[i + 1] += off[i];
  const adj = new Int32Array(2 * E), fill = off.slice(0, N); for (let e = 0; e < E; e++) { adj[fill[eu[e]]++] = e; adj[fill[ev[e]]++] = e; }
  console.log(`Ketten: ${N} Knoten, ${E} Kanten (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  // Zusammenhänge
  const par = new Int32Array(N); for (let i = 0; i < N; i++) par[i] = i;
  const find = x => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  for (let e = 0; e < E; e++) { const a = find(eu[e]), b = find(ev[e]); if (a !== b) par[a] = b; }
  const comp = new Map(); for (let i = 0; i < N; i++) { const r = find(i); let c = comp.get(r); if (!c) comp.set(r, c = { nodes: [], len: 0 }); c.nodes.push(i); }
  for (let e = 0; e < E; e++) comp.get(find(eu[e])).len += elen[e];
  const dist = new Float64Array(N), pe = new Int32Array(N), echain = new Float32Array(E);
  // Heap (Entfernung, Knoten)
  let hd = [], hn = [];
  const push = (d, n) => { let i = hd.length; hd.push(d); hn.push(n); while (i > 0) { const p = (i - 1) >> 1; if (hd[p] <= d) break; hd[i] = hd[p]; hn[i] = hn[p]; i = p; } hd[i] = d; hn[i] = n; };
  const pop = () => { const d0 = hd[0], n0 = hn[0], d = hd.pop(), n = hn.pop(); const L = hd.length; if (L) { let i = 0; for (;;) { let c = 2 * i + 1; if (c >= L) break; if (c + 1 < L && hd[c + 1] < hd[c]) c++; if (hd[c] >= d) break; hd[i] = hd[c]; hn[i] = hn[c]; i = c; } hd[i] = d; hn[i] = n; } return [d0, n0]; };
  function dijkstra(srcs, nodes) {
    for (const n of nodes) { dist[n] = Infinity; pe[n] = -1; } hd = []; hn = [];
    for (const s of srcs) { dist[s] = 0; push(0, s); }
    let far = srcs[0];
    while (hd.length) { const [d, n] = pop(); if (d > dist[n]) continue; if (d > dist[far] || dist[far] === Infinity) far = n;
      for (let k = off[n]; k < off[n + 1]; k++) { const e = adj[k], v = eu[e] === n ? ev[e] : eu[e], nd = d + elen[e]; if (nd < dist[v]) { dist[v] = nd; pe[v] = e; push(nd, v); } } }
    return far; }
  const markPath = (from, len, srcSet, srcs) => { let n = from; while (pe[n] >= 0) { const e = pe[n]; if (echain[e] < len) echain[e] = len; const o = eu[e] === n ? ev[e] : eu[e]; if (!srcSet.has(n)) { srcSet.add(n); srcs.push(n); } n = o; } if (!srcSet.has(n)) { srcSet.add(n); srcs.push(n); } };
  let nk = 0, tmax = Date.now() + 25 * 60 * 1000;
  for (const c of comp.values()) {
    if (c.len < T_SVC) continue; nk++;
    const nodes = c.nodes; let A = dijkstra([nodes[0]], nodes); const B = dijkstra([A], nodes); const L = dist[B]; if (L < T_SVC) continue;
    const srcSet = new Set(), srcs = []; markPath(B, L, srcSet, srcs);
    for (let it = 0; it < 150 && Date.now() < tmax; it++) {
      const F = dijkstra(srcs, nodes); const d = dist[F]; if (!(d >= T_SVC)) break; markPath(F, d, srcSet, srcs); }
  }
  // je Wegstück: Anteil auf Ketten ≥ T_SVC, Stufe 2 ab T_LG
  // v2040: Stufe 2 nur, wenn mindestens die Hälfte des Wegstücks selbst auf einer Kette ≥ T_LG liegt (vorher reichte ein kleines Stück –
  // kurze Seitenäste, die die lange Kette nur berühren, kamen dadurch schon ab Zoom 11)
  const flag = new Float64Array(NW), flag2 = new Float64Array(NW);
  for (let e = 0; e < E; e++) { if (echain[e] >= T_SVC) flag[ew[e]] += elen[e]; if (echain[e] >= T_LG) flag2[ew[e]] += elen[e]; }
  const out = new Map(); let n1 = 0, n2 = 0;
  for (let w = 0; w < NW; w++) if (wlen[w] > 0 && flag[w] / wlen[w] >= 0.5) { const l = flag2[w] / wlen[w] >= 0.5 ? 2 : 1; out.set(wid[w], l); if (l === 2) n2++; else n1++; }
  console.log(`Ketten: ${nk} Zusammenhänge ab ${T_SVC} m, Wege mit Kette ≥ ${T_SVC} m: ${n1 + n2}, davon ≥ ${T_LG} m: ${n2} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  return out;
}
const KETTE = ketten();
// v2040: Zoom-11-Satz (lange Ketten, Stufe 2) ausdünnen. Marcel (Eckwald Schlins): kurze Stücke dürfen nicht schon mit den langen
// Forststraßen kommen. Weg fallen (kommen dann mit den normalen Forstwegen ab 11,5): a) zusammenhängende Gruppen von Stufe-2-Wegen,
// die gezeichnet kürzer als LG_GRUPPE sind (die Kette läuft über andere Wegarten weiter, übrig bleiben kurze Inseln), und
// b) kurze Stummel (< LG_STUMMEL), die an einem Ende an keinen anderen Stufe-2-Weg anschließen (wiederholt, Stummelketten).
const LG_GRUPPE = 2000, LG_STUMMEL = 400;
const KEY = g => (Math.round(g.lon * 1e5) + 3e6) * 1e7 + Math.round(g.lat * 1e5);
const LAENGE = geo => { let len = 0; for (let i = 1; i < geo.length; i++) { const a = geo[i - 1], c = geo[i]; len += Math.hypot((c.lon - a.lon) * 111320 * Math.cos(c.lat * Math.PI / 180), (c.lat - a.lat) * 110540); } return len; };
// Alle Forststraßen (Track) und Servicestraßen mit Enden und Länge
const ALLW = new Map();
for (const kind of ["sac", "svc"]) for (const el of zellen(kind)) {
  if (el.type !== "way" || !el.geometry || el.geometry.length < 2 || ALLW.has(el.id)) continue;
  if (kind === "sac" && ((el.tags || {}).highway !== "track" || (el.tags || {}).via_ferrata_scale)) continue;
  ALLW.set(el.id, { a: KEY(el.geometry[0]), b: KEY(el.geometry[el.geometry.length - 1]), len: LAENGE(el.geometry), tr: kind === "sac" });
}
// Anschlüsse an den Enden der Forststraßen (aus dem ganzen Straßennetz, karte/wegenden.py)
const ENDEN = new Map();
if (WEGENDEN && fs.existsSync(WEGENDEN)) {
  for (const z of fs.readFileSync(WEGENDEN, "utf8").split("\n")) { if (!z) continue; const [x, y, d, ids] = z.split(" ");
    ENDEN.set((+x + 3e6) * 1e7 + +y, { d: d === "1", ids: ids ? ids.split(",").map(Number) : [] }); }
  console.log(`Wegenden: ${ENDEN.size} Enden von Forststraßen`);
} else console.log("Wegenden: keine Datei – keine Ableger");
// v2052: Ableger (schmal, kt 0) – Marcel: „ableger sind auch nicht mit einem weg verbunden, wenn sie an zwei enden mit einem weg oder
// straße verbunden sind dann nicht als ableger nehmen“; normale Forstwege (z. B. Rundwege) bleiben normal breit.
// Ableger = Forststraße mit freiem Ende (dort schließt nichts an), vom freien Ende bis zur ersten Abzweigung kürzer als L_ABL und
// nicht Teil einer Kette ab 1,5 km. Mehrere aneinandergehängte Stücke (nur zwei Wege am Verbindungspunkt) zählen zusammen.
const L_ABL = 500;
const ABLEGER = new Set();
{ const frei = (id, k) => { const e = ENDEN.get(k); return !!e && !e.d && e.ids.length === 1 && e.ids[0] === id; };
  for (const [id, w] of ALLW) { if (!w.tr || KETTE.get(id) || ABLEGER.has(id) || w.a === w.b) continue;
    for (const s0 of [w.a, w.b]) { if (!frei(id, s0)) continue;
      let cur = id, from = s0, sum = 0; const weg = []; let ok = false;
      for (let n = 0; n < 20; n++) { const c = ALLW.get(cur); sum += c.len; weg.push(cur); if (sum >= L_ABL) break;
        const o = from === c.a ? c.b : c.a; if (o === from) break;
        const e = ENDEN.get(o); if (!e) break;                                   // unbekannt (Randgebiet): lieber normal breit
        if (frei(cur, o)) { ok = true; break; }                                  // ganz allein liegendes kurzes Stück
        if (!e.d && e.ids.length === 2) { const nx = e.ids.find(x => x !== cur), nw = ALLW.get(nx);
          if (nw && nw.tr && !KETTE.get(nx) && !weg.includes(nx)) { cur = nx; from = o; continue; } }
        ok = true; break; }                                                       // Abzweigung erreicht
      if (ok && sum < L_ABL) weg.forEach(x => ABLEGER.add(x)); break; } }
  console.log(`Ableger: ${ABLEGER.size} kurze Forststraßen-Stichwege (unter ${L_ABL} m, ein Ende frei)`); }
// v2040: Zoom-11-Satz (lange Ketten, Stufe 2) ausdünnen. Marcel (Eckwald Schlins): kurze Stücke dürfen nicht schon mit den langen
// Forststraßen kommen. Weg fallen (kommen dann mit den normalen Forstwegen ab 11,5): a) zusammenhängende Gruppen von Stufe-2-Wegen,
// die gezeichnet kürzer als LG_GRUPPE sind (die Kette läuft über andere Wegarten weiter, übrig bleiben kurze Inseln), und
// b) kurze Stummel (< LG_STUMMEL), die an einem Ende an keinen anderen Stufe-2-Weg anschließen (wiederholt, Stummelketten).
// v2052: Inseln, die an eine kleine Straße (schon ab Zoom 10,4 sichtbar) anschließen, bleiben (Teil der durchgehenden Straße,
// Gamperdonatal). Lücken bis 1,5 km zwischen einer langen Forststraße und einer kleinen Straße oder einer zweiten langen
// Forststraße werden mit eingeblendet (Gamp nach dem Wanderparkplatz).
const LG_WEG = (() => {
  const info = new Map(), key = KEY;
  for (const kind of ["sac", "svc"]) for (const el of zellen(kind)) {
    if (el.type !== "way" || !el.geometry || el.geometry.length < 2 || info.has(el.id) || KETTE.get(el.id) !== 2) continue;
    if (kind === "sac" && ((el.tags || {}).highway !== "track" || (el.tags || {}).via_ferrata_scale)) continue;
    info.set(el.id, { len: LAENGE(el.geometry), keys: el.geometry.map(key), a: key(el.geometry[0]), b: key(el.geometry[el.geometry.length - 1]) });
  }
  // Punkte kleiner Straßen, an denen Forst-/Servicestraßen enden oder die Straße selbst endet
  const endK = new Set(); for (const w of ALLW.values()) { endK.add(w.a); endK.add(w.b); }
  for (const w of info.values()) for (const k of w.keys) endK.add(k);
  const MINOR = new Set();
  for (const el of zellen("minor")) { if (el.type !== "way" || !el.geometry) continue; const g = el.geometry;
    for (let i = 0; i < g.length; i++) { const k = key(g[i]); if (i === 0 || i === g.length - 1 || endK.has(k)) MINOR.add(k); } }
  const anKey = new Map(); for (const [id, w] of info) for (const k of new Set(w.keys)) { let l = anKey.get(k); if (!l) anKey.set(k, l = []); l.push(id); }
  const raus = new Set();
  // b) Stummel – nur kurze Abzweiger: ein Ende frei, am anderen Ende treffen sich mindestens zwei andere lange Wege (die Straße
  // läuft dort durch). Enden langer Straßen bleiben (OSM teilt Wege oft in kurze Stücke, die durften nicht abgeknabbert werden).
  let nStummel = 0;
  { const andere = (id, k) => (anKey.get(k) || []).filter(o => o !== id);
    // durchgehend: zwei oder mehr andere lange Wege treffen sich dort, oder ein anderer langer Weg läuft durch (Punkt mitten im Weg)
    const durch = (id, k) => { const o = andere(id, k); return o.length >= 2 || o.some(x => info.get(x).a !== k && info.get(x).b !== k); };
    for (const [id, w] of info) { if (w.len >= LG_STUMMEL) continue;
      const fa = andere(id, w.a).length === 0 && !MINOR.has(w.a), fb = andere(id, w.b).length === 0 && !MINOR.has(w.b);
      if ((fa && durch(id, w.b)) || (fb && durch(id, w.a))) { raus.add(id); nStummel++; } } }
  // a) kurze Inseln (nicht, wenn sie an eine kleine Straße anschließen)
  const gesehen = new Set(); let nInsel = 0;
  for (const id of info.keys()) { if (raus.has(id) || gesehen.has(id)) continue;
    const gruppe = [], st = [id]; gesehen.add(id); let summe = 0, anStrasse = false;
    while (st.length) { const w = st.pop(); gruppe.push(w); const iw = info.get(w); summe += iw.len; if (MINOR.has(iw.a) || MINOR.has(iw.b)) anStrasse = true;
      for (const k of iw.keys) for (const o of anKey.get(k) || []) if (!gesehen.has(o) && !raus.has(o)) { gesehen.add(o); st.push(o); } }
    if (summe < LG_GRUPPE && !anStrasse) { gruppe.forEach(w => raus.add(w)); nInsel += gruppe.length; } }
  // c) Lücken füllen: Forst-/Servicestraßen (auch mehrere Stücke hintereinander, zusammen unter 1,5 km), die an beiden Enden an
  // eine lange Forststraße oder kleine Straße anschließen – mindestens ein Ende an einer langen Forststraße.
  const LGK = new Set(); for (const [id, w] of info) if (!raus.has(id)) for (const k of w.keys) LGK.add(k);
  const frueh = k => LGK.has(k) || MINOR.has(k);
  const cEnd = new Map(); for (const [id, w] of ALLW) { if (info.has(id) && !raus.has(id)) continue;
    for (const k of [w.a, w.b]) { let l = cEnd.get(k); if (!l) cEnd.set(k, l = []); l.push(id); } }
  const extra = new Set();
  for (const [id, w] of ALLW) { if ((info.has(id) && !raus.has(id)) || extra.has(id)) continue;
    for (const s0 of [w.a, w.b]) { if (!frueh(s0)) continue;
      let cur = id, from = s0, sum = 0; const weg = [];
      for (let n = 0; n < 8; n++) { const c = ALLW.get(cur); sum += c.len; weg.push(cur); if (sum > 1500) break;
        const o = from === c.a ? c.b : c.a; if (o === from) break;
        if (frueh(o)) { if (LGK.has(s0) || LGK.has(o)) weg.forEach(x => extra.add(x)); break; }
        const nx = (cEnd.get(o) || []).filter(x => x !== cur), e = ENDEN.get(o);
        if (nx.length === 1 && (!e || (!e.d && e.ids.length <= 2)) && !weg.includes(nx[0])) { cur = nx[0]; from = o; continue; }
        break; }
      if (extra.has(id)) break; } }
  console.log(`Zoom-11-Satz: ${info.size} lange Wegstücke, davon ${raus.size} weggelassen (${nStummel} Abzweiger unter ${LG_STUMMEL} m, ${nInsel} in kurzen Inseln), ${extra.size} Lückenstücke dazu`);
  return id => (KETTE.get(id) === 2 && !raus.has(id)) || extra.has(id);
})();

const outN = fs.openSync(AUS + "-niedrig.geojsonl", "w"), outH = fs.openSync(AUS + "-hoch.geojsonl", "w"); let n = 0; const zahl = {};
function schreibeIn(fd, f, oid, abZoom) { const o = { type: "Feature", properties: { ...f.properties, oid }, geometry: f.geometry }; if (abZoom) o.tippecanoe = { minzoom: abZoom }; fs.writeSync(fd, JSON.stringify(o) + "\n"); n++; const k = f.properties.k; zahl[k] = (zahl[k] || 0) + 1; }
const linie = el => ({ type: "LineString", coordinates: el.geometry.map(g => [+g.lon.toFixed(5), +g.lat.toFixed(5)]) });
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
  // v2049/v2052: kt = Ketten-Stufe der Forststraße (1 = ab 1,5 km, 2 = ab 4 km, sonst 1); 0 nur für kurze Ableger (ABLEGER).
  // Die App zeichnet kt 0 schmaler (wie bergfex kurze Stichwege).
  if (p.k === "sac" && p.hw === "track" && el.type === "way") p.kt = KETTE.get(el.id) || (ABLEGER.has(el.id) ? 0 : 1);
  if (p.k === "sac" && p.vf && r.f.geometry.type === "LineString") { vf.push({ f: r.f, oid: el.id }); continue; }
  schreibe(r.f, el.id);
  // lange Güterwege schon ab Zoom 11 (Kopie im niedrigen Satz)
  if (p.k === "sac" && p.hw === "track" && el.type === "way" && LG_WEG(el.id))
    schreibeIn(outN, { properties: { k: "lg", name: p.name, hw: "track", len: p.len }, geometry: r.f.geometry }, el.id);
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
// 4) durchgehende Servicestraßen (ab 1,5 km Kette, ab Zoom 12) und lange Ketten (ab 4 km, ab Zoom 11)
{ const sv = new Set(); for (const el of zellen("svc")) {
    if (el.type !== "way" || !el.geometry || el.geometry.length < 2 || sv.has(el.id)) continue; sv.add(el.id);
    const l = KETTE.get(el.id), lg = LG_WEG(el.id); if (!l && !lg) continue; const t = el.tags || {}; const geom = linie(el); let len = 0;
    for (let i = 1; i < el.geometry.length; i++) { const a = el.geometry[i - 1], b = el.geometry[i]; len += Math.hypot((b.lon - a.lon) * 111320 * Math.cos(b.lat * Math.PI / 180), (b.lat - a.lat) * 110540); }
    const name = t["name:de"] || t.name || "";
    if (l) schreibeIn(outH, { properties: { k: "svc", name, len: Math.round(len) }, geometry: geom }, el.id);
    if (lg) schreibeIn(outN, { properties: { k: "lg", name, hw: "service", len: Math.round(len) }, geometry: geom }, el.id); } }
// 5) Namen großer Seen (beide Sätze, damit sie ab Zoom 11 bis in die hohen Zoomstufen da sind)
{ const sv = new Set(); for (const el of zellen("lake")) {
    const id = el.type + el.id; if (sv.has(id)) continue; sv.add(id); const t = el.tags || {}; const c = el.center; if (!c) continue;
    const name = t["name:de"] || t.name || ""; if (!name) continue;
    const f = { properties: { k: "lake", name, ha: +t.gb_ha || 0 }, geometry: { type: "Point", coordinates: [+c.lon.toFixed(5), +c.lat.toFixed(5)] } };
    schreibeIn(outN, f, el.id); schreibeIn(outH, f, el.id, 12); } }   // v2000: hoher Satz beginnt bei Zoom 11, Seenamen dort nur einmal
fs.closeSync(outN); fs.closeSync(outH);
console.log(`Wegekarte: ${n} Objekte`, JSON.stringify(zahl));
