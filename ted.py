"""
TED-publicaties (Tenders Electronic Daily, EU) ophalen voor België en Nederland.

Bron: officiële TED Search API v3 (anoniem, geen sleutel nodig)
  Documentatie: https://docs.ted.europa.eu/api/latest/search.html
  Schema:       https://api.ted.europa.eu/api-v3.yaml
  Endpoint:     POST https://api.ted.europa.eu/v3/notices/search

Gecontroleerd met echte antwoorden op 13-11-2026. Opvallend in de antwoorden:
  - titels, kopernamen en omschrijvingen staan in veel talen tegelijk
    ({"nld": "...", "fra": "...", ...}); soms als tekst, soms als lijst
  - lijsten bevatten dubbele waarden (één per perceel), bv. CPV en NUTS
  - datums hebben een tijdzone-achtervoegsel: "2026-12-07Z", "2026-11-13+01:00"
  - limieten: max. 250 aankondigingen per pagina, max. 15.000 per zoekopdracht

Uitvoer:
  ted_publicaties.csv / ted_publicaties.json / ted.log
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Instellingen (aan te passen via omgevingsvariabelen in de workflow)
# ---------------------------------------------------------------------------
URL = "https://api.ted.europa.eu/v3/notices/search"
LANDEN = os.environ.get("TED_LANDEN", "BEL NLD").split()   # ISO-3 landcodes
DAGEN_TERUG = int(os.environ.get("TED_DAGEN_TERUG", "1"))   # 1 = sinds gisteren
MAX_AANTAL = int(os.environ.get("TED_MAX", "1000"))         # veiligheidslimiet
PAGINA = 250                                                # API-maximum
TIMEOUT = 60
POGINGEN = 3

# Voorkeurstalen voor tekstvelden, in deze volgorde
TALEN = ["nld", "fra", "eng", "deu"]

VELDEN = [
    "publication-number", "notice-title", "buyer-name", "buyer-country", "buyer-city",
    "place-of-performance", "place-of-performance-subdiv-proc",
    "classification-cpv", "main-classification-proc",
    "notice-type", "form-type", "procedure-type", "contract-nature-main-proc",
    "deadline-receipt-tender-date-lot", "deadline-receipt-request-date-lot",
    "publication-date", "links", "document-url-lot",
    "estimated-value-proc", "estimated-value-cur-proc", "description-proc",
]

# Leesbare namen voor aankondigingstypes die in echte antwoorden voorkwamen.
# Onbekende codes worden gewoon als code getoond (niets verzonnen).
FASE_NAMEN = {
    "pin-only": "Vooraankondiging",
    "pmc": "Marktconsultatie",
    "cn-standard": "Aankondiging opdracht",
    "can-standard": "Gegunde opdracht",
    "can-social": "Gegunde opdracht (sociale diensten)",
    "veat": "Vrijwillige transparantie (vooraf)",
}

CSV_BESTAND = "ted_publicaties.csv"
JSON_BESTAND = "ted_publicaties.json"
LOG_BESTAND = "ted.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_BESTAND, mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("ted")


# ---------------------------------------------------------------------------
# Ophalen
# ---------------------------------------------------------------------------
def zoek(query, pagina):
    body = json.dumps({
        "query": query,
        "fields": VELDEN,
        "page": pagina,
        "limit": PAGINA,
        "scope": "ALL",
        "paginationMode": "PAGE_NUMBER",
    }).encode("utf-8")
    verzoek = Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "aanbestedingen-test-cas (github.com/Bistjn/aanbestedingen-test-cas)",
    })
    for poging in range(1, POGINGEN + 1):
        try:
            with urlopen(verzoek, timeout=TIMEOUT) as antwoord:
                return json.load(antwoord)
        except HTTPError as fout:
            detail = fout.read()[:500].decode("utf-8", "replace") if fout.fp else ""
            if fout.code == 400:
                log.error("TED weigert de zoekopdracht (HTTP 400): %s", detail)
                return None
            log.warning("HTTP %s van TED (poging %s/%s) %s", fout.code, poging, POGINGEN, detail)
        except (URLError, TimeoutError) as fout:
            log.warning("Netwerkfout: %s (poging %s/%s)", fout, poging, POGINGEN)
        except ValueError as fout:
            log.warning("Antwoord is geen geldige JSON: %s", fout)
            return None
        if poging < POGINGEN:
            time.sleep(5 * poging)  # TED begrenst anonieme verzoeken: rustig aan
    log.error("Opgegeven na %s pogingen (pagina %s).", POGINGEN, pagina)
    return None


# ---------------------------------------------------------------------------
# Velden veilig lezen
# ---------------------------------------------------------------------------
def als_lijst(waarde):
    if waarde is None:
        return []
    return waarde if isinstance(waarde, list) else [waarde]


def uniek(waarden):
    """Dubbels verwijderen, volgorde behouden."""
    gezien, uit = set(), []
    for w in waarden:
        if w not in gezien and w not in (None, ""):
            gezien.add(w)
            uit.append(w)
    return uit


def tekst_in_taal(waarde):
    """Kies uit {"nld": ..., "fra": ...} de beste taal. Werkt ook met tekst of lijst."""
    if isinstance(waarde, dict):
        for taal in TALEN + ["mul"] + list(waarde.keys()):
            if taal in waarde and waarde[taal]:
                return " / ".join(str(x) for x in uniek(als_lijst(waarde[taal])))
        return ""
    return " / ".join(str(x) for x in uniek(als_lijst(waarde)))


def datum(waarde):
    """'2026-12-07Z' of '2026-11-13+01:00' -> '2026-12-07'. Vroegste bij meerdere."""
    datums = sorted(str(w)[:10] for w in als_lijst(waarde) if w)
    return datums[0] if datums else ""


def link(links, soort, voorkeur=("NLD", "FRA", "ENG", "MUL")):
    opties = links.get(soort) if isinstance(links, dict) else None
    if not isinstance(opties, dict):
        return ""
    for taal in voorkeur:
        if opties.get(taal):
            return opties[taal]
    return next(iter(opties.values()), "")


# ---------------------------------------------------------------------------
# Eén aankondiging omzetten naar een rij
# ---------------------------------------------------------------------------
def verwerk(n):
    nummer = n.get("publication-number")
    if not nummer:
        return None
    fase_code = n.get("notice-type") or ""
    links = n.get("links") or {}
    nuts_alle = uniek(als_lijst(n.get("place-of-performance")))
    nuts_regio = [c for c in nuts_alle if len(c) > 3]  # 'BE211' ja, 'BEL' (land) nee
    docs = uniek(als_lijst(n.get("document-url-lot")))
    waarde = n.get("estimated-value-proc")

    return {
        "Bron": "TED",
        "ID": nummer,
        "Titel": tekst_in_taal(n.get("notice-title")),
        "Opdrachtgever": tekst_in_taal(n.get("buyer-name")),
        "Plaats opdrachtgever": tekst_in_taal(n.get("buyer-city")),
        "Land": " | ".join(uniek(als_lijst(n.get("buyer-country")))),
        "Fase code": fase_code,
        "Fase": FASE_NAMEN.get(fase_code, fase_code),
        "Formulier": n.get("form-type") or "",
        "Type opdracht": n.get("contract-nature-main-proc") or "",
        "Procedure": n.get("procedure-type") or "",
        "Publicatiedatum": datum(n.get("publication-date")),
        "Deadline inschrijving": datum(n.get("deadline-receipt-tender-date-lot")),
        "Deadline aanvraag deelname": datum(n.get("deadline-receipt-request-date-lot")),
        "CPV hoofdcode": " | ".join(uniek(als_lijst(n.get("main-classification-proc")))),
        "CPV-codes": " | ".join(uniek(als_lijst(n.get("classification-cpv")))),
        "NUTS-codes": " | ".join(nuts_regio),
        "Geraamde waarde": f"{waarde} {n.get('estimated-value-cur-proc') or ''}".strip() if waarde else "",
        "Omschrijving": tekst_in_taal(n.get("description-proc")),
        "Documentlinks": " | ".join(docs),
        "TED-pagina": link(links, "html") or f"https://ted.europa.eu/nl/notice/-/detail/{nummer}",
        "Aankondiging PDF": link(links, "pdf"),
    }


# ---------------------------------------------------------------------------
# Hoofdprogramma
# ---------------------------------------------------------------------------
def main():
    vanaf = (date.today() - timedelta(days=DAGEN_TERUG)).strftime("%Y%m%d")
    query = (f"buyer-country IN ({' '.join(LANDEN)}) "
             f"AND publication-date>={vanaf} SORT BY publication-date DESC")
    log.info("Start TED: landen %s, gepubliceerd vanaf %s", ", ".join(LANDEN), vanaf)
    log.info("Zoekopdracht: %s", query)

    ruw, pagina, totaal = [], 1, None
    while len(ruw) < MAX_AANTAL:
        antwoord = zoek(query, pagina)
        if antwoord is None:
            break
        notices = antwoord.get("notices")
        if not isinstance(notices, list):
            log.error("Onverwacht antwoord: geen 'notices'-lijst. Sleutels: %s", list(antwoord))
            break
        if totaal is None:
            totaal = antwoord.get("totalNoticeCount")
            log.info("TED meldt %s aankondigingen", totaal)
        ruw.extend(notices)
        log.info("Pagina %s: %s aankondigingen (samen %s)", pagina, len(notices), len(ruw))
        if len(notices) < PAGINA or (isinstance(totaal, int) and len(ruw) >= totaal):
            break
        pagina += 1
        time.sleep(1)

    if len(ruw) >= MAX_AANTAL:
        log.warning("Veiligheidslimiet van %s bereikt; verhoog TED_MAX indien nodig.", MAX_AANTAL)
        ruw = ruw[:MAX_AANTAL]

    rijen, bronnen = [], []
    for n in ruw:
        try:
            rij = verwerk(n) if isinstance(n, dict) else None
        except Exception:
            log.exception("Onverwachte fout bij aankondiging %s; overgeslagen.",
                          n.get("publication-number") if isinstance(n, dict) else "?")
            rij = None
        if rij:
            rijen.append(rij)
            bronnen.append(n)

    if not rijen:
        if totaal == 0:
            log.warning("Geen nieuwe aankondigingen gevonden (dat kan, bv. in het weekend).")
            return 0
        log.error("Geen enkele aankondiging verwerkt.")
        return 1

    with open(CSV_BESTAND, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rijen[0].keys()))
        writer.writeheader()
        writer.writerows(rijen)
    with open(JSON_BESTAND, "w", encoding="utf-8") as f:
        json.dump([{"samenvatting": r, "bron": b} for r, b in zip(rijen, bronnen)],
                  f, ensure_ascii=False, indent=2)

    per_land, per_fase = {}, {}
    for r in rijen:
        per_land[r["Land"]] = per_land.get(r["Land"], 0) + 1
        per_fase[r["Fase"]] = per_fase.get(r["Fase"], 0) + 1
    log.info("Klaar: %s aankondigingen verwerkt", len(rijen))
    log.info("  per land: %s", per_land)
    log.info("  per fase: %s", per_fase)
    log.info("  met deadline inschrijving: %s | met documentlink: %s | met NUTS-regio: %s",
             sum(1 for r in rijen if r["Deadline inschrijving"]),
             sum(1 for r in rijen if r["Documentlinks"]),
             sum(1 for r in rijen if r["NUTS-codes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
