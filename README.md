# aanbestedingen-test-cas

Dagelijks overzicht van Belgische en Nederlandse overheidsaanbestedingen.

**Overzicht:** https://bistjn.github.io/aanbestedingen-test-cas/

## Bronnen
| Bron | Wat | Script |
|---|---|---|
| TenderNed (TNS-webservice, publiek) | alle Nederlandse publicaties | `tenderned.py` |
| TED Search API v3 (EU, anoniem) | Belgische en Nederlandse publicaties boven de Europese drempels | `ted.py` |
| Belgisch e-Procurement | nog niet: geen officiële API of open data beschikbaar | — |

`overzicht.py` voegt alles samen, haalt dubbels eruit (TenderNed + TED via het TED-nummer) en maakt `overzicht.html` met filters op land, provincie, fase, type, CPV en deadline.

## Zoekprofielen en matchscore
De profielen staan in `profielen/` (één JSON-bestand per profiel). `matching.py` berekent per aanbesteding een matchscore van 0 tot 100%. Op de pagina kies je een profiel, en standaard verschijnen dan de opdrachten vanaf 40%.
- **IQVIA** (`soort: inhoud`): 60% betekenis, berekend door een meertalig AI-taalmodel (`intfloat/multilingual-e5-small`, draait gratis in GitHub Actions) dat titel en omschrijving vergelijkt met de activiteiten van IQVIA, 25% vaktermen en 15% CPV.
- **Zorginstellingen** (`soort: opdrachtgever`): herkent ziekenhuizen, woonzorgcentra, GGZ en dergelijke aan de naam van de opdrachtgever.

## Automatisch
De GitHub Action *Aanbestedingen ophalen* draait elke dag om 05:47 UTC en publiceert het overzicht op GitHub Pages. Handmatig starten kan via **Actions → Aanbestedingen ophalen → Run workflow**.

Instellingen via omgevingsvariabelen: `TENDERNED_DAGEN_TERUG`, `TED_DAGEN_TERUG`, `TED_LANDEN`, `OVERZICHT_BEWAAR_DAGEN`.
