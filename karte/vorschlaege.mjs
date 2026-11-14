// Tourenvorschläge vorab berechnen (v2066, Marcel 14.11.2026: „ja mit weg 1 anfangen“) – ohne KI.
// Öffnet die App (index.html aus diesem Projekt, alle Dienste live), leitet die Routenanfragen auf den eigenen Routenrechner
// (BRouter auf diesem Rechner, Port 17777) um und ruft je Kartenstück (0,1°) die Funktion sgVorab() der App auf.
// So gelten genau dieselben Regeln wie bei der Suche in der App (Startpunkte, Wegwahl, Zeiten, Bike & Hike).
// Aufruf: node karte/vorschlaege.mjs <App-Adresse> <W,S,E,N> <Ausgabe-Ordner> <teil> <von> <stand>
// Ergebnis: <Ausgabe>/<ix>_<iy>.json.gz (Touren) und <ix>_<iy>-l.json.gz (Linien) je Kartenstück, dazu <Ausgabe>/stat-<teil>.json.
// Ein Kartenstück, das nicht sauber berechnet werden konnte, bekommt KEINE Datei: dort rechnet die App wie bisher selbst.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const [APP, BOX, AUS, TEIL = "0", VON = "1", STAND = new Date().toISOString().slice(0, 10)] = process.argv.slice(2);
const [W, S, E, N] = BOX.split(",").map(Number);
const Z = 0.1, BR = "http://127.0.0.1:17777";
const HTML = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
fs.mkdirSync(AUS, { recursive: true });

const zellen = [];
for (let x = Math.floor(W / Z + 1e-9); x <= Math.floor(E / Z - 1e-9); x++) for (let y = Math.floor(S / Z + 1e-9); y <= Math.floor(N / Z - 1e-9); y++) zellen.push([x, y]);
const meine = zellen.filter((_, i) => i % +VON === +TEIL);
console.log(`Gebiet ${BOX}: ${zellen.length} Kartenstücke, Teil ${TEIL}/${VON}: ${meine.length}`);

