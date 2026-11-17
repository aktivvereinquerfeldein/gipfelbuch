# qfe-active (technisch „gipfelbuch“) – Hinweise für Claude Code

Tourenapp von Marcel (Aktivverein quer.feld.ein). Karte und Planung nach Vorbild bergfex, Entdecken und Feed nach komoot (Vorbild nur intern, siehe Regeln). Antworten immer auf Deutsch, locker und verständlich, ohne Fachchinesisch. Das Thema ME/CFS nicht von dir aus ansprechen.

## Sitzungsstart
1. `git pull` (Code ist maßgeblich; Doku kann älter sein).
2. Vault-Regeln lesen: `/Users/mavo/Documents/Obsidian Vault/CLAUDE.md` (Inbox-Routine, Schreibregeln) und befolgen.
3. Projektwissen nur bei Bedarf lesen, große Dateien gezielt durchsuchen (`grep -n`) statt ganz laden. Vor Änderungen an Karte, Planen, Entdecken, Fotos oder Datenabgleich: passenden Abschnitt in 04 lesen.

## Wo steht was
Basis: `$VAULT/02 Projekte/Tourenapp Gipfelbuch/` (`$VAULT` = `/Users/mavo/Documents/Obsidian Vault`). Der Vault ist die einzige Quelle für Projektwissen; das frühere Claude-Projekt „Gipfelbuch - Tourenplaner“ wird nicht mehr gepflegt.

| Datei | Inhalt | Wann lesen |
|---|---|---|
| `Projektwissen/02 Technik und Zugänge.md` | Dienste, IDs, Datenbank, Abläufe, Funktionsnamen im Code | vor Technik-Arbeit (grep) |
| `Projektwissen/03 Alle Anweisungen und Umsetzung.md` | Marcels Anweisungen wörtlich, chronologisch (groß) | nur zum Nachschlagen |
| `Projektwissen/04 Feste Regeln und Entscheidungen.md` | verbindliche Regeln (Zoomstufen, Farben, Vorschläge, Fotos, Quellen) | vor jeder App-Änderung im betroffenen Bereich |
| `Projektwissen/05 Offene Punkte.md` | offene Aufgaben, Tests für Marcel, ausstehende Entscheidungen | bei „was ist offen?“ und nach Änderungen |
| `Projektwissen/06 Prompts Datenabgleich.md` | Vorgaben Datenabgleich (Prompt 0–6) | vor Arbeit an Hütten/Parkplätzen/Bergbahnen/Klettersteigen/Sperren |
| `Funktionsübersicht Gipfelbuch.md` | aktueller Stand jeder Funktion, Zoomstufen (2.2) | vor Änderungen an einer Funktion |
| `Datenabgleich/*.md`, `Prüfbericht *.md`, `Analyse Weltkarte …md` | Berichte | bei Bezug |
| `karte/README.md` (Repo) | eigener Kartenserver, R2-Pfade | Kartenskripte |
| `daten/<bereich>/` (Repo) | Ergebnisse/Prüflisten der Abläufe | nach Abgleich-Läufen nur bericht.md/pruefliste.md lesen |

