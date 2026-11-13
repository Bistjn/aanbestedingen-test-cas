"""
Matchscores (0-100%) per zoekprofiel.

Twee soorten profielen (map profielen/):
  - soort "inhoud":        past de opdracht inhoudelijk bij een bedrijf? (bv. IQVIA)
  - soort "opdrachtgever": wordt de opdracht uitgeschreven door een bepaald type
                            organisatie? (bv. zorginstellingen)

Profielen van soort "inhoud" krijgen twee scores:
  - directe match: het bedrijf kan zelf inschrijven
  - klant (optioneel, sleutel "klant"): mogelijks interessant voor de klanten van het bedrijf
    (bv. levering van geneesmiddelen is geen opdracht voor IQVIA zelf, maar wel voor
    farma- en medtechbedrijven die IQVIA bedient)
Elk onderdeel = 60% betekenis + 25% trefwoorden + 15% CPV
  * Betekenis: een meertalig AI-taalmodel (intfloat/multilingual-e5-small) vergelijkt
    titel + volledige omschrijving met de beschrijvingen ("facetten") van het profiel.
    Dat werkt over talen heen (NL/FR/EN), met andere woorden en ondanks tikfouten,
    en hangt niet af van de CPV-code.
    De ruwe gelijkenis wordt omgezet naar 0-1 ten opzichte van de mediaan van alle
    aanbestedingen (de "typische" niet-passende opdracht). Lijkt een opdracht meer
    op een "valse vriend" (bv. levering van geneesmiddelen) dan op het profiel, dan
    wordt het betekenisdeel gehalveerd.
  * Trefwoorden: vaste vaktermen per thema (titel telt zwaarder dan omschrijving).
  * CPV: weegt bewust beperkt mee, omdat CPV-codes vaak verkeerd gekozen zijn.

Score voor "opdrachtgever" = herkenning van de naam van de opdrachtgever.

Als het taalmodel niet geladen kan worden, werkt alles verder zonder betekenisdeel
(dan tellen alleen trefwoorden en CPV mee) en staat dat in het logboek.
"""

import glob
import hashlib
import json
import logging
import os
import re
import statistics

log = logging.getLogger("matching")

MODEL_NAAM = os.environ.get("MATCH_MODEL", "intfloat/multilingual-e5-small")
DREMPEL = 40           # vanaf deze score toont de pagina een opdracht
SPREIDING = 0.10       # gelijkenis boven de mediaan die als "volledig passend" telt
GEWICHT_BETEKENIS, GEWICHT_TREFWOORD, GEWICHT_CPV = 0.60, 0.25, 0.15


# ---------------------------------------------------------------------------
# Profielen
# ---------------------------------------------------------------------------
def lees_profielen(map_="profielen"):
    profielen = []
    for pad in sorted(glob.glob(os.path.join(map_, "*.json"))):
        try:
            with open(pad, encoding="utf-8") as f:
                p = json.load(f)
            if p.get("id") and p.get("soort") in ("inhoud", "opdrachtgever"):
                # Versie: verandert het profiel, dan wordt alles opnieuw berekend
                p["_versie"] = hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]
                profielen.append(p)
            else:
                log.warning("Profiel %s overgeslagen: 'id' of geldige 'soort' ontbreekt.", pad)
        except (OSError, ValueError) as fout:
            log.error("Profiel %s kon niet gelezen worden: %s", pad, fout)
    return profielen


# ---------------------------------------------------------------------------
# Tekst-hulpfuncties
# ---------------------------------------------------------------------------
def normaliseer(tekst):
    tekst = (tekst or "").lower()
    tekst = re.sub(r"[’'`´]", "'", tekst)
    return " " + re.sub(r"\s+", " ", tekst) + " "


def bevat(tekst, term):
    """Woordgrens aan het begin; aan het einde mag een woord doorlopen (dakwerk -> dakwerken),
    behalve bij heel korte termen (az, uz, chu), die exact moeten zijn."""
    term = re.escape(term.lower())
    staart = r"\b" if len(term) <= 4 else ""
    return re.search(r"(?<![a-z0-9à-ÿ])" + term + staart, tekst) is not None


def aanbestedingstekst(a):
    return f"{a.get('titel', '')}. {a.get('omschrijving', '')}"[:2000]


# ---------------------------------------------------------------------------
# Taalmodel (betekenis)
# ---------------------------------------------------------------------------
_model = None


def laad_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            log.info("Taalmodel laden: %s", MODEL_NAAM)
            _model = SentenceTransformer(MODEL_NAAM, device="cpu")
        except Exception as fout:  # ontbrekend pakket, geen internet, ...
            log.warning("Taalmodel niet beschikbaar (%s). Matching zonder betekenisdeel.", fout)
            _model = False
    return _model or None


