"""
Voegt TenderNed en TED samen tot één overzicht.

Invoer:  aanbestedingen_nederland.json (tenderned.py) en ted_publicaties.json (ted.py)
         + optioneel data/aanbestedingen.json (eerdere dagen, om geschiedenis te bewaren)
Uitvoer: data/aanbestedingen.json  -> alle aanbestedingen in één formaat
         overzicht.html            -> pagina met filters, werkt zonder internet of server

Dubbels: een Europese opdracht van een Nederlandse overheid staat op TenderNed én op TED.
TenderNed geeft dan het TED-nummer mee (veld pbNummerTed), dat gelijk is aan het
TED publication-number. Die twee worden samengevoegd tot één regel.
"""

import html
import json

import matching
import logging
import os
import sys
from datetime import date, timedelta

BEWAAR_DAGEN = int(os.environ.get("OVERZICHT_BEWAAR_DAGEN", "60"))
DATA_BESTAND = os.path.join("data", "aanbestedingen.json")
HTML_BESTAND = "overzicht.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("overzicht")

# NUTS-niveau 2 (provincies). België: officiële NUTS-lijst van Eurostat.
# Nederland: afgeleid uit de NUTS-omschrijvingen in echte TenderNed-antwoorden
# (bv. NL350 'Utrecht', NL366 'Groot-Rijnmond' -> NL36 Zuid-Holland).
PROVINCIES = {
    "BE10": "Brussels Hoofdstedelijk Gewest",
    "BE21": "Antwerpen", "BE22": "Limburg (BE)", "BE23": "Oost-Vlaanderen",
    "BE24": "Vlaams-Brabant", "BE25": "West-Vlaanderen",
    "BE31": "Waals-Brabant", "BE32": "Henegouwen", "BE33": "Luik",
    "BE34": "Luxemburg (BE)", "BE35": "Namen",
    "NL11": "Groningen", "NL12": "Friesland", "NL13": "Drenthe",
    "NL21": "Overijssel", "NL22": "Gelderland", "NL23": "Flevoland",
    "NL32": "Noord-Holland", "NL34": "Zeeland", "NL35": "Utrecht", "NL36": "Zuid-Holland",
    "NL41": "Noord-Brabant", "NL42": "Limburg (NL)",
}

TYPE_OPDRACHT = {"supplies": "Leveringen", "services": "Diensten", "works": "Werken",
                 "leveringen": "Leveringen", "diensten": "Diensten", "werken": "Werken"}

# Fasegroepen, zodat beide bronnen dezelfde filter delen.
FASE_GROEP_TENDERNED = {"AAO": "Aankondiging", "REC": "Rectificatie", "AGO": "Gegund",
                        "MAC": "Marktconsultatie", "VAK": "Vooraankondiging",
                        "VBE": "Beëindigd"}


def fase_groep_ted(code):
    if code.startswith("cn-"):
        return "Aankondiging"
    if code.startswith("can-") or code == "veat":
        return "Gegund"
    if code.startswith("pin-"):
        return "Vooraankondiging"
    if code == "pmc":
        return "Marktconsultatie"
    return "Overig"


def lees_json(pad):
    try:
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("%s niet gevonden; deze bron wordt overgeslagen.", pad)
    except (ValueError, OSError) as fout:
        log.error("%s kon niet gelezen worden: %s", pad, fout)
    return []


def splits(tekst):
    return [d.strip() for d in str(tekst or "").split("|") if d.strip()]


def provincies(nuts_codes):
    return sorted({PROVINCIES[c[:4]] for c in nuts_codes if c[:4] in PROVINCIES})


def kort(tekst, n=700):
    tekst = " ".join(str(tekst or "").split())
    return tekst if len(tekst) <= n else tekst[:n].rsplit(" ", 1)[0] + " …"


