

import csv
import json
import time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

BASE = "https://www.tenderned.nl/papi/tenderned-rs-tns/v2"

def ophalen(url):
    try:
        with urlopen(url, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, ValueError) as fout:
        print(f"Fout bij {url}: {fout}")
        return None

data = ophalen(f"{BASE}/publicaties?page=0&size=10")

if not data or "content" not in data:
    raise RuntimeError("Geen publicaties ontvangen.")

resultaten = []

for publicatie in data["content"]:
    nummer = publicatie["publicatieId"]

    detail = ophalen(f"{BASE}/publicaties/{nummer}") or publicatie
    documenten = ophalen(
        f"{BASE}/publicaties/{nummer}/documenten"
    ) or {}

    lijst = documenten.get("documenten", [])

    documentlinks = []
    for doc in lijst:
        link = doc.get("links", {}).get("download", {}).get("href", "")
        if link.startswith("/"):
            link = "https://www.tenderned.nl" + link
        if link:
            documentlinks.append(link)

    cpv = detail.get("cpvCodes", detail.get("cpv", []))

    resultaten.append({
        "ID": nummer,
        "Titel": detail.get("aanbestedingNaam", ""),
        "Opdrachtgever": detail.get("opdrachtgeverNaam", ""),
        "Publicatietype": detail.get("typePublicatie", {}).get("omschrijving", ""),
        "Publicatiedatum": detail.get("publicatieDatum", ""),
        "Deadline": detail.get("sluitingsDatum", ""),
        "CPV": json.dumps(cpv, ensure_ascii=False),
        "Omschrijving": detail.get("opdrachtBeschrijving", ""),
        "Aantal documenten": len(lijst),
        "Documentlinks": " | ".join(documentlinks),
        "Bronlink": detail.get("link", {}).get("href", ""),
        "Detail JSON": json.dumps(detail, ensure_ascii=False)
    })

    print(f"{nummer}: {len(lijst)} documenten")
    time.sleep(0.3)

with open("aanbestedingen_nederland.csv", "w", newline="", encoding="utf-8-sig") as file:
    writer = csv.DictWriter(file, fieldnames=resultaten[0].keys())
    writer.writeheader()
    writer.writerows(resultaten)

print(f"Klaar: {len(resultaten)} publicaties verwerkt.")
