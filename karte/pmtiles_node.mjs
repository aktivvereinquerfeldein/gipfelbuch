// Kleines PMTiles-v3-Werkzeug für Node (Lesen aus einer Datei, Schreiben einer neuen Datei).
// Gleiche Kachel-Nummern (Hilbert) und Verzeichnis-Aufbau wie im Worker (cf-worker/src/karte.js).
import fs from "node:fs";
import zlib from "node:zlib";

export function zxyZuId(z, x, y) {
  let acc = 0; for (let a = 0; a < z; a++) acc += 4 ** a;
  const n = 2 ** z; let d = 0, px = x, py = y;
  for (let s = n / 2; s >= 1; s /= 2) {
    const rx = (px & s) > 0 ? 1 : 0, ry = (py & s) > 0 ? 1 : 0;
    d += s * s * ((3 * rx) ^ ry);
    if (ry === 0) { if (rx === 1) { px = n - 1 - px; py = n - 1 - py; } const t = px; px = py; py = t; }
  }
  return acc + d;
}
export function idZuZxy(id) {
  let acc = 0, z = 0;
  for (;;) { const c = 4 ** z; if (acc + c > id) break; acc += c; z++; }
  const n = 2 ** z; let t = id - acc, x = 0, y = 0;
  for (let s = 1; s < n; s *= 2) {
    const rx = 1 & Math.floor(t / 2), ry = 1 & (t ^ rx);
    if (ry === 0) { if (rx === 1) { x = s - 1 - x; y = s - 1 - y; } const q = x; x = y; y = q; }
    x += s * rx; y += s * ry; t = Math.floor(t / 4);
  }
  return [z, x, y];
}

function varint(buf, st) { let v = 0, sh = 0, b; do { b = buf[st.p++]; v += (b & 0x7f) * 2 ** sh; sh += 7; } while (b & 0x80); return v; }
function u64(b, o) { return b.readUInt32LE(o) + b.readUInt32LE(o + 4) * 4294967296; }
function parseDir(u8) {
  const st = { p: 0 }; const n = varint(u8, st); const E = new Array(n);
  let last = 0; for (let i = 0; i < n; i++) { last += varint(u8, st); E[i] = { id: last }; }
  for (let i = 0; i < n; i++) E[i].run = varint(u8, st);
  for (let i = 0; i < n; i++) E[i].len = varint(u8, st);
  for (let i = 0; i < n; i++) { const v = varint(u8, st); E[i].off = (v === 0 && i > 0) ? E[i - 1].off + E[i - 1].len : v - 1; }
  return E;
}

export class PmLeser {
  constructor(datei) {
    this.fd = fs.openSync(datei, "r");
    const h = this.lies(0, 127);
    if (h.toString("latin1", 0, 7) !== "PMTiles" || h[7] !== 3) throw new Error("kein PMTiles v3: " + datei);
    Object.assign(this, { rootOff: u64(h, 8), rootLen: u64(h, 16), metaOff: u64(h, 24), metaLen: u64(h, 32), leafOff: u64(h, 40), leafLen: u64(h, 48),
      dataOff: u64(h, 56), dataLen: u64(h, 64), ic: h[97], tc: h[98], typ: h[99], minz: h[100], maxz: h[101],
      bounds: [h.readInt32LE(102) / 1e7, h.readInt32LE(106) / 1e7, h.readInt32LE(110) / 1e7, h.readInt32LE(114) / 1e7], center: [h[118], h.readInt32LE(119) / 1e7, h.readInt32LE(123) / 1e7] });
    this.root = this.dir(this.rootOff, this.rootLen); this.leaves = new Map();
  }
  lies(off, len) { const b = Buffer.alloc(len); fs.readSync(this.fd, b, 0, len, off); return b; }
  dir(off, len) { const raw = this.lies(off, len); return parseDir(this.ic === 2 ? zlib.gunzipSync(raw) : raw); }
  meta() { if (!this.metaLen) return {}; const raw = this.lies(this.metaOff, this.metaLen); return JSON.parse((this.ic === 2 ? zlib.gunzipSync(raw) : raw).toString()); }
  finde(E, id) { let lo = 0, hi = E.length - 1; while (lo <= hi) { const m = (lo + hi) >> 1; if (E[m].id < id) lo = m + 1; else if (E[m].id > id) hi = m - 1; else return m; } return hi; }
  // Rohdaten (wie gespeichert, meist gzip) oder null
  kachel(z, x, y) {
    if (z < this.minz || z > this.maxz) return null;
    const id = zxyZuId(z, x, y); let E = this.root;
    for (let t = 0; t < 4; t++) {
      const i = this.finde(E, id); if (i < 0) return null; const e = E[i];
      if (e.run > 0) { if (id >= e.id + e.run) return null; return this.lies(this.dataOff + e.off, e.len); }
      let L = this.leaves.get(e.off); if (!L) { L = this.dir(this.leafOff + e.off, e.len); if (this.leaves.size > 256) this.leaves.delete(this.leaves.keys().next().value); this.leaves.set(e.off, L); }
      E = L;
    }
    return null;
  }
  // alle Kachel-Nummern (Läufe aufgelöst) in Reihenfolge
  *ids() {
    const gehe = function* (self, E) { for (const e of E) { if (e.run === 0) yield* gehe(self, self.dir(self.leafOff + e.off, e.len)); else for (let k = 0; k < e.run; k++) yield e.id + k; } };
    yield* gehe(this, this.root);
  }
  // nur Kachel-Nummern in [lo, hi) – Verzeichnisteile außerhalb werden gar nicht gelesen (Welt: Arbeit in Stücke teilen)
  *idsBereich(lo, hi) {
    const gehe = function* (self, E, ende) {
      for (let i = 0; i < E.length; i++) { const e = E[i], bis = i + 1 < E.length ? E[i + 1].id : ende;
        if (bis <= lo) continue; if (e.id >= hi) return;
        if (e.run === 0) yield* gehe(self, self.dir(self.leafOff + e.off, e.len), bis);
        else for (let k = 0; k < e.run; k++) { const id = e.id + k; if (id >= lo && id < hi) yield id; } } };
    yield* gehe(this, this.root, Infinity);
  }
}

