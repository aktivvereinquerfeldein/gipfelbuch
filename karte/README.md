# Gipfelbuch – eigener Kartenserver

Alles, was die Karte braucht, liegt im eigenen Cloudflare-Speicher (R2 „qfeaktivfotos“, Westeuropa) und wird vom Worker
„gipfelbuch“ ausgeliefert. Fremde Dienste (OpenFreeMap, Overpass, fonts.openmaptiles.org) bleiben als Ersatz, falls der eigene
Server nicht antwortet oder ein Gebiet (noch) nicht enthalten ist. Gebiet vorerst: Österreich + Südtirol.

## Was liegt wo (R2)
| Pfad | Inhalt | Erzeugt von |
|---|---|---|
| `karte/aktuell.json` | welche Version gilt, Gebiet, Zellen-Abdeckung | `freischalten.py` |
| `karte/<Datum>/grundkarte.pmtiles` | Grundkarte (OpenMapTiles-Schema wie OpenFreeMap, Zoom 0–14) | Planetiler |
| `karte/<Datum>/zellen/<ebene>/<ix>_<iy>.json.gz` | Wege, Hütten, Parkplätze, Sperren … je Zelle, gleiches Format wie Overpass | `zellen.py` |
| `karte/<Datum>/bericht.md` | Vergleich mit der Vorversion | `vergleich.py` |
| `dem/z/x/y.png` | Höhenmodell (Terrarium) Zoom 0–13 | `hoehen.py dem` |
| `hang/z/x/y.png` | Hangneigung Zoom 11–14 (nur Kacheln mit steilen Stellen) | `hoehen.py hang` |
| `karte/hoehenlinien-<Datum>.pmtiles` | Höhenlinien Zoom 12–15 | `hoehenlinien.mjs` + `packen.py` |
| `fonts/<Schrift>/<Bereich>.pbf` | Schriften, werden beim ersten Abruf gespeichert | Worker |

## Aktualisieren
GitHub Action `.github/workflows/karte-aktualisieren.yml`: automatisch am 3. März, Juni, September, Dezember; von Hand über
GitHub → Actions → „Karte aktualisieren“. Verschwinden in einer Region über 2 % der Wege, wird NICHT freigeschaltet.
Alte Versionen bleiben liegen (Löschen nur nach Rückfrage).

### Vierteljährlicher Abgleich ohne KI (Prompt 3)
- `inventar.py` legt je Version ein Inventar an (`karte/<Version>/inventar.tsv.gz`: Wege mit SAC, Markierung, Zutritt, Downhill,
  Klettersteige, Hütten, Parkplätze, Bergbahnen je Bundesland/Bezirk) und vergleicht es mit der Vorversion. Über 2 % weniger Wege
  in einem Bundesland = nicht freischalten. Volle Liste: `karte/<Version>/aenderungen.json.gz`.
- Neue Hütten (und Bergbahnen/Klettersteige, sobald diese Tabellen befüllt sind) kommen mit `recherche=offen` in die Datenbank,
  Parkplätze über `parkplaetze.py db-neu`; Verschwundenes wird nur „nicht_mehr_gefunden“. Sperren nur als Liste im Bericht.
- Danach „Karte prüfen“ (`karte-pruefen.yml`, `pruefen.mjs`): Vergleichsbilder Handy/Mac an 5 Stellen gegen den letzten Lauf,
  Ladezeiten, je eine Route pro Aktivität mit Sperren-Prüfung.
- Ergebnis für Claude: nur `daten/karte/<Version>.md` lesen (Bilder in `daten/karte/bilder/` nur bei Auffälligkeit ansehen).

## Weiteres Land dazunehmen
1. Im Workflow die Geofabrik-Datei des Landes laden (z. B. `europe/germany-latest.osm.pbf` + `germany.poly`),
   mit `osmium merge` dazunehmen und den Umriss bei `gebiet.py abdeckung` anhängen; `GEBIETE` ergänzen.
2. Für Höhendaten den Lauf mit „hoehen“ starten. 3. Für Routen die 5°-Kacheln in `brouter/einrichten.sh` (KACHELN) ergänzen.

## Routen-Server (BRouter)
`brouter/einrichten.sh` richtet auf einem gemieteten Ubuntu-Server BRouter mit den App-Profilen ein (Profile werden aus
index.html geholt, Straßendaten wöchentlich von brouter.de). Erst nach Marcels Okay buchen.