def vectoren(model, teksten, soort):
    # e5-modellen verwachten 'query: ' voor de vraag en 'passage: ' voor de documenten
    voorvoegsel = "query: " if soort == "query" else "passage: "
    return model.encode([voorvoegsel + t for t in teksten], batch_size=32,
                        normalize_embeddings=True, show_progress_bar=False)


def facet_teksten(facetten):
    teksten, labels = [], []
    for f in facetten or []:
        for t in f.get("teksten", []):
            teksten.append(t)
            labels.append(f.get("label", ""))
    return teksten, labels


def betekenis_berekenen(aanbestedingen, sleutel, facetten, negatief):
    """Bewaar per aanbesteding de ruwe gelijkenis: [beste facet, beste negatief voorbeeld, label]."""
    nieuw = [a for a in aanbestedingen if sleutel not in a.get("_sim", {})]
    if not nieuw:
        return True
    model = laad_model()
    if model is None:
        return False
    teksten, labels = facet_teksten(facetten)
    q = vectoren(model, teksten, "query")
    v = vectoren(model, negatief, "query") if negatief else None
    log.info("[%s] Betekenis berekenen voor %s nieuwe aanbestedingen", sleutel, len(nieuw))
    for start in range(0, len(nieuw), 256):
        blok = nieuw[start:start + 256]
        d = vectoren(model, [aanbestedingstekst(a) for a in blok], "passage")
        sim_pos = d @ q.T
        sim_neg = d @ v.T if v is not None else None
        for i, a in enumerate(blok):
            beste = int(sim_pos[i].argmax())
            a.setdefault("_sim", {})[sleutel] = [
                round(float(sim_pos[i][beste]), 4),
                round(float(sim_neg[i].max()), 4) if sim_neg is not None else 0.0,
                labels[beste],
            ]
    return True


def onderdeel_scoren(aanbestedingen, sleutel, facetten, negatief, trefwoorden, cpv_groepen):
    """Score 0-100 voor één onderdeel (directe match of klant). Geeft {id: (score, redenen)}."""
    met_betekenis = betekenis_berekenen(aanbestedingen, sleutel, facetten, negatief)
    mediaan = None
    if met_betekenis:
        alle = [a["_sim"][sleutel][0] for a in aanbestedingen if sleutel in a.get("_sim", {})]
        mediaan = statistics.median(alle) if alle else None
    gewichten = (GEWICHT_BETEKENIS, GEWICHT_TREFWOORD, GEWICHT_CPV) if mediaan is not None \
        else (0.0, 0.65, 0.35)  # zonder taalmodel: alleen trefwoorden en CPV

    resultaat = {}
    for a in aanbestedingen:
        redenen = []
        # 1. Betekenis (AI-taalmodel)
        b = 0.0
        if mediaan is not None:
            pos, neg, label = a["_sim"][sleutel]
            b = max(0.0, min(1.0, (pos - mediaan) / SPREIDING))
            if neg > pos:
                b *= 0.5   # lijkt meer op iets anders (valse vriend of het andere onderdeel)
            if b >= 0.5:
                redenen.append(f"inhoud: {label}")
        # 2. Vaktermen (titel telt zwaarder dan omschrijving)
        titel, tekst = normaliseer(a.get("titel")), normaliseer(a.get("omschrijving"))
        t_score, gevonden = 0.0, []
        for groep in trefwoorden or []:
            for w in groep.get("woorden", []):
                if bevat(titel, w):
                    t_score += 1.0 * groep.get("gewicht", 1)
                    gevonden.append(w)
                    break
                if bevat(tekst, w):
                    t_score += 0.5 * groep.get("gewicht", 1)
                    gevonden.append(w)
                    break
        t = min(1.0, t_score)
        if gevonden:
            redenen.append("termen: " + ", ".join(gevonden[:4]))
        # 3. CPV (beperkt gewicht: CPV-codes zijn vaak verkeerd gekozen)
        treffer = next((g for g in cpv_groepen or [] for c in a.get("cpv", []) if str(c).startswith(g)), None)
        c = 1.0 if treffer else 0.0
        if treffer:
            redenen.append(f"CPV {treffer}…")
        totaal = round(100 * (gewichten[0] * b + gewichten[1] * t + gewichten[2] * c))
        resultaat[a["id"]] = (int(totaal), redenen)
    return resultaat


