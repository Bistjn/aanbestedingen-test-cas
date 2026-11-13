"""
TenderNed-publicaties ophalen via de publieke TNS-webservice.

Bron: https://data.overheid.nl/dataset/aankondigingen-van-overheidsopdrachten---tenderned
Let op: TenderNed meldt dat deze webservice zonder aankondiging kan wijzigen.
Daarom gaat dit script ervan uit dat elk veld kan ontbreken of een andere vorm kan hebben.

Gebruikte endpoints (gecontroleerd met echte antwoorden op 13-11-2026):
  GET /v2/publicaties?page=0&size=N          -> lijst (zonder CPV-codes)
  GET /v2/publicaties/{id}                   -> details (met CPV- en NUTS-codes)
  GET /v2/publicaties/{id}/documenten        -> documentenlijst met downloadlinks
  GET /v2/publicaties/{id}/pdf               -> aankondiging als PDF

Uitvoer:
  aanbestedingen_nederland.csv   -> overzicht om te openen in Excel
  aanbestedingen_nederland.json  -> dezelfde gegevens + ruwe API-antwoorden
  tenderned.log                  -> logboek van de run
"""

import csv
import json
import logging
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------
SITE = "https://www.tenderned.nl"
API = f"{SITE}/papi/tenderned-rs-tns/v2"

AANTAL = int(os.environ.get("AANTAL_PUBLICATIES", "10"))  # max 100 per pagina
TIMEOUT = 30          # seconden per verzoek
POGINGEN = 3          # aantal pogingen bij tijdelijke fouten
PAUZE = 0.3           # seconden tussen publicaties (vriendelijk voor de server)

CSV_BESTAND = "aanbestedingen_nederland.csv"
JSON_BESTAND = "aanbestedingen_nederland.json"
LOG_BESTAND = "tenderned.log"

# ---------------------------------------------------------------------------
# Logging: naar het scherm (zichtbaar in GitHub Actions) én naar een bestand
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_BESTAND, mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("tenderned")


# ---------------------------------------------------------------------------
# Hulpfuncties voor het ophalen
# ---------------------------------------------------------------------------
def ophalen_json(url):
    """Haal JSON op. Probeert opnieuw bij tijdelijke fouten. Geeft None bij falen."""
    verzoek = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "aanbestedingen-test-cas (github.com/Bistjn/aanbestedingen-test-cas)",
    })
    for poging in range(1, POGINGEN + 1):
        try:
            with urlopen(verzoek, timeout=TIMEOUT) as antwoord:
                return json.load(antwoord)
        except HTTPError as fout:
            # 404 = bestaat niet; opnieuw proberen heeft dan geen zin.
            if fout.code in (400, 401, 403, 404):
                log.warning("HTTP %s (geen nieuwe poging) bij %s", fout.code, url)
                return None
            log.warning("HTTP %s bij %s (poging %s/%s)", fout.code, url, poging, POGINGEN)
        except (URLError, TimeoutError) as fout:
            log.warning("Netwerkfout bij %s: %s (poging %s/%s)", url, fout, poging, POGINGEN)
        except ValueError as fout:
            log.warning("Antwoord is geen geldige JSON bij %s: %s", url, fout)
            return None
        if poging < POGINGEN:
            time.sleep(2 ** poging)  # 2, 4 seconden wachten
    log.error("Opgegeven na %s pogingen: %s", POGINGEN, url)
    return None


# ---------------------------------------------------------------------------
# Hulpfuncties om velden veilig te lezen
# ---------------------------------------------------------------------------
# TenderNed gebruikt soms tekst ("Rectificatie") en soms een object
# ({"code": "REC", "omschrijving": "Rectificatie"}) voor hetzelfde gegeven.
# Deze functies werken met beide vormen, en met None.

def omschrijving(waarde):
    if isinstance(waarde, dict):
        return str(waarde.get("omschrijving") or waarde.get("code") or "")
    if waarde is None:
        return ""
    return str(waarde)


def code(waarde):
    if isinstance(waarde, dict):
        return str(waarde.get("code") or "")
    return ""


def eerste(*waarden):
    """Geef de eerste waarde die niet leeg is."""
    for w in waarden:
        if w not in (None, "", [], {}):
            return w
    return None


