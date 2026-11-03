#!/usr/bin/env python3
"""Selbsttest für den Bergbahn-Leser (karte/bb_lesen.py) – läuft vor jedem Abgleich im Ablauf „Bergbahnen“.

Die Beispielzeilen sind selbst geschrieben und bilden die Schreibweisen nach, an denen der Leser früher gescheitert ist
(Mehrfach-Namen, Datum über zwei Zeilen, Datum ohne Jahr, Wochentage in der Folgezeile, Tabellen mit Mittagspause,
gleiche Bahnnamen an verschiedenen Orten). Schlägt ein Test fehl, bricht der Lauf ab, statt falsche Zeiten einzutragen.
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bb_lesen as L

FEHLER = []
def pruefe(name, ist, soll):
    if ist != soll: FEHLER.append(f"{name}: erwartet {soll!r}, gelesen {ist!r}")

def bahn(key, name, pos=None):
    b = L.Bahn(key, name)
    if pos: b.pos = pos
    return b

J = L.HEUTE.year

# 1) Mehrere Bahnen in einer Überschrift, Zeitraum mit Leerzeichen vor dem Punkt, Wochentage in der nächsten Zeile,
#    „Letzte Berg- und Talfahrt“ mit zusammengesetzten Namen
B = [bahn("a", "Dorfbahn"), bahn("b", "Loischkopfbahn"), bahn("c", "Palüdbahn"), bahn("d", "Einhornbahn I")]
seite = [
    "Betriebszeiten im Sommer 2026",
    "Dorf- und Seebahn im Ort & Loischkopfbahn im Nachbarort",
    "Täglicher Betrieb: 14. Mai - 01. November 2026",
    "Palüdbahn",
    "Sommerbetrieb: 19. Juni - 18 . Oktober 2026",
    "Geöffnet: Freitag - Sonntag",
    "Letzte Berg- und Talfahrt:",
    "Palüd-, Dorf- & Loischkopfbahn: 16:45 Uhr",
]
r = L.lesen(seite, B, "https://example.at/oeffnungszeiten")
pruefe("Mehrfach-Überschrift Dorfbahn", [(f["von"], f["bis"]) for f in r.get("a", []) if f["art"] == "periode"], [("2026-05-14", "2026-11-01")])
pruefe("Mehrfach-Überschrift Loischkopfbahn", [(f["von"], f["bis"]) for f in r.get("b", []) if f["art"] == "periode"], [("2026-05-14", "2026-11-01")])
pruefe("Leerzeichen vor Punkt", [(f["von"], f["bis"]) for f in r.get("c", []) if f["art"] == "periode"], [("2026-06-19", "2026-10-18")])
pruefe("Wochentage Folgezeile", [f.get("tage") for f in r.get("c", []) if f["art"] == "periode"], [["fr", "sa", "so"]])
lt = {k: [f.get("letzte_tal") for f in r.get(k, []) if f["art"] == "zeiten"] for k in "abc"}
pruefe("Zusammengesetzte Namen, letzte Talfahrt", lt, {"a": ["16:45"], "b": ["16:45"], "c": ["16:45"]})
pruefe("Nicht genannte Bahn bleibt leer", r.get("d"), None)

# 2) Datum über zwei Zeilen und ohne Jahreszahl, Uhrzeit „9 bis 16.30 Uhr“
nov = datetime.date(J if datetime.date(J, 11, 8) >= L.HEUTE - datetime.timedelta(days=150) else J + 1, 11, 8)
r = L.lesen(["Täglicher Betrieb bis", "8. November", "Betriebszeiten", "Täglich von 9 bis 16.30 Uhr geöffnet!"], [bahn("e", "Seilbahn Musterort")], "https://seilbahn-musterort.at/")
per = [f for f in r.get("e", []) if f["art"] == "periode"]       # Seite der Bahn (Name in der Adresse)
pruefe("Datum über zwei Zeilen ohne Jahr", [(f["von"], f["bis"]) for f in per], [(None, nov.isoformat())])
pruefe("ohne Jahr gemerkt", [f.get("ohne_jahr") for f in per], [True])
pruefe("Uhrzeit 9 bis 16.30", [(f["zeiten"].get("erste"), f["zeiten"].get("betrieb_bis")) for f in per], [("09:00", "16:30")])

# 3) Tabelle (PDF-Spalten): Mittagspause, Wochentage, Gruppe über zwei Zeilen, gleicher Name an zwei Orten
T = [bahn("g1", "Golmerbahn 1", (9.87, 47.08)), bahn("g2", "Golmerbahn 2", (9.88, 47.09)), bahn("s", "Sennigrat Bahn I", (9.93, 47.06)),
     bahn("p1", "Panoramabahn Burtschasattel", (9.74, 47.13)), bahn("p2", "Panorama Bahn", (9.92, 47.07)), bahn("d1", "Dorfbahn", (9.74, 47.10)),
     bahn("h", "Hochalpilabahn", (9.99, 47.05)), bahn("gr", "Grasjochbahn", (9.98, 47.04)), bahn("x", "Hochalpbahn", (10.14, 47.26))]
tab = [
    "BERGBAHNEN             BETRIEBSZEITEN            FAHRZEITEN            ZU BEACHTEN",
    "Ort A golmer-beispiel.at",
    "Golmerbahn             14.05. – 17.05.2026      08:30 – 17:00 Uhr     Wochenendbetrieb",
    "                       23.05. – 18.10.2026      08:30 – 17:00 Uhr     durchgehender Betrieb",
    "Sennigrat Bahn Ort     20.06. – 04.10.2026      08:30 – 11:45 Uhr     durchgehender Betrieb",
    "                                                13:00 – 16:00 Uhr",
    "Grasjoch Bahn und      04.07. – 13.09.2026      08:30 – 17:00 Uhr     durchgehender Betrieb",
    "Hochalpila Bahn        17.09. – 20.09.2026      08:30 – 17:00 Uhr     Wochenendbetrieb",
    "Tal B",
    "Dorfbahn und           08.05. – 10.05.2026      09:00 – 16:30 Uhr     Wochenendbetrieb",
    "Panoramabahn           14.05. – 01.11.2026      09:00 – 16:30 Uhr     Fr-So",
]
t = L.tabelle(tab, T)
q = t.pop("@quellen", {})
pruefe("Tabelle Golmerbahn I/II", sorted(k for k in t if k.startswith("g") and k != "gr"), ["g1", "g2"])
pruefe("Tabelle Mittagspause", [f["zeiten"].get("betrieb_bis") for f in t.get("s", [])], ["16:00"])
pruefe("Tabelle Gruppe zwei Zeilen", (len(t.get("gr", [])), len(t.get("h", []))), (2, 2))
pruefe("Tabelle Ähnlicher Name woanders nicht", "x" in t, False)
pruefe("Tabelle gleicher Name am richtigen Ort", ("p1" in t, "p2" in t), (True, False))
pruefe("Tabelle Wochentage", [f.get("tage") for f in t.get("p1", [])][-1:], [["fr", "sa", "so"]])
pruefe("Tabelle Webseite aus Zwischenüberschrift", q.get("g1"), "golmer-beispiel.at")

# 4) Feste und Verkaufszeiten sind kein Betrieb
r = L.lesen(["70 Jahre Musterbahn – feiert mit vom 9. bis 15. November 2026", "Vorverkauf Saisonkarten vom 01.11.2026 - 04.12.2026"], [bahn("m", "Musterbahn")], "https://musterbahn.at/")
pruefe("Fest/Vorverkauf ignoriert", [f for fs in r.values() for f in fs if f["art"] == "periode"], [])

# 5) Werbespruch „das ganze Jahr“ ist kein Ganzjahresbetrieb, „ganzjährig in Betrieb“ schon
r = L.lesen(["Musterbahn - Das ganze Jahr Rundumblick"], [bahn("n", "Musterbahn")], "https://musterbahn.at/")
pruefe("Werbespruch nicht ganzjährig", [f for fs in r.values() for f in fs if f["art"] == "ganzjaehrig"], [])
r = L.lesen(["Die Musterbahn ist ganzjährig in Betrieb"], [bahn("n", "Musterbahn")], "https://musterbahn.at/")
pruefe("ganzjährig in Betrieb", len([f for fs in r.values() for f in fs if f["art"] == "ganzjaehrig"]), 1)

# 6) Bahnname, darunter „Sommersaison“, Datum, „Erste Bergfahrt“ / Uhrzeit in eigenen Zeilen
r = L.lesen(["Öffnungszeiten der Bergbahnen", "Musterhornbahn", "Sommersaison", "23.05. - 08.11.2026", "Erste Bergfahrt", "8.30 Uhr", "Letzte Talfahrt", "16.20 Uhr",
             "Talbahn", "Wintersaison", "12.12.2026 - 02.05.2027", "Erste Bergfahrt", "9.00 Uhr"], [bahn("m", "Musterhornbahn"), bahn("t", "Talbahn")], "https://muster.de/oeffnungszeiten/")
pruefe("Saison unter Bahnname", [(f["von"], f["zeiten"].get("erste"), f["zeiten"].get("letzte_tal")) for f in r.get("m", []) if f["art"] == "periode"], [("2026-05-23", "08:30", "16:20")])
pruefe("zweite Bahn", [f["von"] for f in r.get("t", []) if f["art"] == "periode"], ["2026-12-12"])
# 7) Monate ohne Tage, Uhrzeit in derselben Zeile, zwei Bahnen in einer Zeile
r = L.lesen(["Von April bis Oktober ist der Fahrbetrieb von 8:30 bis 22:00 Uhr"], [bahn("a", "Almbahn")], "https://almbahn.de/oeffnungszeiten")
pruefe("Monate", [(f.get("jaehrlich"), f["zeiten"].get("erste")) for fs in r.values() for f in fs if f["art"] == "periode"], [(True, "08:30")])
r = L.lesen(["Betriebszeiten:", "28.03 - 06.04.2026 08:30 - 16:00 Uhr"], [bahn("a", "Almbahn")], "https://almbahn.de/preise.php")
pruefe("Uhrzeit gleiche Zeile", [(f["von"], f["zeiten"].get("betrieb_bis")) for fs in r.values() for f in fs if f["art"] == "periode"], [("2026-03-28", "16:00")])
r = L.lesen(["Betriebszeiten Winter", "Ifenbahn 11.12.2026-04.04.2027 & Gaisbühllift 04.12.2026-04.04.2027"], [bahn("i", "Ifenbahn"), bahn("g", "Gaisbühllift")], "https://x.de/oeffnungszeiten")
pruefe("zwei Bahnen eine Zeile", ([f["von"] for f in r.get("i", []) if f["art"] == "periode"], [f["von"] for f in r.get("g", []) if f["art"] == "periode"]), (["2026-12-11"], ["2026-12-04"]))

# 8) Ungefähre Monate, Monatsliste mit Uhrzeit davor, Menüpunkt vor „Öffnungszeiten“, „Sektion II“ im Kartennamen
r = L.lesen(["Betriebszeiten", "Die Bahn ist von Mitte Dezember bis Ende März täglich in Betrieb, 8:30 bis 16:00 Uhr"], [bahn("a", "Almbahn")], "https://almbahn.de/")
pruefe("ungefähre Monate", [(f["von"][5:], f["bis"][5:], f.get("ca_von"), f.get("ca_bis")) for fs in r.values() for f in fs if f["art"] == "periode"], [("12-15", "03-31", True, True)])
r = L.lesen(["Musterbahn", "Sommer 2026", "Täglich vom 14.05. – 08.11.2026", "Fahrbetrieb von 8.30 Uhr bis 16.30 Uhr. Im Juli, August und September von 8.00 Uhr bis 17.30 Uhr."], [bahn("m", "Musterbahn")], "https://x.at/betriebszeiten/")
pruefe("Monatsliste", sorted((f["von"][5:], f["bis"][5:], f["zeiten"].get("erste"), f["zeiten"].get("betrieb_bis")) for f in r.get("m", []) if f["art"] == "periode"),
       [("05-14", "11-08", "08:30", "16:30"), ("07-01", "09-30", "08:00", "17:30")])
r = L.lesen(["Pool Tickets", "Adventure Golf", "AI", "Öffnungszeiten", "Die Seilbahn fährt während der Wintersaison, d.h. von November bis April, täglich von", "8:30 Uhr bis 18:00 Uhr jede Viertelstunde."], [], "https://muster.de/offnungszeiten.html", "Öffnungszeiten")
pruefe("Menüpunkt vor Öffnungszeiten", [(f["von"][5:], f["zeiten"].get("betrieb_bis")) for fs in r.values() for f in fs if f["art"] == "periode"], [("11-01", "18:00")])
r = L.lesen(["Öffnungszeiten Wintersaison 2026/27", "Musterhornbahn", "Wintersaison", "12.12.2026 - 02.05.2027"], [bahn("s2", "Musterhornbahn Sektion II")], "https://x.de/oeffnungszeiten/")
pruefe("Sektion im Namen", [f["von"] for f in r.get("s2", []) if f["art"] == "periode"], ["2026-12-12"])

# 9) Menüpunkt „Geschichte der Bahn“, Zeitraum ohne Jahr der schon vorbei ist, „bis voraussichtlich“, Höhle
r = L.lesen(["Geschichte der Bahn", "Betriebszeiten", "1. Mai – 30. September", "8.30 – 17.30 Uhr"], [bahn("u", "Untersbergbahn")], "https://www.untersbergbahn.at/infos/betriebszeiten/", "Betriebszeiten - Untersbergbahn")
f9 = [f for f in r.get("u", []) if f["art"] == "periode"]
pruefe("Menüpunkt Geschichte, ohne Jahr", [(f["von"][5:], f["bis"][5:], f["von"][:4] == f["bis"][:4], f["bis"] >= L.HEUTE.isoformat()) for f in f9], [("05-01", "09-30", True, True)])
r = L.lesen(["Von Montag, 02. November 2026 bis voraussichtlich Sonntag, 13. Dezember 2026 findet unsere Herbst-Revision statt. Während diesem Zeitraum ist die Untersbergbahn geschlossen."], [bahn("u", "Untersbergbahn")], "https://x.at/betriebszeiten/")
pruefe("bis voraussichtlich", [(f["von"], f["bis"]) for f in r.get("u", []) if f["art"] == "revision"], [("2026-11-02", "2026-12-13")])
r = L.lesen(["Eishöhle", "Geöffnet: Mitte Mai bis Mitte Oktober"], [bahn("u", "Untersbergbahn")], "https://x.at/erlebnisse/eishoehle/")
pruefe("Höhle ist keine Bahn", [f for fs in r.values() for f in fs if f["art"] == "periode"], [])

# 10) Öffnungszeiten der Verkaufsstellen (Kassa) sind keine Betriebszeiten
r = L.lesen(["Öffnungszeiten unserer Verkaufsstellen", "Tiroler Zugspitzbahn", "Während der Betriebszeiten täglich von 8.30 bis 16.00 Uhr;", "vom 9.11.-15.11.2026 von 9.00 bis 14.00 Uhr", "Preise"], [bahn("z", "Tiroler Zugspitzbahn")], "https://x.at/preise-zeiten/snow-card/")
pruefe("Kassa keine Bahnzeit", [f["art"] for fs in r.values() for f in fs], [])

if FEHLER:
    print("Selbsttest Bergbahn-Leser: FEHLER"); print("\n".join("- " + f for f in FEHLER)); sys.exit(1)
print("Selbsttest Bergbahn-Leser: alle Prüfungen bestanden")
