// Wanderkarte (Prompt 3, Punkte 4, 5, 7): nach dem Freischalten automatisch prüfen – ohne KI.
//  - Vergleichsbilder an festen Stellen (Handy 390x844 und Mac 1440x900) mit dem letzten Lauf vergleichen (Anteil geänderter Bildpunkte)
//  - Ladezeit je Stelle messen (bis die Karte fertig gezeichnet ist)
//  - je eine Route Wandern, Bergwandern, Klettersteig, MTB, E-MTB, Rad mit den Routen-Funktionen der App planen
//    und die Sperren-Prüfung aus den eigenen Daten (ksSperren) darauf laufen lassen
// Aufruf: node karte/pruefen.mjs <App-Adresse> <Bilder-Ordner> <ergebnis.json> <bericht-anhang.md>
// Bilder werden im Repo überschrieben (alte Stände bleiben in der Git-Geschichte). Benötigt: playwright, pngjs, pixelmatch.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import pngjs from "pngjs";
const { PNG } = pngjs;
import pixelmatch from "pixelmatch";

const [APP, BILDER, AUS, MD] = process.argv.slice(2);
const ORTE = [
  { id: "rankweil", name: "Rankweil", c: [9.643, 47.271], z: 14 },
  { id: "brandnertal", name: "Brandnertal", c: [9.737, 47.103], z: 13 },
  { id: "montafon", name: "Montafon", c: [9.92, 47.08], z: 12 },
  { id: "innsbruck", name: "Innsbruck Nordkette", c: [11.39, 47.29], z: 13 },
  { id: "suedtirol", name: "Südtirol Seiser Alm", c: [11.63, 46.54], z: 13 },
];
const GROESSEN = [{ id: "handy", w: 390, h: 844, mobile: true }, { id: "mac", w: 1440, h: 900, mobile: false }];
const ROUTEN = [
  { act: "wandern", name: "Wandern Brand – Douglasshütte", a: [9.7374, 47.1022], b: [9.7646, 47.0567] },
  { act: "bergsteigen", name: "Bergwandern Douglasshütte – Schesaplana", a: [9.7646, 47.0567], b: [9.7076, 47.0543] },
  { act: "klettersteig", name: "Klettersteig Seegrube – Hafelekar", a: [11.3797, 47.3045], b: [11.3858, 47.3127] },
  { act: "mtb", name: "MTB Rankweil – Übersaxen", a: [9.643, 47.2713], b: [9.673, 47.2539] },
  { act: "emtb", name: "E-MTB Schruns – Kapell", a: [9.9196, 47.08], b: [9.937, 47.051] },
  { act: "rad", name: "Rad Feldkirch – Rankweil", a: [9.6005, 47.2406], b: [9.643, 47.2706] },
];
const GRENZE_BILD = 15; // % geänderte Bildpunkte, ab denen eine Stelle auffällig ist

const alt = fs.existsSync(AUS) ? JSON.parse(fs.readFileSync(AUS, "utf8")) : null;
fs.mkdirSync(BILDER, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] });
const erg = { datum: new Date().toISOString().slice(0, 10), app: APP, status: null, bilder: [], routen: [], fehler: [] };

async function fertig(page, ms = 60000, start = null) {
  // Ladezeit = bis zum letzten nachgeladenen Kartenstück; fertig, wenn 1,5 s lang nichts mehr kommt (auch die eigenen Zellen)
  return page.evaluate(async ([ms, start]) => {
    const t0 = start ?? performance.now(); let last = t0;
    const on = () => (last = performance.now());
    map.on("data", on); map.on("render", on);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    while (performance.now() - t0 < ms) {
      await new Promise((r) => setTimeout(r, 200));
      if (map.loaded() && !map.isMoving() && performance.now() - last > 1500) break;
    }
    map.off("data", on); map.off("render", on);
    return Math.round(last - t0);
  }, [ms, start]);
}