function dirBytes(E) {
  const o = []; const vi = v => { while (v >= 128) { o.push((v & 127) | 128); v = Math.floor(v / 128); } o.push(v); };
  vi(E.length); let last = 0; for (const e of E) { vi(e.id - last); last = e.id; }
  for (const e of E) vi(e.run); for (const e of E) vi(e.len);
  for (let i = 0; i < E.length; i++) { const e = E[i]; if (i > 0 && e.off === E[i - 1].off + E[i - 1].len) vi(0); else vi(e.off + 1); }
  return zlib.gzipSync(Buffer.from(o), { level: 9 });
}

// Schreibt Kacheln in aufsteigender Reihenfolge (add mit wachsender id), gleiche Inhalte nur einmal
export class PmSchreiber {
  constructor(datei) { this.datei = datei; this.tmp = datei + ".daten"; this.fd = fs.openSync(this.tmp, "w"); this.pos = 0; this.E = []; this.gleich = new Map(); this.lastId = -1; this.n = 0; }
  add(id, data) { // data = gzip-Kachel (Buffer)
    if (id <= this.lastId) throw new Error("Reihenfolge"); this.lastId = id; this.n++;
    const key = data.length < 2048 ? data.toString("base64") : null; let off = key ? this.gleich.get(key) : undefined;
    if (off === undefined) { off = this.pos; fs.writeSync(this.fd, data, 0, data.length, this.pos); this.pos += data.length; if (key) this.gleich.set(key, off); }
    const L = this.E[this.E.length - 1];
    if (L && L.off === off && L.len === data.length && L.id + L.run === id) L.run++;
    else this.E.push({ id, off, len: data.length, run: 1 });
  }
  fertig({ minz, maxz, bounds, center, meta }) {
    fs.closeSync(this.fd);
    let root, leaves = Buffer.alloc(0);
    root = dirBytes(this.E);
    if (root.length > 16384 - 127) {
      for (let gr = 4096; ; gr *= 2) {
        const R = [], teile = []; let lo = 0;
        for (let i = 0; i < this.E.length; i += gr) { const b = dirBytes(this.E.slice(i, i + gr)); R.push({ id: this.E[i].id, off: lo, len: b.length, run: 0 }); teile.push(b); lo += b.length; }
        root = dirBytes(R); if (root.length <= 16384 - 127) { leaves = Buffer.concat(teile); break; }
      }
    }
    const metaB = zlib.gzipSync(Buffer.from(JSON.stringify(meta || {})));
    const H = Buffer.alloc(127); H.write("PMTiles", 0, "latin1"); H[7] = 3;
    const w64 = (o, v) => { H.writeUInt32LE(v % 4294967296, o); H.writeUInt32LE(Math.floor(v / 4294967296), o + 4); };
    const rootOff = 127, metaOff = rootOff + root.length, leafOff = metaOff + metaB.length, dataOff = leafOff + leaves.length;
    w64(8, rootOff); w64(16, root.length); w64(24, metaOff); w64(32, metaB.length); w64(40, leafOff); w64(48, leaves.length); w64(56, dataOff); w64(64, this.pos);
    w64(72, this.n); w64(80, this.E.length); w64(88, this.gleich.size || this.E.length);
    H[96] = 1; H[97] = 2; H[98] = 2; H[99] = 1; H[100] = minz; H[101] = maxz;
    H.writeInt32LE(Math.round(bounds[0] * 1e7), 102); H.writeInt32LE(Math.round(bounds[1] * 1e7), 106); H.writeInt32LE(Math.round(bounds[2] * 1e7), 110); H.writeInt32LE(Math.round(bounds[3] * 1e7), 114);
    H[118] = center[0]; H.writeInt32LE(Math.round(center[1] * 1e7), 119); H.writeInt32LE(Math.round(center[2] * 1e7), 123);
    const out = fs.openSync(this.datei, "w"); let p = 0;
    for (const b of [H, root, metaB, leaves]) { fs.writeSync(out, b, 0, b.length, p); p += b.length; }
    const rd = fs.openSync(this.tmp, "r"); const buf = Buffer.alloc(1 << 24); let q = 0, n;
    while ((n = fs.readSync(rd, buf, 0, buf.length, q)) > 0) { fs.writeSync(out, buf, 0, n, p); p += n; q += n; }
    fs.closeSync(rd); fs.closeSync(out); fs.unlinkSync(this.tmp);
    return { kacheln: this.n, eintraege: this.E.length, bytes: p };
  }
}
