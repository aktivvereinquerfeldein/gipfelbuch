import os, sys, json, re, concurrent.futures as cf
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sammler
urls = [u for u in open(sys.argv[1]).read().split() if "/en/" not in u]
out = {}
def f(u):
    raw, err = sammler.holen(u)
    if err or not raw: return u, None
    z, _ = sammler.text_haupt(raw)
    return u, "\n".join(z or [])
with cf.ThreadPoolExecutor(4) as ex:
    for i, (u, t) in enumerate(ex.map(f, urls)):
        if t: out[u] = t
        if i % 300 == 0: print(i, len(out), flush=True)
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False)
print("fertig", len(out))