## Aufbau (Kurz)
- `index.html` – ganze App (ca. 1,1 MB): MapLibre 5, eigene Vektorkarte (sonst OpenFreeMap), BRouter, Overpass, Photon, Open-Meteo, Commons/Panoramax, supabase-js. Version im Element `id="ver"`.
- `sw.js` (Gerätespeicher, Name `gb-shell-vN`), `manifest.webmanifest`, Symbole.
- `cf-worker/src/index.js` + `karte.js`, `wrangler.toml` – Worker „gipfelbuch“: /overpass, /dem, /api/foto, /fotos, /karte/* (Grundkarte, Zellen, Wege, Relief, Touren), /fonts. Workers AI (Fotoprüfung beim Hochladen).
- `karte/` – Skripte der GitHub-Abläufe; `.github/workflows/` – 22 Abläufe (Zeitplan siehe 02).
- Supabase `vehrlrjegxlhxibrxqxm` (Frankfurt), Cloudflare-Konto „quer-feld-ein“ (fbcade196ec6ad17a597c7ae5d75b31b), R2 `qfeaktivfotos`, KV `gipfelbuch-cache`.

## Nach jeder Änderung (ohne Rückfrage)
1. Version `id="ver"` +1; bei Logo/Symbolen auch `?v=` in index.html/manifest und `gb-shell-vN` in sw.js erhöhen.
2. Syntax prüfen (Skript aus index.html extrahieren, `node --check`), testen mit Playwright: Live-Adresse laden, nur index.html per `page.route` durch die lokale Datei ersetzen, Service Worker sperren; Handy 390×844 und Mac 1440×900, keine Skriptfehler. Zeiten am Mac messen.
3. Commit auf Deutsch „vXXXX: was neu ist“, Push auf `main` (veröffentlicht automatisch, 1–2 min).
4. Build prüfen: `gh api repos/aktivvereinquerfeldein/gipfelbuch/commits/<sha>/check-runs` (alle success) und live: `curl -s https://gipfelbuch.quer-feld-ein.workers.dev | grep -o 'id="ver"[^<]*<'`. Kommt der Build nicht an: leeren Commit pushen.
5. Vault: `cp index.html` nach `…/Webseite/index.html` und `…/Tourenapp.html` (dauerhaft freigegeben), Prüfsumme vergleichen.
6. Vault-Doku ergänzen (ohne Emojis, nichts löschen): Daily Note `$VAULT/05 Daily Notes/JJJJ-MM-TT.md` (anlegen oder ergänzen, Frontmatter tags/status/date), Funktionsübersicht, und in Projektwissen: Anweisung wörtlich + Umsetzung + Version → 03, neue Regel → 04, erledigt/neu → 05, Technik → 02.
7. Kurz berichten, was neu ist; neue Regeln Marcels immer in 04 festhalten.

## Abläufe starten/prüfen
- Start: `gh api -X POST repos/aktivvereinquerfeldein/gipfelbuch/actions/workflows/<datei>.yml/dispatches -f ref=main -f 'inputs[modus]=voll'`
- Status: `gh api repos/aktivvereinquerfeldein/gipfelbuch/actions/workflows/<datei>.yml/runs` – Folgeläufe nie blind voraussetzen, selbst nachsehen.
- Ergebnisse stehen im Repo unter `daten/`; Claude liest nur Berichte/Prüflisten und schreibt Entscheidungen in `daten/<bereich>/entscheidungen.json` (Push startet die Übernahme).
- Nie zwei Läufe „Relief 1m“ gleichzeitig. Höchstens 2–3 Recherche-Helfer gleichzeitig. In SQL über Supabase kein Semikolon im Text.

## Feste Regeln (Kurzfassung, Details in 04)
- App heißt **qfe-active**; „Gipfelbuch“ nur für die Gipfelliste im Profil. Technische Namen bleiben „gipfelbuch“.
- In ausgelieferten Dateien (index.html, sw.js, Worker, Texte, Kommentare) nie „bergfex“; neutral schreiben. Neue ausgelieferte Dateitypen in `.assetsignore` prüfen.
- Nie Passwörter/Tokens eingeben, keine Konten anlegen, nichts Kostenpflichtiges buchen.
- Vault und Datenbank: nichts überschreiben oder löschen ohne Rückfrage (Ausnahme: App-Kopien im Vault). Daten nur „nicht_mehr_gefunden“/„abgelaufen“ markieren.
- Zoomstufen, Farben, Linienfarben, Kartenbild nicht ohne Auftrag ändern. Karte immer scharf. Oberfläche: Creme-Fläche, Waldgrün Schrift, Terrakotta Hauptfarbe (auch im Dunkelmodus).
- Datenabgleich: Webseiten nur lesen, nur Fakten mit Quelle/Prüfdatum/Sicherheit, OSM nie alleinige Quelle, nichts schätzen. Abgleiche als Skript ohne KI.
- Zeiten (ab v1968) je Wegstück (TM, tmHours): zu Fuß je ca. 50 m max(Gehzeit, Steig-/Abstiegszeit) (+ halbes Kleineres beim Bergwandern) plus SAC-Zuschlag T1–T6 aus den BRouter-WayTags; Rad nach Steigung und Belag. Tempo: Entspannt 1,5 / Moderat 1 (= bergfex „Mäßig“) / Schnell 0,7. Ohne Wegdaten legHours(act,km,up,down).
- Supabase-Tabelle `posts` (alt, leer) nur mit Zustimmung löschen. Zweite Adresse GitHub Pages ist derzeit 404, Hauptadresse ist Cloudflare.
- bergfex, komoot, Outdooractive nur ansehen, dort nie etwas ändern. Unsere Routen (BRouter) bleiben; bergfex ist Vorbild für Zeiten, nicht für Wege.
- Hochladen nach Test ohne Rückfrage; alte Kartenstände darf Claude selbst aufräumen, nie den laufenden.
