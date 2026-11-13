# Gipfelbuch – KI-Einordnung von Fotos mit CLIP/SigLIP (frei, läuft auf dem GitHub-Rechner, nichts kostet).
# Jede Klasse hat mehrere englische Beschreibungen; das Modell vergleicht das Bild mit allen und nimmt die beste Klasse.
# Klassen:
#   gipfelkreuz – Gipfelkreuz bzw. Gipfelzeichen aus der Nähe  (v2060: erstes Bild beim Gipfel)
#   gipfel   – der Berg selbst, aus einiger Entfernung gesehen (zweites Bild beim Gipfel; das Motiv steht NICHT dort, wo fotografiert wurde)
#   aussicht – Aussicht/Panorama, Landschaft                   (Gipfel und Hütten, danach)
#   see      – See, Bergsee, Stausee, Weiher                   (v2060: für Seen; beim Gipfel/bei der Hütte zählt er wie Aussicht)
#   nah      – Nahaufnahmen von Pilzen, Insekten, Pflanzen, Steinen (v2060, wird aussortiert)
#   huette   – Hütte, Alpe, Berggasthaus von außen              (Hütten)
#   weg      – Weg, Pfad, Forststraße, Straße in Gehrichtung     (Trailview)
#   blume, gesicht (auch Personengruppen, v1965), fuesse, essen, schrift, innen (Kirchen- und andere Innenräume, v1986) – werden aussortiert (ok = False)
#   wegweiser – Wegweiser am Weg (erlaubt, zählt für Trailview wie weg)
#   tier, ort – bleiben erlaubt, aber nicht bei Gipfel/Hütte/Trailview
import os, io

KLASSEN = {
    "gipfelkreuz": [
        "a large summit cross standing on a mountain top, photographed from nearby",
        "a wooden or metal cross on a summit against the sky",
        "a summit cross with a summit register box",
        "a cairn or summit marker on top of a mountain, close up",
    ],
    "gipfel": [
        "a photo of a single mountain peak seen from a distance",
        "a rocky mountain summit seen from below",
        "a pointed mountain rising above the surrounding landscape",
        "a snowy mountain in the alps",
        "a steep mountain face with cliffs",
        "a grassy mountain ridge leading up to a summit",
    ],
    "aussicht": [
        "a panoramic view over valleys and mountain ranges",
        "a wide alpine landscape seen from high up",
        "view from a mountain down into a valley with villages",
        "many mountain ranges fading into the distance",
        "an alpine meadow with mountains in the background",
        "clouds and fog below mountain tops",
    ],
    "see": [
        "a mountain lake surrounded by peaks",
        "a lake with clear water and its shore",
        "an alpine lake reflecting the mountains",
        "a reservoir lake in the mountains",
        "a small pond or tarn in an alpine meadow",
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
    "nah": [
        "a close-up photo of a mushroom",
        "a macro photo of an insect, butterfly or beetle",
        "a close-up of a plant, leaf, moss or lichen",
        "a close-up of a rock, stone or tree bark",
        "a close-up of an object lying on the ground",
    ],
    "gesicht": [
        "a selfie",
        "a close-up portrait of a person's face",
        "people posing close to the camera",
        "a smiling person in the foreground",
        "a group of people posing together for a photo",
        "a group photo of hikers standing next to each other",
        "several people standing close together and smiling at the camera",
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
        "a ski lift or cable car station",
    ],
    "innen": [
        "the interior of a church with pews and an altar",
        "inside a chapel or cathedral with a vaulted ceiling",
        "a church organ and stained glass windows inside a church",
        "an indoor room with a ceiling, seen from the inside",
        "inside a large hall or museum",
    ],
}
AUSSORTIEREN = {"blume", "nah", "gesicht", "fuesse", "essen", "schrift", "innen"}
VERSION = 2   # Stand der Gruppen (v2060: gipfelkreuz, see, nah); steht in werte.v, ältere Mitglieder-Fotos werden einmal neu geprüft

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
            werte = dict(list(w.items())[:4]); werte["v"] = VERSION
            out[i] = {"klasse": kl, "ok": kl not in AUSSORTIEREN, "werte": werte}
        return out
