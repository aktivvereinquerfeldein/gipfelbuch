import os, sys, re, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import quellen
urls = quellen.sitemap_urls(sys.argv[1], re.compile("."))
open(sys.argv[2], "w").write("\n".join(urls))
c = collections.Counter(re.sub(r"https?://[^/]+/", "", u).split("/")[0] if u.count("/")>3 else re.sub(r"-[^-]*$","",u.rsplit("/",1)[-1])[:15] for u in urls)
print(len(urls)); print(c.most_common(30))