def score_inhoud(aanbestedingen, profiel):
    versie = profiel["_versie"]
    klant = profiel.get("klant") or {}
    valse = profiel.get("valse_vrienden") or []
    direct_teksten, _ = facet_teksten(profiel.get("facetten"))
    klant_teksten, _ = facet_teksten(klant.get("facetten"))

    # Directe match: wat lijkt op een klantopdracht of valse vriend telt niet als direct
    direct = onderdeel_scoren(aanbestedingen, f"{profiel['id']}:direct:{versie}",
                              profiel.get("facetten"), valse + klant_teksten,
                              profiel.get("trefwoorden"), profiel.get("cpv_groepen"))
    # Klantopdracht: wat lijkt op een directe match of valse vriend telt niet als klant
    voor_klant = onderdeel_scoren(aanbestedingen, f"{profiel['id']}:klant:{versie}",
                                  klant.get("facetten"), valse + direct_teksten,
                                  klant.get("trefwoorden"), klant.get("cpv_groepen")) if klant else {}

    for a in aanbestedingen:
        d_score, d_redenen = direct[a["id"]]
        k_score, k_redenen = voor_klant.get(a["id"], (0, []))
        if d_score >= DREMPEL:
            soort = "direct"
        elif k_score >= DREMPEL:
            soort = "klant"
        else:
            soort = ""
        a.setdefault("matches", {})[profiel["id"]] = {
            "score": d_score, "redenen": d_redenen,
            "klant_score": k_score, "klant_redenen": k_redenen,
            "soort": soort,
        }


def score_opdrachtgever(aanbestedingen, profiel):
    for a in aanbestedingen:
        naam = normaliseer(a.get("opdrachtgever"))
        zeker = [w for w in profiel.get("zeker", []) if bevat(naam, w)]
        mogelijk = [w for w in profiel.get("mogelijk", []) if bevat(naam, w)]
        uitzondering = [w for w in profiel.get("uitzonderingen", []) if bevat(naam, w)]
        if uitzondering:
            score, redenen = 15, [f"opdrachtgever is overheid/beleid ({uitzondering[0]})"]
        elif zeker:
            score, redenen = min(100, 85 + 5 * (len(zeker) - 1)), [f"opdrachtgever: {', '.join(zeker[:3])}"]
        elif mogelijk:
            score, redenen = 50, [f"opdrachtgever mogelijk zorg ({', '.join(mogelijk[:2])})"]
        else:
            score, redenen = 0, []
        a.setdefault("matches", {})[profiel["id"]] = {"score": score, "redenen": redenen,
                                                      "soort": "direct" if score >= DREMPEL else ""}


def bereken(aanbestedingen, profielen):
    """Voegt a['matches'][profiel_id] = {score, redenen} toe aan elke aanbesteding."""
    for p in profielen:
        try:
            if p["soort"] == "inhoud":
                score_inhoud(aanbestedingen, p)
            else:
                score_opdrachtgever(aanbestedingen, p)
            m = [a["matches"][p["id"]] for a in aanbestedingen]
            direct = sum(1 for x in m if x.get("soort", "direct" if x["score"] >= DREMPEL else "") == "direct")
            klant = sum(1 for x in m if x.get("soort") == "klant")
            log.info("[%s] van %s aanbestedingen: %s directe match (≥%s%%)%s", p["id"], len(m), direct, DREMPEL,
                     f", {klant} mogelijks interessant voor klant" if p.get("klant") else "")
        except Exception:
            log.exception("Matching voor profiel %s mislukt; dit profiel wordt overgeslagen.", p.get("id"))
    for p in profielen:
        rapport(aanbestedingen, p)
    # Oude berekeningen van gewijzigde profielen opruimen
    geldig = {p["_versie"] for p in profielen}
    for a in aanbestedingen:
        if "_sim" in a:
            a["_sim"] = {k: v for k, v in a["_sim"].items() if k.rsplit(":", 1)[-1] in geldig}


def rapport(aanbestedingen, profiel, n=12):
    """Schrijf voorbeelden naar het logboek, om de kwaliteit van de matching te controleren."""
    pid = profiel["id"]
    m = [(a, a.get("matches", {}).get(pid)) for a in aanbestedingen]
    m = [(a, x) for a, x in m if x]
    if not m:
        return
    verdeling = [0] * 5
    for _, x in m:
        verdeling[min(4, max(x["score"], x.get("klant_score", 0)) // 20)] += 1
    log.info("[%s] verdeling hoogste score: 0-19%%: %s | 20-39%%: %s | 40-59%%: %s | 60-79%%: %s | 80-100%%: %s",
             pid, *verdeling)

    def regel(a, score, redenen):
        return f"  {score:>3}% | {a.get('titel', '')[:70]} | {a.get('opdrachtgever', '')[:35]} | {'; '.join(redenen)[:110]}"

    for soort, veld, red in (("DIRECT", "score", "redenen"), ("KLANT", "klant_score", "klant_redenen")):
        if veld not in m[0][1]:
            continue
        top = sorted(m, key=lambda ax: ax[1][veld], reverse=True)
        log.info("[%s] Beste %s-matches:", pid, soort)
        for a, x in top[:n]:
            log.info(regel(a, x[veld], x.get(red, [])))
        grens = [ax for ax in top if 30 <= ax[1][veld] < 50][:8]
        if grens:
            log.info("[%s] Twijfelgevallen %s (30-49%%):", pid, soort)
            for a, x in grens:
                log.info(regel(a, x[veld], x.get(red, [])))