# ---------------------------------------------------------------------------
# Omzetten naar één gemeenschappelijk formaat
# ---------------------------------------------------------------------------
def van_tenderned(item):
    s, bron = item.get("samenvatting", {}), item.get("bron", {})
    detail = bron.get("detail") or {}
    nuts = [n.get("code") for n in detail.get("nutsCodes") or [] if isinstance(n, dict) and n.get("code")]
    cpv = [c.get("code") for c in detail.get("cpvCodes") or [] if isinstance(c, dict) and c.get("code")]
    return {
        "id": f"TN-{s.get('ID')}",
        "bronnen": ["TenderNed"],
        "ted_nummer": s.get("TED-nummer") or "",
        "titel": s.get("Titel", ""),
        "opdrachtgever": s.get("Opdrachtgever", ""),
        "land": "NL",
        "provincies": provincies(nuts),
        "nuts": nuts,
        "fase": s.get("Fase", ""),
        "fase_groep": FASE_GROEP_TENDERNED.get(s.get("Fase code", ""), "Overig"),
        "type": TYPE_OPDRACHT.get(str(s.get("Type opdracht", "")).lower(), s.get("Type opdracht", "")),
        "procedure": s.get("Procedure", ""),
        "publicatiedatum": str(s.get("Publicatiedatum", ""))[:10],
        "deadline": str(s.get("Deadline", ""))[:10],
        "cpv": cpv,
        "cpv_hoofd": s.get("CPV hoofdcode", ""),
        "omschrijving": kort(s.get("Omschrijving")),
        "links": {
            "pagina": s.get("TenderNed-pagina", ""),
            "pdf": s.get("Aankondiging PDF", ""),
            "documenten": [d.get("url") for d in bron.get("documenten") or [] if d.get("url")],
            "zip": s.get("Alle documenten (zip)", ""),
            "extern": s.get("Extern platform link", ""),
            "ted": "",
        },
    }


def van_ted(item):
    s = item.get("samenvatting", {})
    nuts = splits(s.get("NUTS-codes"))
    land = {"BEL": "BE", "NLD": "NL"}.get(splits(s.get("Land"))[0] if splits(s.get("Land")) else "", "")
    return {
        "id": f"TED-{s.get('ID')}",
        "bronnen": ["TED"],
        "ted_nummer": s.get("ID", ""),
        "titel": s.get("Titel", ""),
        "opdrachtgever": s.get("Opdrachtgever", ""),
        "land": land,
        "provincies": provincies(nuts),
        "nuts": nuts,
        "fase": s.get("Fase", ""),
        "fase_groep": fase_groep_ted(s.get("Fase code", "")),
        "type": TYPE_OPDRACHT.get(str(s.get("Type opdracht", "")).lower(), s.get("Type opdracht", "")),
        "procedure": s.get("Procedure", ""),
        "publicatiedatum": s.get("Publicatiedatum", ""),
        "deadline": s.get("Deadline inschrijving") or s.get("Deadline aanvraag deelname") or "",
        "cpv": splits(s.get("CPV-codes")),
        "cpv_hoofd": (splits(s.get("CPV hoofdcode")) or [""])[0],
        "omschrijving": kort(s.get("Omschrijving")),
        "links": {
            "pagina": s.get("TED-pagina", ""),
            "pdf": s.get("Aankondiging PDF", ""),
            "documenten": splits(s.get("Documentlinks")),
            "zip": "",
            "extern": "",
            "ted": s.get("TED-pagina", ""),
        },
    }


def volledig(r):
    """Vul ontbrekende velden aan, zodat oudere of onvolledige gegevens niet crashen."""
    standaard = {"bronnen": [], "ted_nummer": "", "titel": "", "opdrachtgever": "", "land": "",
                 "provincies": [], "nuts": [], "fase": "", "fase_groep": "Overig", "type": "",
                 "procedure": "", "publicatiedatum": "", "deadline": "", "cpv": [], "cpv_hoofd": "",
                 "omschrijving": ""}
    for k, v in standaard.items():
        if not isinstance(r.get(k), type(v)):
            r[k] = v
    links = r.get("links") if isinstance(r.get("links"), dict) else {}
    for k in ("pagina", "pdf", "zip", "extern", "ted"):
        links[k] = links.get(k) or ""
    links["documenten"] = links.get("documenten") if isinstance(links.get("documenten"), list) else []
    r["links"] = links
    return r


