#!/usr/bin/env python3
"""Claude-Hilfe: Einträge (JSON-Liste) als EIN SQL-Update für gb_parkplaetze (ohne Semikolon, für den Supabase-Connector).
Eintrag: {"ids":[..], "g":"ja|nein|unbekannt", "t":[[dauer,eur],..], "m":höchst, "z":zeiten, "za":[..], "e":ermäßigung, "wm":wohnmobile,
          "el":true/false, "h":hinweis, "q":quelle, "u":url, "qd":"JJJJ-MM-TT", "k":"bestaetigt|eine_quelle|unklar", "w":widerspruch}
Aufruf: park_sql.py eintraege.json [pruefdatum]"""
import json, sys
E = json.load(open(sys.argv[1])); P = sys.argv[2] if len(sys.argv) > 2 else "2026-10-19"
j = json.dumps(E, ensure_ascii=False, separators=(",", ":")).replace("'", "''").replace(";", ",")
print(f"""with r as (select (jsonb_array_elements_text(x->'ids'))::bigint id, x from jsonb_array_elements('{j}'::jsonb) x)
update gb_parkplaetze p set gebuehr=x->>'g',
 tarife=case when x ? 't' or x ? 'm' then jsonb_strip_nulls(jsonb_build_object('posten',(select coalesce(jsonb_agg(jsonb_build_object('dauer',t->>0,'eur',(t->>1)::numeric)),'[]'::jsonb) from jsonb_array_elements(coalesce(x->'t','[]'::jsonb)) t),'hoechst',x->'m')) end,
 gebuehr_zeiten=x->>'z', zahlungsarten=(select array_agg(z) from jsonb_array_elements_text(coalesce(x->'za','[]'::jsonb)) z), ermaessigungen=x->>'e', wohnmobile=x->>'wm',
 ladestation=coalesce((x->>'el')::boolean,p.ladestation), hinweise=x->>'h', quelle=x->>'q', quelle_url=x->>'u', quelle_datum=(x->>'qd')::date, pruef_datum='{P}', sicherheit=x->>'k',
 daten=p.daten||jsonb_strip_nulls(jsonb_build_object('recherche','fertig','pruef_claude','{P}','widerspruch',x->>'w')), geaendert=now()
from r where p.id=r.id returning p.id""")
