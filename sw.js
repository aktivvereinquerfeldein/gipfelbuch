/* Gipfelbuch – Zwischenspeicher am Gerät.
   App: immer zuerst aus dem Netz (Updates kommen sofort), ohne Netz aus dem Speicher.
   Karten-Kacheln, Höhenmodell, Schriften: einmal geladen → aus dem Speicher.
   Wikipedia/Fotos-Suche/Ortssuche: sofort aus dem Speicher, im Hintergrund aktualisieren. */
const SHELL="gb-shell-v1", TILES="gb-tiles-v1", API="gb-api-v1";
const LIMIT={[TILES]:6000,[API]:1500,[SHELL]:60};
self.addEventListener("install",e=>{ self.skipWaiting(); e.waitUntil(caches.open(SHELL).then(c=>c.addAll(["./","./manifest.webmanifest","./icon-192.png"])).catch(()=>{})); });
self.addEventListener("activate",e=>{ e.waitUntil((async()=>{ const keep=[SHELL,TILES,API]; for(const k of await caches.keys()) if(!keep.includes(k)) await caches.delete(k); await self.clients.claim(); })()); });
const trimT={};
function trimSoon(name){ clearTimeout(trimT[name]); trimT[name]=setTimeout(async()=>{ const c=await caches.open(name); const keys=await c.keys(); const max=LIMIT[name]||1000; for(const k of keys.slice(0,Math.max(0,keys.length-max))) await c.delete(k); },8000); }
async function cacheFirst(req,name){ const c=await caches.open(name); const hit=await c.match(req); if(hit) return hit; const r=await fetch(req); if(r.ok){ c.put(req,r.clone()).catch(()=>{}); trimSoon(name); } return r; }
async function staleWhileRevalidate(req,name){ const c=await caches.open(name); const hit=await c.match(req); const net=fetch(req).then(r=>{ if(r.ok){ c.put(req,r.clone()).catch(()=>{}); trimSoon(name); } return r; }).catch(()=>hit||Response.error()); return hit||net; }
async function networkFirst(req,name){ const c=await caches.open(name); try{ const r=await fetch(req); if(r.ok) c.put(req,r.clone()).catch(()=>{}); return r; }catch(e){ const hit=await c.match(req,{ignoreSearch:true}); if(hit) return hit; throw e; } }
self.addEventListener("fetch",e=>{
  const req=e.request; if(req.method!=="GET") return;
  const u=new URL(req.url), hp=u.hostname+u.pathname;
  if(/\/dem\/\d+\/\d+\/\d+\.png$/.test(u.pathname)){ e.respondWith(cacheFirst(req,TILES)); return; } // Höhenmodell vom eigenen Server
  if(/\/dem\/\d+\/\d+\/\d+\.png$/.test(u.pathname)){ e.respondWith(cacheFirst(req,TILES)); return; } // Höhenmodell vom eigenen Server
  if(u.origin===location.origin&&/^\/(api\/|status|overpass)/.test(u.pathname)) return; // Vermittler immer live
  if(u.origin===location.origin){ e.respondWith(req.mode==="navigate"||/\/$|\.html$/.test(u.pathname)?networkFirst(req,SHELL):staleWhileRevalidate(req,SHELL)); return; }
  if(/\.(pbf|mvt|png|jpe?g|webp)$/i.test(u.pathname)&&/openfreemap\.org|opentopomap\.org|basemap\.at|elevation-tiles-prod|arcgisonline\.com|mapillary\.com|fonts\.openmaptiles\.org/.test(hp)){ e.respondWith(cacheFirst(req,TILES)); return; }
  if(/fonts\.openmaptiles\.org|unpkg\.com|cdn\.jsdelivr\.net|fonts\.gstatic\.com/.test(u.hostname)){ e.respondWith(cacheFirst(req,SHELL)); return; }
  if(/openfreemap\.org|fonts\.googleapis\.com/.test(u.hostname)){ e.respondWith(staleWhileRevalidate(req,SHELL)); return; }
  if(/wikipedia\.org|wikidata\.org|commons\.wikimedia\.org|photon\.komoot\.io|api\.panoramax\.xyz/.test(u.hostname)){ e.respondWith(staleWhileRevalidate(req,API)); return; }
});