def samenvoegen(records):
    """Ontdubbel op id, en voeg TenderNed + TED samen via het TED-nummer."""
    per_id = {}
    for r in records:
        per_id[r["id"]] = volledig(r)  # nieuwere versie van dezelfde publicatie wint
    tn_per_ted = {r["ted_nummer"]: r for r in per_id.values()
                  if "TenderNed" in r["bronnen"] and r["ted_nummer"]}
    resultaat, samengevoegd = [], 0
    for r in per_id.values():
        if r["bronnen"] == ["TED"] and r["ted_nummer"] in tn_per_ted:
            tn = tn_per_ted[r["ted_nummer"]]
            if "TED" not in tn["bronnen"]:
                tn["bronnen"].append("TED")
            tn["links"]["ted"] = r["links"]["pagina"]
            tn["deadline"] = tn["deadline"] or r["deadline"]
            tn["provincies"] = tn["provincies"] or r["provincies"]
            samengevoegd += 1
            continue
        resultaat.append(r)
    return resultaat, samengevoegd


# ---------------------------------------------------------------------------
# HTML-pagina
# ---------------------------------------------------------------------------
PAGINA = r"""<!DOCTYPE html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aanbestedingen België & Nederland</title>
<style>
:root{--bg:#f6f7f9;--kaart:#fff;--tekst:#1d2433;--zacht:#5b6476;--lijn:#e2e5ea;--accent:#1f5eff;--groen:#127a3e;--oranje:#a85a00}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--tekst)}
header{padding:20px 24px 8px}h1{margin:0;font-size:22px}header p{margin:4px 0 0;color:var(--zacht)}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;padding:12px 24px;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--lijn);z-index:2}
.filters label{display:flex;flex-direction:column;font-size:12px;color:var(--zacht);gap:3px}
input,select{font:inherit;padding:7px 9px;border:1px solid var(--lijn);border-radius:8px;background:#fff;color:var(--tekst)}
.check{flex-direction:row!important;align-items:center;gap:6px!important;font-size:14px!important;color:var(--tekst)!important;padding-top:18px}
.balk{display:flex;justify-content:space-between;align-items:center;padding:10px 24px;color:var(--zacht);font-size:14px;gap:10px;flex-wrap:wrap}
main{padding:0 24px 40px;display:grid;gap:10px}
.kaart{background:var(--kaart);border:1px solid var(--lijn);border-radius:12px;padding:14px 16px}
.kaart h2{font-size:16px;margin:0 0 4px}.kaart h2 a{color:var(--tekst);text-decoration:none}.kaart h2 a:hover{color:var(--accent)}
.meta{color:var(--zacht);font-size:13px;display:flex;flex-wrap:wrap;gap:4px 14px;margin-bottom:6px}
.label{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;background:#eef1f6;color:var(--zacht);margin-right:4px}
.label.open{background:#e3f4ea;color:var(--groen)}.label.bron{background:#e8eeff;color:var(--accent)}.label.kort{background:#fff1df;color:var(--oranje)}
.omschr{font-size:14px;margin:6px 0;color:#384152}
.links a{font-size:13px;margin-right:12px;color:var(--accent)}
details summary{cursor:pointer;font-size:13px;color:var(--accent)}details div{font-size:13px;margin-top:4px;word-break:break-all}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--lijn);background:#fff;cursor:pointer}
.label.match{background:#1f5eff;color:#fff}.label.klant{background:#7a3db8;color:#fff}.waarom{font-size:13px;color:var(--zacht)}
.profiel{margin:0 24px 10px;padding:10px 14px;background:#fff;border:1px dashed var(--lijn);border-radius:10px;font-size:13px;color:var(--zacht)}
.profiel b{color:var(--tekst)}.chip{display:inline-block;background:#eef1f6;border-radius:99px;padding:0 8px;margin:2px 3px 0 0;color:var(--tekst)}
.leeg{padding:40px;text-align:center;color:var(--zacht)}
</style></head><body>
<header><h1>Aanbestedingen België &amp; Nederland</h1>
<p>Bronnen: TenderNed en TED · bijgewerkt __BIJGEWERKT__ · __AANTAL__ aanbestedingen</p></header>
<div class="filters">
 <label>Zoekprofiel<select id="f-profiel"><option value="">Geen (alles tonen)</option></select></label>
 <label id="l-soort" hidden>Soort match<select id="f-soort"><option value="">Alles</option><option value="direct">Directe match</option><option value="klant">Mogelijks interessant voor klant</option></select></label>
 <label>Minimale match<select id="f-min"><option value="40">40% of meer</option><option value="60">60% of meer</option><option value="80">80% of meer</option><option value="0">Alles (met score)</option></select></label>
 <label>Zoeken<input id="f-tekst" type="search" placeholder="titel, opdrachtgever, omschrijving"></label>
 <label>Land<select id="f-land"><option value="">Alle</option><option value="BE">België</option><option value="NL">Nederland</option></select></label>
 <label>Provincie<select id="f-prov"><option value="">Alle</option></select></label>
 <label>Fase<select id="f-fase"><option value="">Alle</option></select></label>
 <label>Type opdracht<select id="f-type"><option value="">Alle</option></select></label>
 <label>CPV-code begint met<input id="f-cpv" placeholder="bv. 45 of 7221" inputmode="numeric"></label>
 <label>Gepubliceerd<select id="f-dagen"><option value="">Altijd</option><option value="1">Laatste 2 dagen</option><option value="7">Laatste week</option><option value="30">Laatste 30 dagen</option></select></label>
 <label class="check"><input id="f-open" type="checkbox"> Alleen nog open voor inschrijving</label>
</div>
<div class="balk"><span id="teller"></span>
 <label>Sorteren <select id="f-sort"><option value="pub">Nieuwste eerst</option><option value="match">Beste match</option><option value="deadline">Deadline eerst</option></select></label></div>
<div id="profiel" class="profiel" hidden></div>
<main id="lijst"></main>
<div style="text-align:center;padding-bottom:40px"><button id="meer" hidden>Toon meer</button></div>
<script id="data" type="application/json">__DATA__</script>
<script id="profielen" type="application/json">__PROFIELEN__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const VANDAAG = new Date().toISOString().slice(0,10);
const $ = id => document.getElementById(id);
const PROFIELEN = JSON.parse(document.getElementById('profielen').textContent);
let toon = 100;
PROFIELEN.forEach(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.naam;$('f-profiel').appendChild(o);});
function vul(id, waarden){ const s=$(id); [...new Set(waarden)].filter(Boolean).sort().forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;s.appendChild(o);}); }
vul('f-prov', DATA.flatMap(r=>r.provincies)); vul('f-fase', DATA.map(r=>r.fase_groep)); vul('f-type', DATA.map(r=>r.type));
function esc(t){ return String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function dagenTot(d){ return Math.round((new Date(d)-new Date(VANDAAG))/864e5); }
function profiel(){ return PROFIELEN.find(x=>x.id===$('f-profiel').value) || null; }
function toonProfiel(p){
  const el=$('profiel');
  if(!p){ el.hidden=true; return; }
  el.hidden=false;
  el.innerHTML = `<b>${esc(p.naam)}</b> · ${esc(p.toelichting)}<br>De matchscore (0–100%) wordt elke dag berekend`
    + (p.soort==='inhoud' ? ' door een AI-taalmodel dat titel en volledige omschrijving inhoudelijk vergelijkt (ook in het Frans en Engels), aangevuld met vaktermen en CPV-codes.' : ' op basis van de naam van de opdrachtgever.');
}
function gefilterd(){
  const tekst=$('f-tekst').value.toLowerCase().trim(), land=$('f-land').value, prov=$('f-prov').value, fase=$('f-fase').value,
        type=$('f-type').value, cpv=$('f-cpv').value.replace(/\D/g,''), dagen=$('f-dagen').value, open=$('f-open').checked;
  const grens = dagen ? new Date(Date.now()-dagen*864e5).toISOString().slice(0,10) : '';
  const p = profiel(); toonProfiel(p); const min = Number($('f-min').value);
  let r = DATA.filter(a => (!land||a.land===land) && (!prov||a.provincies.includes(prov)) && (!fase||a.fase_groep===fase)
    && (!type||a.type===type) && (!cpv||a.cpv.some(c=>c.startsWith(cpv))) && (!grens||a.publicatiedatum>=grens)
    && (!open||(a.deadline&&a.deadline>=VANDAAG))
    && (!tekst||(a.titel+' '+a.opdrachtgever+' '+a.omschrijving).toLowerCase().includes(tekst)));
  if (p){
    const soort=$('f-soort').value;
    r.forEach(a=>{
      const m=(a.matches||{})[p.id]||{score:0,redenen:[]}, d=m.score||0, k=m.klant_score||0;
      // Directe match gaat voor; anders klantopdracht; zo krijgt elke opdracht één label
      if (soort!=='klant' && d>=Math.max(min,1)) { a._soort='direct'; a._score=d; a._redenen=m.redenen||[]; }
      else if (soort!=='direct' && p.klant && k>=Math.max(min,1)) { a._soort='klant'; a._score=k; a._redenen=m.klant_redenen||[]; }
      else { a._soort=''; a._score=0; a._redenen=[]; }
    });
    r=r.filter(a=>a._soort);
  }
  if ($('f-sort').value==='match' && p) r.sort((a,b)=>b._score-a._score);
  else if ($('f-sort').value==='deadline') r.sort((a,b)=>(a.deadline&&a.deadline>=VANDAAG?a.deadline:'9')<(b.deadline&&b.deadline>=VANDAAG?b.deadline:'9')?-1:1);
  else r.sort((a,b)=>a.publicatiedatum<b.publicatiedatum?1:-1);
  return r;
}
let MATCH = false;
function kaart(a){
  const d = a.deadline ? dagenTot(a.deadline) : null;
  const dl = a.deadline ? `<span>Deadline: <b>${esc(a.deadline)}</b>${d>=0&&d<=7?' <span class="label kort">nog '+d+' d</span>':''}${d<0?' (verstreken)':''}</span>` : '';
  const docs = a.links.documenten||[];
  return `<article class="kaart">
   <div>${a.bronnen.map(b=>`<span class="label bron">${esc(b)}</span>`).join('')}<span class="label ${a.fase_groep==='Aankondiging'?'open':''}">${esc(a.fase||a.fase_groep)}</span>${a.type?`<span class="label">${esc(a.type)}</span>`:''}</div>
   ${MATCH?`<div class="waarom"><span class="label ${a._soort==='klant'?'klant':'match'}">${a._soort==='klant'?'Mogelijks interessant voor klant · ':'Match '}${a._score}%</span> waarom: ${esc(a._redenen.join(', '))}</div>`:''}
   <h2><a href="${esc(a.links.pagina)}" target="_blank" rel="noopener">${esc(a.titel||'(zonder titel)')}</a></h2>
   <div class="meta"><span>${esc(a.opdrachtgever)}</span><span>${a.land==='BE'?'België':a.land==='NL'?'Nederland':''}${a.provincies.length?' · '+esc(a.provincies.join(', ')):''}</span><span>Gepubliceerd: ${esc(a.publicatiedatum)}</span>${dl}</div>
   ${a.cpv.length?`<div class="meta">CPV: ${esc(a.cpv.slice(0,6).join(', '))}${a.cpv.length>6?' …':''}</div>`:''}
   ${a.omschrijving?`<div class="omschr">${esc(a.omschrijving)}</div>`:''}
   <div class="links">${a.links.pdf?`<a href="${esc(a.links.pdf)}" target="_blank" rel="noopener">Aankondiging (PDF)</a>`:''}${a.links.zip?`<a href="${esc(a.links.zip)}" target="_blank" rel="noopener">Alle documenten (zip)</a>`:''}${a.links.extern?`<a href="${esc(a.links.extern)}" target="_blank" rel="noopener">Documenten op extern platform</a>`:''}${a.links.ted&&a.bronnen.length>1?`<a href="${esc(a.links.ted)}" target="_blank" rel="noopener">Ook op TED</a>`:''}</div>
   ${docs.length?`<details><summary>${docs.length} document${docs.length>1?'en':''}</summary><div>${docs.map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join('<br>')}</div></details>`:''}
  </article>`;
}
function teken(){
  const r = gefilterd(); MATCH = !!profiel();
  $('teller').textContent = `${r.length} van ${DATA.length} aanbestedingen`;
  $('lijst').innerHTML = r.length ? r.slice(0,toon).map(kaart).join('') : '<div class="leeg">Geen aanbestedingen voor deze filters.</div>';
  $('meer').hidden = r.length <= toon;
}
$('f-profiel').addEventListener('change',()=>{ const p=profiel(); $('f-sort').value = p ? 'match' : 'pub'; $('l-soort').hidden = !(p&&p.klant); if(!(p&&p.klant)) $('f-soort').value=''; });
document.querySelectorAll('.filters input, .filters select, #f-sort').forEach(e=>e.addEventListener('input',()=>{toon=100;teken();}));
$('meer').addEventListener('click',()=>{toon+=100;teken();});
teken();
</script></body></html>"""