async function routen(page) {
      for (const r of ROUTEN) {
        const res = await page.evaluate(async (r) => {
          const t0 = performance.now(); const out = { ok: false };
          try {
            const leg = await routeLeg({ lng: r.a[0], lat: r.a[1] }, { lng: r.b[0], lat: r.b[1] }, r.act);
            out.routing_ms = Math.round(performance.now() - t0);
            if (!leg || !leg.coords || leg.coords.length < 2) { out.fehler = "keine Route"; return out; }
            let m = 0; const c = leg.coords;
            for (let i = 1; i < c.length; i++) { const kx = 111320 * Math.cos(c[i][1] * Math.PI / 180); m += Math.hypot((c[i][0] - c[i - 1][0]) * kx, (c[i][1] - c[i - 1][1]) * 110540); }
            out.km = Math.round(m / 100) / 10; out.profil = leg.profile; out.gerade = !!leg.straight;
            const pts = c.filter((_, i) => i % 3 === 0 || i === c.length - 1).map((p) => ({ lng: p[0], lat: p[1] }));
            const t1 = performance.now();
            const sp = await ksSperren(pts);
            out.sperren_ms = Math.round(performance.now() - t1);
            out.sperren_eigen = sp !== null; // null = eigene Daten haben nicht geantwortet (App würde Overpass nehmen)
            out.sperren_wege = sp ? sp.filter((e) => e.type === "way").length : null;
            out.ok = !out.gerade && out.sperren_eigen;
          } catch (e) { out.fehler = String(e.message || e).slice(0, 160); }
          return out;
        }, r);
        erg.routen.push({ name: r.name, act: r.act, ...res });
      }
}

try {
  const st = await fetch(APP.replace(/\/$/, "") + "/karte/status").then((r) => r.json()).catch(() => null);
  erg.status = st;
  for (const g of GROESSEN) {
    const ctx = await browser.newContext({ viewport: { width: g.w, height: g.h }, isMobile: g.mobile, hasTouch: g.mobile, locale: "de-AT",
      deviceScaleFactor: 1, geolocation: { latitude: 47.27, longitude: 9.64 }, permissions: [] });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => erg.fehler.push(`${g.id}: ${String(e.message).slice(0, 160)}`));
    const t0 = Date.now();
    await page.goto(APP, { waitUntil: "load", timeout: 90000 });
    await page.waitForFunction(() => typeof map !== "undefined" && map.isStyleLoaded && map.isStyleLoaded(), null, { timeout: 90000 });
    const ersterStart = Date.now() - t0 + (await fertig(page));
    erg.bilder.push({ groesse: g.id, ort: "Start der App", ladezeit_ms: ersterStart });
    if (g.id === "mac") await routen(page);
    for (const o of ORTE) {
      const t0 = await page.evaluate(([c, z]) => { const t = performance.now(); map.jumpTo({ center: c, zoom: z, bearing: 0, pitch: 0 }); return t; }, [o.c, o.z]);
      const ms = await fertig(page, 60000, t0);
      await page.waitForTimeout(500);
      const datei = path.join(BILDER, `${g.id}-${o.id}.png`);
      let buf;
      try { buf = await page.screenshot({ type: "png", timeout: 150000, animations: "disabled", caret: "hide" }); }
      catch (e) { erg.fehler.push(`Bild ${g.id}-${o.id}: ${String(e.message).split("\n")[0]}`); erg.bilder.push({ groesse: g.id, ort: o.name, ladezeit_ms: ms, abweichung_prozent: null }); continue; }
      let diff = null;
      if (fs.existsSync(datei)) {
        try {
          const a = PNG.sync.read(fs.readFileSync(datei)), b = PNG.sync.read(buf);
          if (a.width === b.width && a.height === b.height) {
            const n = pixelmatch(a.data, b.data, null, a.width, a.height, { threshold: 0.15 });
            diff = Math.round((n / (a.width * a.height)) * 1000) / 10;
          }
        } catch (e) { erg.fehler.push(`Bildvergleich ${g.id}-${o.id}: ${e.message}`); }
      }
      fs.writeFileSync(datei, buf);
      erg.bilder.push({ groesse: g.id, ort: o.name, datei: `${g.id}-${o.id}.png`, ladezeit_ms: ms, abweichung_prozent: diff });
    }
    await ctx.close();
  }
} catch (e) {
  erg.fehler.push("Abbruch: " + String(e.message || e).slice(0, 300));
}
await browser.close();

