// Welt in Regionen: die Stücke aus paket.mjs (PAKET_VON/PAKET_BIS, je Stück: Nummer 8 Byte, Länge 4 Byte, Kachel) in Reihenfolge
// zu EINER PMTiles-Datei zusammenfügen. Für hunderte Millionen Kacheln: Verzeichnis in Zahlenfeldern statt Objekten,
// gleiche kleine Kacheln (Meer, Wald …) nur einmal gespeichert.
// Aufruf: node paket_zusammen.mjs <ausgabe.pmtiles> <grundkarte.pmtiles> <wege.pmtiles|-> <stück1> <stück2> …  (Stücke in Nummernfolge)
import fs from "node:fs";
import zlib from "node:zlib";
import crypto from "node:crypto";
import { PmLeser } from "../pmtiles_node.mjs";

const [, , AUS, GRUND, WEGE, ...TEILE] = process.argv;
const G = new PmLeser(GRUND), W = WEGE && WEGE !== "-" ? new PmLeser(WEGE) : null;
const tmp = AUS + ".daten"; const fd = fs.openSync(tmp, "w"); let pos = 0;
let cap = 1 << 24, n = 0;
let EID = new Float64Array(cap), EOFF = new Float64Array(cap), ELEN = new Uint32Array(cap), ERUN = new Uint32Array(cap);
const wachse = () => { cap *= 2; const g = (A, T) => { const x = new T(cap); x.set(A); return x; };
  EID = g(EID, Float64Array); EOFF = g(EOFF, Float64Array); ELEN = g(ELEN, Uint32Array); ERUN = g(ERUN, Uint32Array); };
const gleich = new Map(); const GLEICH_MAX = 30e6; let kacheln = 0, lastId = -1, einzig = 0;
const KB = 1 << 20; const puffer = Buffer.alloc(64 * KB); let pf = 0;
const schreibe = d => { if (pf + d.length > puffer.length) { fs.writeSync(fd, puffer, 0, pf); pf = 0; } if (d.length > puffer.length) { fs.writeSync(fd, d); } else { d.copy(puffer, pf); pf += d.length; } pos += d.length; };
function add(id, d) {
  if (id <= lastId) throw new Error("Reihenfolge " + id); lastId = id; kacheln++;
  let off; const key = d.length < 4096 ? crypto.createHash("md5").update(d).digest("base64") : null;
  if (key) off = gleich.get(key);
  if (off === undefined) { off = pos; schreibe(d); einzig++; if (key && gleich.size < GLEICH_MAX) gleich.set(key, off); }
  if (n && EOFF[n - 1] === off && ELEN[n - 1] === d.length && EID[n - 1] + ERUN[n - 1] === id) { ERUN[n - 1]++; return; }
  if (n === cap) wachse(); EID[n] = id; EOFF[n] = off; ELEN[n] = d.length; ERUN[n] = 1; n++;
}
const t0 = Date.now();
for (const t of TEILE) {
  const f = fs.openSync(t, "r"); const gr = fs.fstatSync(f).size; let p = 0; const kopf = Buffer.alloc(12);
  while (p < gr) { fs.readSync(f, kopf, 0, 12, p); const id = kopf.readDoubleLE(0), L = kopf.readUInt32LE(8); const d = Buffer.alloc(L); fs.readSync(f, d, 0, L, p + 12); p += 12 + L; add(id, d); }
  fs.closeSync(f); fs.unlinkSync(t);
  console.log(`${t}: ${kacheln} Kacheln, ${n} Einträge, ${(pos / 1e9).toFixed(1)} GB, ${Math.round((Date.now() - t0) / 1000)} s`);
}
if (pf) fs.writeSync(fd, puffer, 0, pf); fs.closeSync(fd);

