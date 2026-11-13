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

## Automatisch
De GitHub Action *Aanbestedingen ophalen* draait elke dag om 05:47 UTC en publiceert het overzicht op GitHub Pages. Handmatig starten kan via **Actions → Aanbestedingen ophalen → Run workflow**.

Instellingen via omgevingsvariabelen: `TENDERNED_DAGEN_TERUG`, `TED_DAGEN_TERUG`, `TED_LANDEN`, `OVERZICHT_BEWAAR_DAGEN`.