// Bericht
const vorher = {}; (alt?.bilder || []).forEach((b) => (vorher[b.groesse + "|" + b.ort] = b.ladezeit_ms));
const auff = erg.bilder.filter((b) => b.abweichung_prozent != null && b.abweichung_prozent >= GRENZE_BILD);
const rf = erg.routen.filter((r) => !r.ok);
const z = [];
z.push("", "## Prüfung nach dem Freischalten (automatisch)", "");
z.push(`Kartenserver: Version ${erg.status?.version || "?"}, Wege-Vektorkacheln ${erg.status?.wege ? "ja" : "nein"}, Hütten-/Parkplatz-Daten ${erg.status?.gb || "nein"}.`, "");
z.push("### Vergleichsbilder und Ladezeit", "", `Abweichung = Anteil geänderter Bildpunkte gegenüber dem letzten Lauf (ab ${GRENZE_BILD} % auffällig). Bilder: daten/karte/bilder/.`, "");
z.push("| Größe | Stelle | Ladezeit | vorher | Abweichung |", "|---|---|---|---|---|");
for (const b of erg.bilder) {
  const v = vorher[b.groesse + "|" + b.ort];
  z.push(`| ${b.groesse === "handy" ? "Handy" : "Mac"} | ${b.ort} | ${(b.ladezeit_ms / 1000).toFixed(1)} s | ${v ? (v / 1000).toFixed(1) + " s" : "–"} | ${b.abweichung_prozent == null ? "erstes Bild" : b.abweichung_prozent + " %"}${b.abweichung_prozent >= GRENZE_BILD ? " **auffällig**" : ""} |`);
}
z.push("", "### Routen und Sperren-Prüfung", "", "| Route | Ergebnis | km | Routing | Sperren-Prüfung | Wege mit Einschränkung |", "|---|---|---|---|---|---|");
for (const r of erg.routen)
  z.push(`| ${r.name} | ${r.ok ? "ok" : "**Fehler" + (r.fehler ? ": " + r.fehler : r.gerade ? ": nur Luftlinie" : !r.sperren_eigen ? ": eigene Sperren-Daten fehlen" : "") + "**"} | ${r.km ?? "–"} | ${r.routing_ms != null ? (r.routing_ms / 1000).toFixed(1) + " s" : "–"} | ${r.sperren_ms != null ? r.sperren_ms + " ms" : "–"} | ${r.sperren_wege ?? "–"} |`);
if (erg.fehler.length) z.push("", "### Fehlermeldungen der App", "", ...[...new Set(erg.fehler)].slice(0, 15).map((f) => "- " + f));
z.push("", auff.length || rf.length || erg.routen.length < ROUTEN.length
  ? `**Zum Ansehen: ${auff.length} Bild(er) auffällig, ${rf.length + (ROUTEN.length - erg.routen.length)} Route(n) mit Fehler.** Bei Bildern zuerst prüfen, ob nur Beschriftungen/Daten anders sind (in Ordnung) oder Farben/Zoomstufen (nicht in Ordnung).`
  : "Alles in Ordnung: Kartenbild unverändert, alle Routen und die Sperren-Prüfung funktionieren.");
fs.writeFileSync(AUS, JSON.stringify(erg, null, 1));
fs.appendFileSync(MD, z.join("\n") + "\n");
console.log(z.join("\n"));
erg.auffaellig = auff.length + rf.length;