function dirBytes(a, b) {
  const o = []; const vi = v => { while (v >= 128) { o.push((v % 128) | 128); v = Math.floor(v / 128); } o.push(v); };
  vi(b - a); let last = 0; for (let i = a; i < b; i++) { vi(EID[i] - last); last = EID[i]; }
  for (let i = a; i < b; i++) vi(ERUN[i]); for (let i = a; i < b; i++) vi(ELEN[i]);
  for (let i = a; i < b; i++) { if (i > a && EOFF[i] === EOFF[i - 1] + ELEN[i - 1]) vi(0); else vi(EOFF[i] + 1); }
  return zlib.gzipSync(Buffer.from(o), { level: 9 });
}
function dirAus(R) { // R = [{id, off, len}] (Blätter)
  const o = []; const vi = v => { while (v >= 128) { o.push((v % 128) | 128); v = Math.floor(v / 128); } o.push(v); };
  vi(R.length); let last = 0; for (const e of R) { vi(e.id - last); last = e.id; } for (const e of R) vi(0); for (const e of R) vi(e.len);
  for (let i = 0; i < R.length; i++) { const e = R[i]; if (i > 0 && e.off === R[i - 1].off + R[i - 1].len) vi(0); else vi(e.off + 1); }
  return zlib.gzipSync(Buffer.from(o), { level: 9 });
}
let root = dirBytes(0, n), leaves = [], leafLen = 0;
if (root.length > 16384 - 127) {
  for (let gr = 4096; ; gr *= 2) {
    const R = []; leaves = []; leafLen = 0;
    for (let i = 0; i < n; i += gr) { const b = dirBytes(i, Math.min(n, i + gr)); R.push({ id: EID[i], off: leafLen, len: b.length }); leaves.push(b); leafLen += b.length; }
    root = dirAus(R); if (root.length <= 16384 - 127) break;
  }
}
const mg = G.meta(), mw = W ? W.meta() : {};
const vl = [...(mg.vector_layers || []), ...(mw.vector_layers || []),
  { id: "lcf_landcover", minzoom: 10, maxzoom: 12, fields: { class: "String", subclass: "String" } },
  { id: "wcf_water", minzoom: 9, maxzoom: 10, fields: { class: "String" } },
  { id: "wnc_water_name", minzoom: 13, maxzoom: 14, fields: { name: "String", gbw: "Number", gbh: "Number" } }];
const metaB = zlib.gzipSync(Buffer.from(JSON.stringify({ ...mg, name: "qfe-active Kartenpaket Welt", description: "Grundkarte + Wege + eingebackene Ebenen (lcf, wcf, wnc)", vector_layers: vl })));
const H = Buffer.alloc(127); H.write("PMTiles", 0, "latin1"); H[7] = 3;
const w64 = (o, v) => { H.writeUInt32LE(v % 4294967296, o); H.writeUInt32LE(Math.floor(v / 4294967296), o + 4); };
const rootOff = 127, metaOff = rootOff + root.length, leafOff = metaOff + metaB.length, dataOff = leafOff + leafLen;
w64(8, rootOff); w64(16, root.length); w64(24, metaOff); w64(32, metaB.length); w64(40, leafOff); w64(48, leafLen); w64(56, dataOff); w64(64, pos);
w64(72, kacheln); w64(80, n); w64(88, einzig);
H[96] = 1; H[97] = 2; H[98] = 2; H[99] = 1; H[100] = +(process.env.PAKET_MINZ ?? 0); H[101] = Math.max(G.maxz, W ? W.maxz : 0);
H.writeInt32LE(-1800000000, 102); H.writeInt32LE(-850511287, 106); H.writeInt32LE(1800000000, 110); H.writeInt32LE(850511287, 114);
H[118] = 2; H.writeInt32LE(0, 119); H.writeInt32LE(0, 123);
const out = fs.openSync(AUS, "w"); let p = 0;
for (const b of [H, root, metaB, ...leaves]) { fs.writeSync(out, b, 0, b.length, p); p += b.length; }
// Daten anhängen (Datei kann über 100 GB groß sein: in großen Blöcken kopieren)
{ const src = fs.openSync(tmp, "r"); const B = Buffer.alloc(64 * KB * 16); let q = 0, r;
  while ((r = fs.readSync(src, B, 0, B.length, q)) > 0) { fs.writeSync(out, B, 0, r, p); p += r; q += r; } fs.closeSync(src); fs.unlinkSync(tmp); }
fs.closeSync(out);
const info = { kacheln, eintraege: n, einzig, bytes: p, huelle: [-180, -85.0511, 180, 85.0511], vector_layers: vl };
fs.writeFileSync(AUS + ".json", JSON.stringify(info));
console.log("fertig:", JSON.stringify({ kacheln, eintraege: n, einzig, gb: +(p / 1e9).toFixed(1), sekunden: Math.round((Date.now() - t0) / 1000) }));
