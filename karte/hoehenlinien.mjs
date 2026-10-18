// Gipfelbuch-Kartenserver: Höhenlinien vorab berechnen – mit derselben Bibliothek und denselben Einstellungen
// wie die App (maplibre-contour, Abstände 12:[100,500] 13:[50,100] 14:[20,100] 15:[10,50]), damit sie gleich aussehen.
// Aufruf: node hoehenlinien.mjs <dem-ordner> <kachelliste.txt (z/x/y je Zeile)> <ziel-ordner> [teil/anzahl]
// Benötigt: npm i maplibre-contour@0.1.1 pngjs@7
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import mlcontour from "maplibre-contour";
import { PNG } from "pngjs";

const [dem, liste, ziel, teil = "0/1"] = process.argv.slice(2);
const [ti, tn] = teil.split("/").map(Number);
// genau wie in index.html (Quelle "contours")
const OPT = { thresholds: { 12: [100, 500], 13: [50, 100], 14: [20, 100], 15: [10, 50] }, contourLayer: "contours", elevationKey: "ele", levelKey: "level", extent: 4096, buffer: 1 };

function optionsForZoom(z) { // wie getOptionsForZoom in maplibre-contour
  const { thresholds, ...rest } = OPT; let levels = [], best = -Infinity;
  for (const [k, v] of Object.entries(thresholds)) { const n = +k; if (n <= z && n > best) { best = n; levels = typeof v === "number" ? [v] : v; } }
  return { levels, ...rest };
}

const manager = new mlcontour.LocalDemManager({
  demUrlPattern: "{z}/{x}/{y}", cacheSize: 400, encoding: "terrarium", maxzoom: 13, timeoutMs: 30000,
  getTile: async (url) => {
    const p = path.join(dem, url + ".png");
    if (!fs.existsSync(p)) throw new Error("fehlt " + url);
    return { data: new Blob([fs.readFileSync(p)]) };
  },
  decodeImage: async (blob) => {
    const png = PNG.sync.read(Buffer.from(await blob.arrayBuffer()));
    return mlcontour.decodeParsedImage(png.width, png.height, "terrarium", new Uint8ClampedArray(png.data.buffer, png.data.byteOffset, png.data.length));
  },
});

const tiles = fs.readFileSync(liste, "utf8").split("\n").filter(Boolean).filter((_, i) => i % tn === ti);
let n = 0, leer = 0, t0 = Date.now();
for (const t of tiles) {
  const [z, x, y] = t.split("/").map(Number);
  let res;
  try { res = await manager.fetchContourTile(z, x, y, optionsForZoom(z), new AbortController()); }
  catch (e) { console.error("Fehler", t, e.message); continue; }
  const buf = Buffer.from(res.arrayBuffer);
  n++;
  if (!buf.length) { leer++; continue; }
  const p = path.join(ziel, String(z), String(x), y + ".pbf");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, zlib.gzipSync(buf, { level: 9 }));
  if (n % 5000 === 0) console.log(`  ${n}/${tiles.length} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
}
console.log(`Höhenlinien Teil ${teil}: ${n} Kacheln, ${leer} leer, ${((Date.now() - t0) / 1000).toFixed(0)} s`);