def main():
    nieuw = [van_tenderned(i) for i in lees_json("aanbestedingen_nederland.json") if isinstance(i, dict)]
    nieuw += [van_ted(i) for i in lees_json("ted_publicaties.json") if isinstance(i, dict)]
    log.info("Nieuw opgehaald: %s records", len(nieuw))

    oud = lees_json(DATA_BESTAND) if os.path.exists(DATA_BESTAND) else []
    if oud:
        log.info("Eerdere gegevens: %s aanbestedingen", len(oud))

    # Oude eerst, zodat nieuwe versies van dezelfde publicatie winnen
    alles, samengevoegd = samenvoegen([r for r in oud if isinstance(r, dict) and r.get("id")] + nieuw)
    grens = (date.today() - timedelta(days=BEWAAR_DAGEN)).isoformat()
    alles = [r for r in alles if r.get("publicatiedatum", "") >= grens
             or (r.get("deadline") and r["deadline"] >= date.today().isoformat())]
    alles.sort(key=lambda r: r.get("publicatiedatum", ""), reverse=True)

    if not alles:
        log.error("Geen enkele aanbesteding om te tonen.")
        return 1

    profielen = matching.lees_profielen()
    log.info("Zoekprofielen: %s", ", ".join(p["naam"] for p in profielen) or "geen")
    matching.bereken(alles, profielen)

    os.makedirs("data", exist_ok=True)
    with open(DATA_BESTAND, "w", encoding="utf-8") as f:
        json.dump(alles, f, ensure_ascii=False, indent=1)

    # De pagina krijgt geen ruwe AI-gegevens (_sim), alleen de scores
    pagina_data = [{k: v for k, v in r.items() if k != "_sim"} for r in alles]
    data_json = json.dumps(pagina_data, ensure_ascii=False).replace("</", "<\\/")
    profiel_info = [{"id": p["id"], "naam": p["naam"], "soort": p["soort"], "toelichting": p.get("toelichting", ""),
                     "klant": bool(p.get("klant"))} for p in profielen]
    profielen_json = json.dumps(profiel_info, ensure_ascii=False).replace("</", "<\\/")
    pagina = (PAGINA.replace("__DATA__", data_json)
                    .replace("__PROFIELEN__", profielen_json)
                    .replace("__DREMPEL__", str(matching.DREMPEL))
                    .replace("__BIJGEWERKT__", html.escape(date.today().strftime("%d-%m-%Y")))
                    .replace("__AANTAL__", str(len(alles))))
    with open(HTML_BESTAND, "w", encoding="utf-8") as f:
        f.write(pagina)

    per_land = {}
    for r in alles:
        per_land[r["land"] or "?"] = per_land.get(r["land"] or "?", 0) + 1
    log.info("Overzicht: %s aanbestedingen (%s) | %s dubbels samengevoegd (TenderNed + TED)",
             len(alles), per_land, samengevoegd)
    log.info("  open voor inschrijving: %s | met provincie: %s",
             sum(1 for r in alles if r["deadline"] and r["deadline"] >= date.today().isoformat()),
             sum(1 for r in alles if r["provincies"]))
    log.info("Bestanden: %s, %s", DATA_BESTAND, HTML_BESTAND)
    return 0


if __name__ == "__main__":
    sys.exit(main())
