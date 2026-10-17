# Gipfelbuch – Hinweise für Claude

Tourenapp von Marcel (Aktivverein quer.feld.ein): Karte und Planung wie bergfex, Entdecken und Feed wie komoot. Antworten immer auf Deutsch, verständlich, ohne Fachchinesisch.

Ausführliches Projektwissen (alle Anweisungen, feste Regeln, offene Punkte, Funktionsübersicht) liegt in Marcels Obsidian-Vault unter `02 Projekte/Tourenapp Gipfelbuch/` (Ordner `Projektwissen` und `Funktionsübersicht Gipfelbuch.md`). Wenn erreichbar, dort zuerst lesen.

## Aufbau
- `index.html` – die ganze App (MapLibre 5, OpenFreeMap, BRouter, Overpass, Photon, Open-Meteo, Wikipedia/Commons/Panoramax, supabase-js). Versionsnummer im Element `id="ver"` bei jeder Änderung um 1 erhöhen.
- `sw.js` – Zwischenspeicher am Gerät (Shell networkFirst, Kacheln/Höhenmodell cacheFirst).
- `cf-worker/src/index.js` + `wrangler.toml` – Cloudflare Worker „gipfelbuch“: `/overpass` (7 Tage KV-Cache), `/dem/z/x/y.png` (Höhenmodell, R2), `/api/foto` und `/fotos/*` (Fotos in R2 „qfeaktivfotos“, Westeuropa).
- Supabase-Projekt `vehrlrjegxlhxibrxqxm` (Frankfurt): profiles, user_data, friendships, activities, likes – alle mit RLS. Tabelle `posts` ist alt/leer; Löschen nur mit Marcels Zustimmung.

## Veröffentlichen
- Push auf `master` → GitHub Pages und Cloudflare Workers Builds laufen automatisch.
- Prüfen: `gh api repos/aktivvereinquerfeldein/gipfelbuch/commits/<sha>/check-runs` (deploy, build, Workers Builds = success).
- Live: https://gipfelbuch.quer-feld-ein.workers.dev (Haupt) und https://aktivvereinquerfeldein.github.io/gipfelbuch/

## Regeln (Kurzfassung)
- Vor dem Push testen (Playwright mit nachgebildeten Diensten, Handy 390×844 und Mac), Skript auf Syntaxfehler prüfen.
- Festgelegte Zoomstufen und Kartenregeln nicht ohne Auftrag ändern (siehe Funktionsübersicht 2.2 und „04 Feste Regeln und Entscheidungen“).
- Zeiten: Formel max(Distanz-Zeit, Höhen-Zeit) + min/2 mit bergfex-Werten (TIMEP); Moderat = bergfex ×1,3 (MOD_F), Tempo Entspannt 1,27 / Moderat 1 / Schnell 0,77.
- Nie Passwörter/Tokens eingeben, keine Konten anlegen. Nichts in Vault oder Datenbank löschen/überschreiben ohne Rückfrage.
- Nach jeder Änderung: Kopie in den Vault (`Webseite/index.html`, `Tourenapp.html`), Daily Note und Funktionsübersicht ergänzen (ohne Emojis), kurz berichten.
- Commit-Nachrichten auf Deutsch, Format „vXXXX: was neu ist“.