const PROXY = process.env.VORAB_PROXY;   // nur für Tests hinter einem Proxy
const browser = await chromium.launch({ executablePath: process.env.VORAB_CHROME || undefined, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"], ...(PROXY ? { proxy: { server: PROXY, bypass: "127.0.0.1,localhost" } } : {}) });
let routen = 0, routenFehler = 0;
const demMem = new Map(), opMem = new Map(), zahl = {}; const zaehl = (k) => { zahl[k] = (zahl[k] || 0) + 1; };
async function neueSeite() {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, serviceWorkers: "block", ignoreHTTPSErrors: !!PROXY });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("Seitenfehler:", String(e).slice(0, 200)));
  const start = new RegExp("^" + APP.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "/?(index\\.html)?(\\?.*)?$");
  await page.route(start, (r) => r.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: HTML }));
  // Routenanfragen (auch das Hochladen der eigenen Profile) an den Routenrechner auf diesem Rechner
  await page.route(/^https:\/\/brouter\.de\/brouter/, async (route) => {
    const req = route.request(); const u = new URL(req.url()); routen++;
    try {
      const r = await fetch(BR + u.pathname + u.search, { method: req.method(), body: req.method() === "POST" ? req.postDataBuffer() : undefined });
      const body = Buffer.from(await r.arrayBuffer());
      if (!r.ok) routenFehler++;
      await route.fulfill({ status: r.status, headers: { "access-control-allow-origin": "*", "content-type": r.headers.get("content-type") || "application/json" }, body });
    } catch (e) { routenFehler++; await route.fulfill({ status: 503, headers: { "access-control-allow-origin": "*" }, body: "Routenrechner nicht erreichbar" }); }
  });
  // Höhen direkt von der Quelle (gleiche Kacheln wie der eigene Server sie weiterreicht), auf diesem Rechner gemerkt – entlastet den eigenen Server
  await page.route(/\/dem\/\d+\/\d+\/\d+\.png$/, async (route) => {
    const m = route.request().url().match(/\/dem\/(\d+)\/(\d+)\/(\d+)\.png$/); const k = `${m[1]}/${m[2]}/${m[3]}`;
    try {
      if (!demMem.has(k)) demMem.set(k, (async () => { for (let v = 0; ; v++) { try { const r = await fetch(`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${k}.png`, { signal: AbortSignal.timeout(20000) }); if (r.ok) return Buffer.from(await r.arrayBuffer()); if (r.status === 404) return null; throw new Error("HTTP " + r.status); } catch (e) { if (v >= 3) throw e; await new Promise((z) => setTimeout(z, 2000 * (v + 1))); } } })());
      const b = await demMem.get(k);
      if (!b) return route.fulfill({ status: 404, headers: { "access-control-allow-origin": "*" }, body: "" });
      await route.fulfill({ status: 200, headers: { "access-control-allow-origin": "*", "content-type": "image/png" }, body: b });
    } catch (e) { demMem.delete(k); zaehl("dem-fehler"); await route.fulfill({ status: 503, headers: { "access-control-allow-origin": "*" }, body: "" }); }
  });
  // Kartenabfragen (Startpunkte, Seen …): Antworten auf diesem Rechner merken und bei Fehlern in Ruhe wiederholen
  await page.route(/\/overpass(\?|$)/, async (route) => {
    const req = route.request(); const body = req.postData() || ""; const key = req.method() + " " + req.url() + " " + body;
    try {
      if (!opMem.has(key)) opMem.set(key, (async () => { let last = null;
        for (let v = 0; v < 5; v++) { try { const r = await fetch(req.url(), { method: req.method(), body: req.method() === "POST" ? body : undefined, headers: { "content-type": req.headers()["content-type"] || "application/x-www-form-urlencoded" }, signal: AbortSignal.timeout(120000) }); const t = await r.text(); if (r.ok && t.trim().startsWith("{")) return t; last = "HTTP " + r.status; } catch (e) { last = String(e.message || e); } zaehl("overpass-wiederholt"); await new Promise((z) => setTimeout(z, 15000 * (v + 1))); }
        throw new Error(last); })());
      const t = await opMem.get(key);
      await route.fulfill({ status: 200, headers: { "access-control-allow-origin": "*", "content-type": "application/json" }, body: t });
    } catch (e) { opMem.delete(key); zaehl("overpass-fehler"); await route.fulfill({ status: 504, headers: { "access-control-allow-origin": "*" }, body: "Kartenabfrage fehlgeschlagen" }); }
  });
  // Kartenbild wird hier nicht gebraucht: Relief, Höhenlinien, Hangneigung, Schriften und fremde Kartenbilder gar nicht erst laden
  await page.route(/\/karte\/(relief|relief1m|reliefw|hang|hoehenlinien|fels|land|landwelt|wege)\/|\/fonts\/|openfreemap\.org|opentopomap\.org|basemap\.at|arcgisonline\.com|panoramax|mapillary|wikimedia|wikipedia|open-meteo/, (r) => r.abort());
  page.on("response", (r) => { if (r.status() >= 400 && r.status() !== 404) zaehl(r.status() + " " + new URL(r.url()).pathname.split("/").slice(0, 3).join("/")); });
  page.on("requestfailed", (r) => { const u = new URL(r.url()); if (!/relief|hang|hoehenlinien|fonts|fels|land|wege/.test(u.pathname) && /workers\.dev|supabase|photon/.test(u.hostname)) zaehl("abgebrochen " + u.pathname.split("/").slice(0, 3).join("/")); });
  await page.goto(APP + "/", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof sgVorab === "function" && typeof KS !== "undefined", null, { timeout: 60000 });
  await page.waitForFunction(() => KS.ok === true, null, { timeout: 60000 }).catch(() => { throw new Error("eigener Kartenserver nicht bereit (KS.ok)"); });
  await page.evaluate(() => { try { map.jumpTo({ center: [-30, 40], zoom: 3 }); } catch (e) {} });   // Karte aufs offene Meer: es wird nichts gezeichnet und kaum etwas geladen
  return { ctx, page };
}