def volledige_url(href):
    if not isinstance(href, str) or not href:
        return ""
    return SITE + href if href.startswith("/") else href


def link_href(obj, *sleutels):
    """Lees bv. obj['links']['pdf']['href'] zonder te crashen."""
    for sleutel in sleutels:
        if not isinstance(obj, dict):
            return ""
        obj = obj.get(sleutel)
    return obj if isinstance(obj, str) else ""


def codes_tekst(lijst, met_hoofd=False):
    """Maak van [{code, omschrijving, isHoofdOpdracht}] een leesbare tekst."""
    if not isinstance(lijst, list):
        return ""
    delen = []
    for item in lijst:
        if isinstance(item, dict):
            tekst = f"{item.get('code', '')} ({item.get('omschrijving', '')})".strip()
            if met_hoofd and item.get("isHoofdOpdracht"):
                tekst += " [hoofd]"
        else:
            tekst = str(item)
        delen.append(tekst)
    return " | ".join(delen)


def trefwoorden(detail):
    woorden = []
    for i in range(1, 6):
        w = detail.get(f"trefwoord{i}")
        if w:
            woorden.append(str(w).strip().strip('"'))
    return " | ".join(woorden)


# ---------------------------------------------------------------------------
# Eén publicatie verwerken
# ---------------------------------------------------------------------------
def verwerk(item):
    pid = item.get("publicatieId")
    if pid in (None, ""):
        log.warning("Publicatie zonder publicatieId overgeslagen.")
        return None
    pid = str(pid)

    detail = ophalen_json(f"{API}/publicaties/{pid}")
    if detail is None:
        log.warning("[%s] Geen details: alleen lijstgegevens beschikbaar (geen CPV).", pid)
        detail = {}

    docs_antwoord = ophalen_json(f"{API}/publicaties/{pid}/documenten") or {}
    documenten = docs_antwoord.get("documenten")
    if not isinstance(documenten, list):
        documenten = []

    doc_regels = []
    for doc in documenten:
        if not isinstance(doc, dict):
            continue
        url = volledige_url(link_href(doc, "links", "download", "href"))
        if url:
            naam = doc.get("documentNaam") or "document"
            soort = code(doc.get("typeDocument"))
            doc_regels.append({
                "naam": naam,
                "type": soort,
                "categorie": omschrijving(doc.get("publicatieCategorie")),
                "grootte_bytes": doc.get("grootte"),
                "url": url,
            })

    cpv = detail.get("cpvCodes")
    rij = {
        "ID": pid,
        "Kenmerk": eerste(detail.get("kenmerk"), item.get("kenmerk")) or "",
        "Titel": eerste(detail.get("aanbestedingNaam"), item.get("aanbestedingNaam")) or "",
        "Opdrachtgever": eerste(detail.get("opdrachtgeverNaam"), item.get("opdrachtgeverNaam")) or "",
        # Lijst: object {code, omschrijving}. Detail: tekst. -> lijst eerst.
        "Fase code": code(item.get("typePublicatie")) or code(detail.get("aankondigingCode")),
        "Fase": omschrijving(eerste(item.get("typePublicatie"), detail.get("aankondigingCode"))),
        "Formulier": omschrijving(eerste(item.get("publicatiecode"), detail.get("publicatieCode"))),
        "Status": omschrijving(item.get("publicatiestatus")),
        "Type opdracht": omschrijving(eerste(item.get("typeOpdracht"), detail.get("typeOpdrachtCode"))),
        "Procedure": omschrijving(eerste(item.get("procedure"), detail.get("procedureCode"))),
        "Europees/Nationaal": omschrijving(detail.get("nationaalOfEuropeesCode"))
            or ("Europees" if item.get("europees") else ("Nationaal" if item.get("europees") is False else "")),
        "Juridisch kader": omschrijving(detail.get("juridischKaderCode")),
        "Publicatiedatum": eerste(item.get("publicatieDatum"), detail.get("publicatieDatum")) or "",
        "Deadline": eerste(detail.get("sluitingsDatum"), item.get("sluitingsDatum")) or "",
        "Start opdracht": detail.get("aanvangOpdrachtDatum") or "",
        "Einde opdracht": detail.get("voltooiingOpdrachtDatum") or "",
        "CPV hoofdcode": next((c.get("code", "") for c in cpv or [] if isinstance(c, dict) and c.get("isHoofdOpdracht")), ""),
        "CPV-codes": codes_tekst(cpv, met_hoofd=True),
        "NUTS-codes": codes_tekst(detail.get("nutsCodes")),
        "Referentienummer": detail.get("referentieNummer") or "",
        "TED-nummer": detail.get("pbNummerTed") or "",
        "Trefwoorden": trefwoorden(detail),
        "Omschrijving": eerste(detail.get("opdrachtBeschrijving"), item.get("opdrachtBeschrijving")) or "",
        "Aantal documenten": len(doc_regels),
        "Documentlinks": " | ".join(d["url"] for d in doc_regels),
        "Alle documenten (zip)": volledige_url(link_href(docs_antwoord, "links", "downloadZip", "href")) if doc_regels else "",
        "Aankondiging PDF": volledige_url(link_href(detail, "links", "pdf", "href")),
        "Extern platform": eerste(detail.get("urlTsenderNaam"), item.get("tsenderNaam")) or "",
        "Extern platform link": eerste(detail.get("urlTsenderWebsite"), item.get("tsenderLink")) or "",
        "TenderNed-pagina": eerste(link_href(item, "link", "href"), detail.get("urlDigitaalSysteem"))
            or f"{SITE}/aankondigingen/overzicht/{pid}",
    }

    # Ontbrekende belangrijke velden melden (geen fout, wel nuttig om te weten)
    ontbrekend = [v for v in ("Deadline", "CPV-codes") if not rij[v]]
    if ontbrekend:
        log.info("[%s] Niet beschikbaar in de bron: %s", pid, ", ".join(ontbrekend))

    log.info("[%s] %s | %s | %s documenten%s",
             pid, rij["Fase"] or "?", rij["Titel"][:60], len(doc_regels),
             f" | extern: {rij['Extern platform']}" if rij["Extern platform"] else "")

    return rij, {"lijst": item, "detail": detail, "documenten": doc_regels}


