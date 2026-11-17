// Handy- und Mac-Test der App (ersetzt t3.py aus den früheren Sitzungen).
// Lädt die Live-Adresse, ersetzt nur index.html durch die lokale Datei, sperrt den Service Worker
// und meldet Skriptfehler sowie die Ladezeit. Aufruf (im Projektordner):
//   cd werkzeuge && npm i --silent && node test.mjs            (beide Größen)
//   node test.mjs handy | mac                                   (nur eine Größe)
// Bildschirmfotos landen in werkzeuge/bilder/ (wird nicht hochgeladen).
import { chromium } from "playwright";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const APP = "https://gipfelbuch.quer-feld-ein.workers.dev/";
const HTML = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
const VER = (HTML.match(/id="ver"[^>]*>(v\d+)</) || [])[1];
const GROESSEN = { handy: { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }, mac: { width: 1440, height: 900 } };
const wahl = process.argv[2] ? [process.argv[2]] : Object.keys(GROESSEN);
fs.mkdirSync(new URL("./bilder/", import.meta.url), { recursive: true });

const browser = await chromium.launch();
let fehlerGesamt = 0;
for (const name of wahl) {
  const g = GROESSEN[name]; const { width, height, ...rest } = g;
  const ctx = await browser.newContext({ viewport: { width, height }, ...rest, serviceWorkers: "block" });
  const page = await ctx.newPage(); const fehler = [];
  page.on("pageerror", (e) => fehler.push(String(e).slice(0, 300)));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) fehler.push("Konsole: " + m.text().slice(0, 300)); });
  await page.route(/^https:\/\/gipfelbuch\.quer-feld-ein\.workers\.dev\/?(index\.html)?(\?.*)?$/, (r) => r.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: HTML }));
  const t0 = Date.now();
  await page.goto(APP, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.map && map.loaded && map.loaded(), null, { timeout: 60000 }).catch(() => fehler.push("Karte nicht innerhalb 60 s geladen"));
  const ms = Date.now() - t0;
  await page.waitForTimeout(3000);
  const ver = await page.locator("#ver").textContent().catch(() => null);
  if (ver !== VER) fehler.push(`Version auf der Seite ${ver}, erwartet ${VER}`);
  await page.screenshot({ path: fileURLToPath(new URL(`./bilder/${name}.png`, import.meta.url)) });
  console.log(`${name} ${width}×${height}: ${ver}, Karte geladen nach ${ms} ms, ${fehler.length} Fehler`);
  for (const f of fehler) console.log("  - " + f);
  fehlerGesamt += fehler.length; await ctx.close();
}
await browser.close();
process.exit(fehlerGesamt ? 1 : 0);