const stat = { stand: STAND, box: BOX, teil: +TEIL, von: +VON, zellen: meine.length, fertig: 0, leer: 0, fehler: [], ziele: 0, touren: 0, ohne: 0, routen: 0, je: {} };
let S0 = await neueSeite(), seit = 0;
for (const [ix, iy] of meine) {
  let ok = null;
  for (let v = 0; v < 3 && !ok; v++) {
    try {
      if (seit >= 25) { await S0.ctx.close(); S0 = await neueSeite(); seit = 0; }
      const t0 = Date.now();
      const r = await Promise.race([
        S0.page.evaluate(([x, y]) => sgVorab(x, y), [ix, iy]),
        new Promise((_, no) => setTimeout(() => no(new Error("Zeit (10 min)")), 10 * 60000)),
      ]);
      if (r.stat.fehler) throw new Error(`${r.stat.fehler} Ziele mit Fehler (${r.stat.fehlerText || ""})`);
      ok = r; seit++;
      console.log(`${ix}_${iy}: ${r.stat.ziele} Ziele, ${r.touren.length} Touren, ${r.stat.ohne} ohne Tour, ${Math.round((Date.now() - t0) / 1000)} s ${JSON.stringify(r.stat.sek || {})} Startpunkte ${r.stat.startpunkte ?? "-"}, Routen ${r.stat.routen}, zu langsam ${r.stat.langsam ?? 0}`);
    } catch (e) {
      console.log(`${ix}_${iy}: Versuch ${v + 1} fehlgeschlagen – ${String(e.message || e).slice(0, 160)} | Netz: ${JSON.stringify(zahl)}`);
      try { await S0.ctx.close(); } catch (_) {}
      await new Promise((r) => setTimeout(r, 15000 * (v + 1)));
      S0 = await neueSeite(); seit = 0;
    }
  }
  if (!ok) { stat.fehler.push(`${ix}_${iy}`); continue; }
  if (ok.linien.length !== ok.touren.length) { stat.fehler.push(`${ix}_${iy}`); continue; }
  // zwei Dateien: Liste der Touren (klein, wird für die Auswahl geladen) und die Linien dazu (nur für die gezeigten Touren)
  fs.writeFileSync(path.join(AUS, `${ix}_${iy}.json.gz`), zlib.gzipSync(JSON.stringify({ v: 1, stand: STAND, touren: ok.touren })));
  fs.writeFileSync(path.join(AUS, `${ix}_${iy}-l.json.gz`), zlib.gzipSync(JSON.stringify({ v: 1, stand: STAND, linien: ok.linien })));
  stat.fertig++; if (!ok.touren.length) stat.leer++;
  stat.ziele += ok.stat.ziele; stat.touren += ok.touren.length; stat.ohne += ok.stat.ohne;
  for (const t of ok.touren) stat.je[t.a] = (stat.je[t.a] || 0) + 1;
}
stat.routen = routen; stat.routenFehler = routenFehler; stat.netz = zahl;
await browser.close();
fs.writeFileSync(path.join(AUS, `stat-${TEIL}.json`), JSON.stringify(stat));
console.log(JSON.stringify(stat));
// zu viele Ausfälle = der Lauf zählt nicht (nichts wird freigeschaltet)
if (stat.fehler.length > Math.max(1, meine.length * 0.1)) { console.log("::error::zu viele Kartenstücke ohne Ergebnis"); process.exit(2); }
if (routen > 50 && routenFehler > routen * 0.5) { console.log("::error::Routenrechner lieferte überwiegend Fehler"); process.exit(3); }
process.exit(0);   // offene Zeitgrenzen (20 min je Kartenstück) würden den Ablauf sonst unnötig offen halten