# ---------------------------------------------------------------------------
# Hoofdprogramma
# ---------------------------------------------------------------------------
def main():
    log.info("Start: %s publicaties ophalen van TenderNed", AANTAL)

    lijst = ophalen_json(f"{API}/publicaties?page=0&size={AANTAL}")
    if not isinstance(lijst, dict) or not isinstance(lijst.get("content"), list):
        log.error("Geen publicatielijst ontvangen. Is het endpoint gewijzigd of onbereikbaar?")
        return 1

    items = lijst["content"]
    log.info("Lijst ontvangen: %s publicaties (totaal beschikbaar: %s)",
             len(items), lijst.get("totalElements", "?"))

    rijen, ruw = [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            resultaat = verwerk(item)
        except Exception:  # één kapotte publicatie mag de rest niet stoppen
            log.exception("Onverwachte fout bij publicatie %s; overgeslagen.", item.get("publicatieId"))
            resultaat = None
        if resultaat:
            rijen.append(resultaat[0])
            ruw.append(resultaat[1])
        time.sleep(PAUZE)

    if not rijen:
        log.error("Geen enkele publicatie verwerkt.")
        return 1

    with open(CSV_BESTAND, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rijen[0].keys()))
        writer.writeheader()
        writer.writerows(rijen)

    with open(JSON_BESTAND, "w", encoding="utf-8") as f:
        json.dump(
            [{"samenvatting": r, "bron": b} for r, b in zip(rijen, ruw)],
            f, ensure_ascii=False, indent=2,
        )

    met_cpv = sum(1 for r in rijen if r["CPV-codes"])
    met_deadline = sum(1 for r in rijen if r["Deadline"])
    met_docs = sum(1 for r in rijen if r["Aantal documenten"] or r["Extern platform link"])
    log.info("Klaar: %s/%s publicaties verwerkt", len(rijen), len(items))
    log.info("  met CPV-codes: %s | met deadline: %s | met documenten of externe link: %s",
             met_cpv, met_deadline, met_docs)
    log.info("Bestanden: %s, %s, %s", CSV_BESTAND, JSON_BESTAND, LOG_BESTAND)
    return 0


if __name__ == "__main__":
    sys.exit(main())
