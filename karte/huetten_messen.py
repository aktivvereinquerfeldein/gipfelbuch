#!/usr/bin/env python3
"""Misst maschinell gelesene Hütten-Werte (auto.json aus huetten_dach.py sammeln) gegen die von Claude geprüften Einträge
(recherche=fertig). Verglichen werden Verpflegung, Monat von Saisonbeginn/-ende (Sommer/Winter) und Ruhetage.
Aufruf: huetten_messen.py <alle.json> <bericht.md> <auto1.json> [auto2.json ...]"""
import json, re, sys, collections, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ki_leser

MON = {"jän": 1, "jan": 1, "feb": 2, "mär": 3, "mrz": 3, "apr": 4, "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12}
WT = {"montag": "mo", "dienstag": "di", "mittwoch": "mi", "donnerstag": "do", "freitag": "fr", "samstag": "sa", "sonntag": "so"}


def monate(t):
    """(Beginn, Ende) als Monate aus einem Saisontext wie „Mitte Mai–Anfang Oktober“ oder „15.06.–20.09.2026“"""
    if not t: return None
    t = re.split(r"\(", t.lower())[0]; f = []   # Klammern („Pause 09.07.–22.07.“) zählen nicht
    for m in re.finditer(r"(\d{1,2})\.\s?(\d{1,2})\.|\b(jän|jan|feb|mär|mrz|apr|mai|jun|jul|aug|sep|okt|nov|dez)", t):
        f.append(int(m.group(2)) if m.group(2) else MON[m.group(3)])
    return (f[0], f[-1]) if len(f) >= 2 else None


def ruhe(r):
    o = r.get("oeffnung") or {}
    if o.get("ruhetage"): return set(o["ruhetage"])
    t = (r.get("ruhetage") or "").lower()
    if not t: return None
    if re.search(r"kein", t): return set()
    s = {v for k, v in WT.items() if k in t}
    return s if s and not re.search(r"außer|ausser|ferien|juli|august|winter|sommer|nur", t) else None


if __name__ == "__main__":
    rows = {(r["osm_typ"], r["osm_id"]): r for r in json.load(open(sys.argv[1]))}
    c = collections.Counter(); fehler = []
    for p in sys.argv[3:]:
        for e in json.load(open(p)):
            if e.get("zurueck") or "quellen_jetzt" in e: continue
            r = rows.get((e["t"], e["id"]))
            if not r or (r.get("daten") or {}).get("recherche") != "fertig": continue
            n = r["name"]
            if e.get("art_lesen") == "ki": e = ki_leser.nachpruefen(e, n)
            v, s = e.get("verpflegung"), r.get("verpflegung")
            if v not in (None, "unbekannt") and s not in (None, "unbekannt"):
                if v == s or (s == "ja" and v == "nur_getraenke"): c["verpflegung_richtig"] += 1
                else: c["verpflegung_FALSCH"] += 1; fehler.append(f"- {n}: Verpflegung {v}, geprüft {s} ({e.get('url')})")
            for art in ("sommer", "winter"):
                a, b = monate(e.get("saison_" + art)), monate(r.get("saison_" + art))
                if a and b:
                    if abs(a[0] - b[0]) <= 0 and abs(a[1] - b[1]) <= 0: c[f"{art}_richtig"] += 1
                    elif abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1: c[f"{art}_fast (±1 Monat)"] += 1
                    else: c[f"{art}_FALSCH"] += 1; fehler.append(f"- {n}: {art} {e.get('saison_' + art)}, geprüft {r.get('saison_' + art)} ({e.get('url')})")
                elif a and not b: c[f"{art}_neu_ohne_vergleich"] += 1
            x, y = ruhe({"oeffnung": e.get("oeffnung"), "ruhetage": e.get("ruhetage")}), ruhe(r)
            if x is not None and x and y is not None:
                if x == y: c["ruhetage_richtig"] += 1
                else: c["ruhetage_FALSCH"] += 1; fehler.append(f"- {n}: Ruhetage {e.get('ruhetage')}, geprüft {r.get('ruhetage')} ({e.get('url')})")
    with open(sys.argv[2], "w", encoding="utf-8") as fo:
        fo.write("# Messung gegen geprüfte Hütten\n\n")
        for k in sorted(c): fo.write(f"- {k}: {c[k]}\n")
        fo.write("\n## Abweichungen\n\n" + ("\n".join(fehler) or "keine") + "\n")
    print(dict(c)); print("\n".join(fehler[:40]))
