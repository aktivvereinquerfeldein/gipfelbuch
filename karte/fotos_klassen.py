# Gipfelbuch – KI-Einordnung von Fotos mit CLIP/SigLIP (frei, läuft auf dem GitHub-Rechner, nichts kostet).
# Jede Klasse hat mehrere englische Beschreibungen; das Modell vergleicht das Bild mit allen und nimmt die beste Klasse.
# Klassen:
#   gipfel   – der Berg selbst, Gipfel, Gipfelkreuz            (für Gipfel-Fotos, erstes Bild)
#   aussicht – Aussicht/Panorama, Bergsee, Landschaft           (Gipfel und Hütten)
#   huette   – Hütte, Alpe, Berggasthaus von außen              (Hütten)
#   weg      – Weg, Pfad, Forststraße, Straße in Gehrichtung     (Trailview)
#   blume, gesicht, fuesse, essen, schrift – werden aussortiert (ok = False)
#   wegweiser – Wegweiser am Weg (erlaubt, zählt für Trailview wie weg)
#   tier, ort – bleiben erlaubt, aber nicht bei Gipfel/Hütte/Trailview
import os, io

KLASSEN = {
    "gipfel": [
        "a photo of a mountain peak",
        "a rocky mountain summit seen from below",
        "a summit cross on top of a mountain",
        "hikers standing next to a summit cross on a mountain top",
        "a snowy mountain in the alps",
        "a steep mountain face with cliffs",
    ],
    "aussicht": [
        "a panoramic view over valleys and mountain ranges",
        "a wide alpine landscape seen from high up",
        "view from a mountain over a lake and valley",
        "a mountain lake surrounded by peaks",
        "an alpine meadow with mountains in the background",
        "clouds and fog below mountain tops",
    ],
    "huette": [
        "a photo of an alpine mountain hut",
        "a wooden alpine chalet on a meadow",
        "a mountain hut building with a terrace",
        "a farmhouse on an alpine pasture",
        "a stone mountain refuge",
    ],
    "weg": [
        "a hiking trail leading through the landscape",
        "a narrow footpath on a mountain slope",
        "a gravel forest road",
        "a path through a forest",
        "a dirt road in the countryside",
        "a road seen from the driver's perspective",
        "a trail marked with red and white paint",
    ],
    "blume": [
        "a close-up photo of a flower",
        "a macro photo of a blossom",
        "close-up of alpine flowers in the grass",
    ],
    "gesicht": [
        "a selfie",
        "a close-up portrait of a person's face",
        "people posing close to the camera",
        "a smiling person in the foreground",
    ],
    "fuesse": [
        "a photo of feet in hiking boots",
        "shoes on the ground seen from above",
        "legs and feet of a person",
    ],
    "essen": [
        "a plate of food on a table",
        "a glass of beer or a drink on a table",
        "cake and coffee",
    ],
    "schrift": [
        "a sign board with text",
        "a map or a document",
        "a screenshot",
    ],
    "wegweiser": [
        "a yellow trail signpost with arrows",
        "a hiking signpost next to a path",
    ],
    "tier": [
        "a close-up photo of an animal",
        "cows grazing on a meadow",
        "a bird or a marmot",
    ],
    "ort": [
        "a village with houses and a church",
        "a town street with buildings",
        "the inside of a building",
        "a ski lift or cable car station",
    ],
}
AUSSORTIEREN = {"blume", "gesicht", "fuesse", "essen", "schrift"}

MODELL = os.environ.get("FOTO_MODELL", "ViT-B-16-SigLIP2")
GEWICHTE = os.environ.get("FOTO_GEWICHTE", "webli")

class Pruefer:
    def __init__(s):
        import torch, open_clip
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        s.torch = torch
        s.model, _, s.prep = open_clip.create_model_and_transforms(MODELL, pretrained=GEWICHTE)
        s.model.eval()
        tok = open_clip.get_tokenizer(MODELL)
        s.namen, texte = [], []
        for k, v in KLASSEN.items():
            for t in v: s.namen.append(k); texte.append(t)
        with torch.no_grad():
            f = s.model.encode_text(tok(texte))
            s.txt = f / f.norm(dim=-1, keepdim=True)

    def pruefe(s, bilder):
        """bilder: Liste von bytes. Ergebnis: Liste von dicts {klasse, ok, werte} oder None, wenn das Bild nicht lesbar ist"""
        from PIL import Image
        torch = s.torch
        ims, idx = [], []
        for i, b in enumerate(bilder):
            try:
                im = Image.open(io.BytesIO(b)).convert("RGB")
                ims.append(s.prep(im)); idx.append(i)
            except Exception:
                pass
        out = [None] * len(bilder)
        if not ims: return out
        with torch.no_grad():
            f = s.model.encode_image(torch.stack(ims))
            f = f / f.norm(dim=-1, keepdim=True)
            sim = (f @ s.txt.T) * 100.0
        for j, i in enumerate(idx):
            row = sim[j]
            # je Klasse den besten Satz nehmen, dann Softmax über die Klassen
            best = {}
            for n, v in zip(s.namen, row.tolist()):
                if n not in best or v > best[n]: best[n] = v
            ks = list(best)
            p = torch.softmax(torch.tensor([best[k] for k in ks]), 0).tolist()
            w = {k: round(v, 3) for k, v in sorted(zip(ks, p), key=lambda x: -x[1])}
            kl = next(iter(w))
            # Aussortieren nur, wenn sicher genug (sonst lieber die zweitbeste erlaubte Klasse)
            if kl in AUSSORTIEREN and w[kl] < 0.45:
                kl = next(k for k in w if k not in AUSSORTIEREN)
            out[i] = {"klasse": kl, "ok": kl not in AUSSORTIEREN, "werte": dict(list(w.items())[:4])}
        return out
