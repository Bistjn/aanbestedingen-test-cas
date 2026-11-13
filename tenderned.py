
import csv
import json
from urllib.request import urlopen

url = "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties?page=0&size=10"

with urlopen(url, timeout=30) as response:
    data = json.load(response)

publicaties = data.get("content", [])

with open("aanbestedingen_nederland.csv", "w", newline="", encoding="utf-8-sig") as file:
    writer = csv.writer(file)
    writer.writerow(["Publicatie-ID", "Gegevens (JSON)"])

    for publicatie in publicaties:
        writer.writerow([
            publicatie.get("publicatieId", publicatie.get("id", "")),
            json.dumps(publicatie, ensure_ascii=False)
        ])

print(f"{len(publicaties)} Nederlandse publicaties opgehaald.")
