import os, time, json, logging, requests, re, hashlib
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════════════════════
# CONFIG — Toutes les variables Railway
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_KEY", "")

# Scan
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "45"))

# Filtres prix
PRIX_MIN          = int(os.environ.get("PRIX_MIN", "500"))
PRIX_MAX          = int(os.environ.get("PRIX_MAX", "15000"))

# Filtres kilométrage
KM_MIN            = int(os.environ.get("KM_MIN", "0"))
KM_MAX            = int(os.environ.get("KM_MAX", "250000"))

# Filtres année
ANNEE_MIN         = int(os.environ.get("ANNEE_MIN", "2005"))
ANNEE_MAX         = int(os.environ.get("ANNEE_MAX", "2025"))

# Carburant — vide = tous | ex: DIESEL,HYBRIDE
CARBURANTS_INCLUS = [c.strip().upper() for c in os.environ.get("CARBURANTS_INCLUS","").split(",") if c.strip()]
CARBURANTS_EXCLUS = [c.strip().upper() for c in os.environ.get("CARBURANTS_EXCLUS","").split(",") if c.strip()]

# Boîte — vide = toutes | ex: MANUELLE,AUTOMATIQUE
BOITES_INCLUSES   = [b.strip().upper() for b in os.environ.get("BOITES_INCLUSES","").split(",") if b.strip()]
BOITES_EXCLUES    = [b.strip().upper() for b in os.environ.get("BOITES_EXCLUES","DSG,EDC,ROBOTISEE").split(",") if b.strip()]

# Marques
MARQUES           = [m.strip().upper() for m in os.environ.get("MARQUES","").split(",") if m.strip()]
MARQUES_BLACKLIST = [m.strip().upper() for m in os.environ.get("MARQUES_BLACKLIST","MICROCAR,CHATENET,LIGIER,AIXAM,BELLIER").split(",") if m.strip()]

# Mots clés
MOTS_CLES         = [m.strip().lower() for m in os.environ.get("MOTS_CLES","").split(",") if m.strip()]

# Seuils pépites — MAX n'envoie QUE si TOUS ces critères sont remplis
SCORE_MIN         = int(os.environ.get("SCORE_MIN", "70"))          # Note /100 minimum
MARGE_NETTE_MIN   = int(os.environ.get("MARGE_NETTE_MIN", "600"))   # Marge nette AE minimum €
DECOTE_MIN        = int(os.environ.get("DECOTE_MIN", "15"))         # % minimum sous Argus
SCORE_URGENTE     = int(os.environ.get("SCORE_URGENTE", "88"))      # Score alerte urgente (son)

# Fiscal auto-entrepreneur
COTISATIONS_AE    = float(os.environ.get("COTISATIONS_AE", "12.3"))
TVA_MARGE         = os.environ.get("TVA_MARGE", "true").lower() == "true"

# Rapport
RAPPORT_INTERVAL  = int(os.environ.get("RAPPORT_INTERVAL", "1"))   # Rapport toutes les X minutes

# Parallel
MAX_WORKERS       = int(os.environ.get("MAX_WORKERS", "10"))

KNOWN_FILE  = "known_max.json"
STATS_FILE  = "stats_max.json"
PEPITES_FILE= "pepites_max.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("MAX")

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
]
_ua = 0
def get_headers():
    global _ua
    _ua = (_ua + 1) % len(UA_LIST)
    return {"User-Agent": UA_LIST[_ua], "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9", "Connection": "keep-alive", "Cache-Control": "no-cache"}

# ══════════════════════════════════════════════════════════════
# BASE MOTEURS — 35 motorisations encodées
# ══════════════════════════════════════════════════════════════
MOTEURS_DB = {
    # ─── MOTEURS À FUIR ─────────────────────────────────────
    "EP6|1.6 THP|PRINCE":           (-30, 2,  "Chaîne distrib fragile → casse 1500-3000€. Fuyez avant 2016."),
    "1.2 TCE 115|1.2 TCE 120":      (-20, 3,  "Joint culasse fréquent → 800-2000€. Rappels constructeur 2014-2018."),
    "N47|116D|118D|120D|2.0D BMW":  (-30, 2,  "Chaîne côté boîte → dépose moteur 3000-5000€. Bannir absolument."),
    "N20":                           (-15, 4,  "Chaîne BMW côté boîte + pompe eau plastique. Risqué."),
    "OM651|220 CDI|200 CDI MERC":   (-20, 3,  "Injecteurs Piezo + swirl flaps catastrophiques → 2000-5000€."),
    "M271|KOMPRESSOR":               (-10, 5,  "Chaîne côté boîte Mercedes. Vérifier bruit démarrage."),
    "1.4 TSI DSG|CAXA|CTHD":        (-20, 3,  "DSG7 embrayage sec défaillant → 700-2000€. Éviter DSG7."),
    "2.0 TFSI|BWE|BWT|AXX":         (-15, 4,  "Consommation huile 1L/1000km anormale. Recours collectif USA."),
    "TWINAIR|875CC|0.9 TWINAIR":    (-25, 2,  "Catastrophe absolue. Courroie 3ans, vibrations, consommation × 2."),
    "1.0 ECOBOOST|FOXON":           (-15, 4,  "Joint culasse avant 2016 → 800-1500€. OK après 2017."),
    "DV6|1.6 HDI 90|1.6 HDI 92":   (-10, 5,  "EGR + FAP coûteux usage ville. Route = correct."),
    "1.2 PURETECH|EB2|EB4":         (-15, 4,  "Courroie bain huile se dégrade → casse moteur. OK après 2019."),
    "2.0 TDI M9R|2.0 DCI LAG":     (-10, 5,  "Injecteurs Siemens fragiles >150k. Éviter >200 000km."),
    "A16DTH|1.6 CDTI OPEL":        (-8,  5,  "Chaîne Opel problématique. Vérifier bruit démarrage."),
    "1.4 T-JET":                    (-5,  5,  "Courroie obligatoire 60 000km. Turbine 170ch fragile."),

    # ─── MOTEURS FIABLES ────────────────────────────────────
    "K9K|1.5 DCI|1.5DCI":          (+20, 9,  "MEILLEUR diesel du marché. Taxis 400 000km. Achetez les yeux fermés."),
    "2ZR-FXE|HYBRID TOYOTA|YARIS H|AURIS H|COROLLA H|PRIUS": (+25, 10, "Légendaire. Taxis 600 000km. Batterie dure 300k. PÉPITE ABSOLUE."),
    "1NZ-FE|1.5 VVTI TOYOTA":      (+18, 9,  "Toyota increvable. JD Power N°1 fiabilité."),
    "1.9 TDI|ALH|AXR|AGR":         (+18, 9,  "Légendaire VW. 500 000km réguliers. Le meilleur diesel VW."),
    "2.0 TDI CFHC|2.0 TDI 2009":   (+12, 7,  "Bon TDI après 2009. Solide si entretien suivi."),
    "K4M|K4J|1.6 16V RENAULT":     (+15, 8,  "Excellent moteur atmosphérique Renault. Simple et fiable."),
    "1.3 MULTIJET|MULTIJET 75":    (+12, 8,  "Meilleur petit diesel Fiat. Fiable et économique."),
    "R18A|1.8 VTEC HONDA":         (+18, 9,  "Honda 400 000km sans problème. Fiabilité japonaise absolue."),
    "G4FA|1.2 MPI HYUNDAI":        (+15, 8,  "Coréens très fiables depuis 2010. Progression constante."),
    "U2|1.6 CRDI KIA|1.6 CRDI HYU":(+10, 7,  "Bon diesel coréen. Fiabilité correcte."),
    "HR16|1.6 NISSAN|MR20":        (+15, 8,  "Nissan fiable. Peu de problèmes connus."),
    "K12B|1.2 SUZUKI SWIFT":       (+15, 8,  "Suzuki Swift = pépite légère et fiable. 300 000km rapportés."),
    "DW10|2.0 HDI|BLUEHDI 150":    (+5,  7,  "Bon diesel PSA. Fiable si entretien. FAP à surveiller."),
    "1.6 TDI VW|CLHB|CAYC":       (+10, 7,  "Bon petit TDI VW. Économique et fiable."),
    "HR15DE|1.5 DCI NISSAN":       (+12, 8,  "Petit diesel Nissan fiable. Peu de problèmes."),
}

def analyser_moteur(titre):
    titre_up = titre.upper()
    best = None
    best_len = 0
    for cles, (score, fid, conseil) in MOTEURS_DB.items():
        for cle in cles.split("|"):
            cle = cle.strip()
            if len(cle) > 3 and cle in titre_up and len(cle) > best_len:
                best = (cles.split("|")[0], score, fid, conseil)
                best_len = len(cle)
    return best  # (nom, score, fiabilite, conseil) ou None

# ══════════════════════════════════════════════════════════════
# DÉTECTION CARBURANT / BOÎTE
# ══════════════════════════════════════════════════════════════
_DIESEL    = ["TDI","HDI","DCI","CDTI","TDCI","BLUEHDI","MULTIJET","CRDI","D4D","SDI","2.0D","1.5D","1.6D","1.9D","DIESEL"]
_HYBRIDE   = ["HYBRID","HYBRIDE","HEV","PHEV","E-HYBRID","RECHARGEABLE","PLUG-IN"]
_ELECTRIQUE= ["ELECTRIQUE","ELECTRIC","EV","BEV","ZOE","LEAF","IONIQ","E-TRON","MODEL 3","MEGANE E"]
_ESSENCE   = ["TSI","VTI","GTI","TFSI","TCE","PURETECH","MPI","16V","TURBO","ESSENCE","ESTENCE","1.0","1.2","1.4","1.6 E","1.8","2.0 E"]
_GPL       = ["GPL","LPG","BIFUEL"]
_ETHANOL   = ["E85","ETHANOL","FLEX","FLEXFUEL"]

_DSG       = ["DSG","S-TRONIC","PDK"]
_EDC       = ["EDC","DCT","POWERSHIFT","EAT6","EAT8"]
_CVT       = ["CVT","XTRONIC","MULTITRONIC","LINEARTRONIC"]
_ROBOT     = ["ROBOTISEE","ROBOTISÉE","AMT","EASYTRONIC","SENSODRIVE"]
_AUTO      = ["AUTOMATIQUE","BVA","TIPTRONIC","AUTO "]
_MANUELLE  = ["MANUELLE","BVM","BV5","BV6","BV6 ","MT ","BOITE MANUELLE"]

def detecter_carburant(titre):
    t = titre.upper()
    if any(m in t for m in _ELECTRIQUE): return "ELECTRIQUE"
    if any(m in t for m in _HYBRIDE):    return "HYBRIDE"
    if any(m in t for m in _GPL):        return "GPL"
    if any(m in t for m in _ETHANOL):    return "ETHANOL"
    if any(m in t for m in _DIESEL):     return "DIESEL"
    if any(m in t for m in _ESSENCE):    return "ESSENCE"
    return "?"

def detecter_boite(titre):
    t = titre.upper()
    if any(m in t for m in _DSG):    return "DSG"
    if any(m in t for m in _EDC):    return "EDC"
    if any(m in t for m in _CVT):    return "CVT"
    if any(m in t for m in _ROBOT):  return "ROBOTISEE"
    if any(m in t for m in _AUTO):   return "AUTOMATIQUE"
    if any(m in t for m in _MANUELLE):return "MANUELLE"
    return "?"

# ══════════════════════════════════════════════════════════════
# FISCAL AUTO-ENTREPRENEUR
# ══════════════════════════════════════════════════════════════
def calcul_ae(achat, revente):
    if revente <= achat:
        return {"brute":0,"tva":0,"cot":0,"nette":0,"roi":0}
    brute  = revente - achat
    tva    = round(brute / 1.20 * 0.20) if TVA_MARGE else 0
    cot    = round(revente * COTISATIONS_AE / 100)
    nette  = brute - tva - cot
    roi    = round((nette / achat) * 100, 1) if achat > 0 else 0
    return {"brute":brute,"tva":tva,"cot":cot,"nette":nette,"roi":roi}

# ══════════════════════════════════════════════════════════════
# SCORING /100
# ══════════════════════════════════════════════════════════════
MODELES_BONUS = {
    "YARIS HYBRID":25,"COROLLA HYBRID":25,"AURIS HYBRID":22,"PRIUS":22,
    "JAZZ":20,"CIVIC":18,"CR-V":15,
    "CLIO DCI":20,"CLIO 1.5":18,"K9K":20,
    "DUSTER":18,"LOGAN":16,"SANDERO":16,
    "GOLF TDI":16,"POLO TDI":15,"OCTAVIA TDI":18,"FABIA TDI":15,
    "208 1.2":10,"308 1.2":10,"3008":12,
    "SWIFT":15,"I20":14,"I30":14,"CEED":14,"TUCSON":12,"KONA":14,
    "YARIS":15,"AYGO":12,
}

MOTS_SUSPECTS  = ["accidenté","sinistre","epave","pièces","ne démarre","moteur hs",
                   "boite cassée","rouillé","inondé","grêle","brûlé","flood","hail"]
MOTS_POSITIFS  = ["1er main","première main","faible km","peu kilométré","révisé",
                   "entretien suivi","carnet","non fumeur","full options","toit ouvrant",
                   "cuir","garantie","certifié","concession","garage agréé"]

def get_note_pepite(score):
    if score >= 90: return "💎","AFFAIRE EXCEPTIONNELLE"
    if score >= 75: return "🔥","EXCELLENTE AFFAIRE"
    if score >= 60: return "✅","BONNE AFFAIRE"
    if score >= 45: return "⚠️","PASSABLE"
    return "❌","À ÉVITER"

def barre_score(score):
    f = round(score/10)
    return "█"*f + "░"*(10-f)

def scorer(a):
    score = 40
    t_up  = a["titre"].upper()
    t_low = a["titre"].lower()
    detail = {}

    # Modèle connu
    for m, b in MODELES_BONUS.items():
        if m in t_up:
            score += b; detail["modèle"] = f"+{b}pts ({m})"; break

    # Moteur
    mot = analyser_moteur(a["titre"])
    if mot:
        score += mot[1]; detail["moteur"] = f"{mot[1]:+d}pts ({mot[0][:20]})"

    # Source
    bs = {"enchere_etat":25,"enchere":15,"pro":10,"occasion":0}.get(a.get("type",""),0)
    if bs: score += bs; detail["source"] = f"+{bs}pts ({a['type']})"

    # Kilométrage
    km = a.get("km",0)
    if km > 0:
        if km < 50000:   b=18
        elif km < 80000: b=14
        elif km < 120000:b=10
        elif km < 160000:b=5
        elif km < 200000:b=2
        else:            b=-12
        score += b; detail["km"] = f"{b:+d}pts ({km:,}km)".replace(",",".")

    # Année
    an = a.get("annee",0)
    if an > 0:
        age = 2024 - an
        if age <= 3:    b=15
        elif age <= 6:  b=12
        elif age <= 10: b=8
        elif age <= 15: b=4
        else:           b=-8
        score += b; detail["année"] = f"{b:+d}pts ({an})"

    # Carburant
    carb = a.get("_carburant","?")
    cb = {"HYBRIDE":10,"ELECTRIQUE":8,"DIESEL":5,"GPL":3,"ESSENCE":2}.get(carb,0)
    if cb: score += cb; detail["carburant"] = f"+{cb}pts ({carb})"

    # Boîte
    boite = a.get("_boite","?")
    bb = {"AUTOMATIQUE":8,"MANUELLE":3,"DSG":-8,"EDC":-5,"CVT":-3,"ROBOTISEE":-6}.get(boite,0)
    if bb: score += bb; detail["boîte"] = f"{bb:+d}pts ({boite})"

    # Mots suspects
    for mot in MOTS_SUSPECTS:
        if mot in t_low:
            score -= 25; detail["⚠️suspect"] = f"-25pts ({mot})"; break

    # Mots positifs
    bp = 0
    trouvés = []
    for mot in MOTS_POSITIFS:
        if mot in t_low and bp < 15:
            bp += 3; trouvés.append(mot)
    if bp: score += bp; detail["positifs"] = f"+{bp}pts"

    a["_detail_score"] = detail
    return max(0, min(100, score))

# ══════════════════════════════════════════════════════════════
# STOCKAGE
# ══════════════════════════════════════════════════════════════
def load_json(f, default):
    try:
        with open(f) as fp: return json.load(fp)
    except: return default

def save_json(f, data):
    with open(f,"w") as fp: json.dump(data, fp, ensure_ascii=False)

def load_known():  return set(load_json(KNOWN_FILE, []))
def save_known(s): save_json(KNOWN_FILE, list(s)[-10000:])
def load_stats():  return load_json(STATS_FILE, {"scanne":0,"analyse":0,"pepites":0,"marge":0,"checks":0,"sources":{}})
def save_stats(s): save_json(STATS_FILE, s)
def load_pepites():return load_json(PEPITES_FILE, [])
def save_pepites(p):save_json(PEPITES_FILE, p[-300:])

# ══════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════
def extraire_prix(t):
    for p in [r"(\d[\d\s\u202f]{1,6})\s*€", r"€\s*(\d[\d\s\u202f]{1,6})"]:
        m = re.search(p, t)
        if m:
            try:
                v = int(re.sub(r"\s|\u202f","",m.group(1)))
                if 100 <= v <= 200000: return v
            except: pass
    return 0

def extraire_km(t):
    m = re.search(r"(\d[\d\s\u202f]{2,6})\s*km", t, re.I)
    if m:
        try:
            v = int(re.sub(r"\s|\u202f","",m.group(1)))
            if 500 <= v <= 999999: return v
        except: pass
    return 0

def extraire_annee(t):
    for m in re.finditer(r"\b(19[89]\d|200\d|201\d|202[0-4])\b", t):
        try:
            v = int(m.group(1))
            if 1990 <= v <= 2024: return v
        except: pass
    return 0

def get_url(url, timeout=15):
    try:
        r = requests.get(url, headers=get_headers(), timeout=timeout)
        r.raise_for_status()
        return r
    except: return None

def build(id, src, titre, prix, km, annee, url, typ):
    a = {"id":id,"source":src,"titre":titre[:120],"prix":prix,"km":km,"annee":annee,"url":url,"type":typ}
    a["_carburant"] = detecter_carburant(titre)
    a["_boite"]     = detecter_boite(titre)
    return a

def hid(s): return hashlib.md5(s.encode()).hexdigest()[:10]

def matches_filter(a):
    # Prix
    if a["prix"]  > 0 and a["prix"]  < PRIX_MIN:  return False
    if a["prix"]  > 0 and a["prix"]  > PRIX_MAX:  return False
    # KM
    if a["km"]    > 0 and a["km"]    < KM_MIN:    return False
    if a["km"]    > 0 and a["km"]    > KM_MAX:    return False
    # Année
    if a["annee"] > 0 and a["annee"] < ANNEE_MIN: return False
    if a["annee"] > 0 and a["annee"] > ANNEE_MAX: return False
    # Marques
    t = a["titre"].upper()
    if MARQUES and not any(m in t for m in MARQUES): return False
    if any(m in t for m in MARQUES_BLACKLIST):        return False
    # Mots clés
    if MOTS_CLES and not any(k in a["titre"].lower() for k in MOTS_CLES): return False
    # Carburant
    carb = a.get("_carburant","?")
    if CARBURANTS_INCLUS and carb not in CARBURANTS_INCLUS and carb != "?": return False
    if CARBURANTS_EXCLUS and carb in CARBURANTS_EXCLUS: return False
    # Boîte
    boite = a.get("_boite","?")
    if BOITES_INCLUSES and boite not in BOITES_INCLUSES and boite != "?": return False
    if BOITES_EXCLUES  and boite in BOITES_EXCLUES:  return False
    return True

# ══════════════════════════════════════════════════════════════
# SCRAPERS — 20 SOURCES
# ══════════════════════════════════════════════════════════════
def _parse(url, pattern, base, src, typ, limit=25):
    annonces = []
    r = get_url(url)
    if not r: return []
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.find_all("a", href=re.compile(pattern))[:limit]:
        href = item.get("href","")
        if not href: continue
        text = item.get_text(" ", strip=True)
        if len(text) < 5: continue
        full = base + href if href.startswith("/") else href
        aid  = hid(href)
        annonces.append(build(f"{src[:3]}_{aid}", src, text, extraire_prix(text), extraire_km(text), extraire_annee(text), full, typ))
    return annonces

def scrape_alcopa():
    try:
        r = get_url(f"https://www.alcopa-auction.fr/recherche?prixMax={PRIX_MAX}&prixMin={PRIX_MIN}&kmMax={KM_MAX}&tri=dateDesc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/vehicule/|/lot/|/voiture/"))[:30]:
            href = item.get("href","")
            m = re.search(r"/(vehicule|lot|voiture)/([^/?]+)", href)
            if not m: continue
            text = item.get_text(" ", strip=True)
            url_f = "https://www.alcopa-auction.fr" + href if href.startswith("/") else href
            out.append(build("alcopa_"+m.group(2), "🏆 Alcopa", text, extraire_prix(text), extraire_km(text), extraire_annee(text), url_f, "enchere"))
        log.info(f"   Alcopa: {len(out)}")
        return out
    except Exception as e: log.warning(f"Alcopa: {e}"); return []

def scrape_bca():
    try:
        a = _parse("https://www.bcautoencheres.fr/buyer/facetedSearch/vehicle?bq=salecountry_exact%3AFR&sortby=auctiondate&pageSize=25",
                   r"/buyer/|/vehicle/", "https://www.bcautoencheres.fr", "🔵 BCA Pro", "enchere", 25)
        log.info(f"   BCA: {len(a)}"); return a
    except Exception as e: log.warning(f"BCA: {e}"); return []

def scrape_agorastore():
    try:
        r = get_url("https://www.agorastore.fr/vehicules-transports/voitures")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/lot/"))[:25]:
            href = item.get("href","")
            m2 = re.search(r"/lot/([^/]+)", href)
            if not m2: continue
            text = item.get_text(" ", strip=True)
            out.append(build("agora_"+m2.group(1), "🏛️ Agorastore", text, extraire_prix(text), 0, extraire_annee(text), "https://www.agorastore.fr"+href, "enchere"))
        log.info(f"   Agorastore: {len(out)}"); return out
    except Exception as e: log.warning(f"Agorastore: {e}"); return []

def scrape_interencheres():
    try:
        a = _parse("https://www.interencheres.com/vehicules-transports/voitures/?sort=date_desc",
                   r"/lot", "https://www.interencheres.com", "⚖️ Interenchères", "enchere", 20)
        log.info(f"   Interenchères: {len(a)}"); return a
    except Exception as e: log.warning(f"Interenchères: {e}"); return []

def scrape_autobid():
    try:
        r = get_url(f"https://www.autobid.de/fr/recherche?priceTo={PRIX_MAX}&priceFrom={PRIX_MIN}&mileageTo={KM_MAX}&country=FR&sort=date_desc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/fr/voiture/"))[:20]:
            href = item.get("href","")
            m2 = re.search(r"/fr/voiture/([^/]+)", href)
            if not m2: continue
            text = item.get_text(" ", strip=True)
            out.append(build("ab_"+m2.group(1), "🔨 Autobid", text, extraire_prix(text), extraire_km(text), extraire_annee(text), "https://www.autobid.de"+href, "enchere"))
        log.info(f"   Autobid: {len(out)}"); return out
    except Exception as e: log.warning(f"Autobid: {e}"); return []

def scrape_drouot():
    try:
        a = _parse("https://www.drouot.com/lots?search=voiture&category=vehicules",
                   r"/lot", "https://www.drouot.com", "🎪 Drouot", "enchere", 15)
        log.info(f"   Drouot: {len(a)}"); return a
    except Exception as e: log.warning(f"Drouot: {e}"); return []

def scrape_domaines():
    try:
        a = _parse("https://encheres-domaine.gouv.fr/lot/liste?nature=1&famille=0201&tri=DateCreationDesc",
                   r"/lot/detail", "https://encheres-domaine.gouv.fr", "🏛️ Domaines État 🇫🇷", "enchere_etat", 15)
        log.info(f"   Domaines: {len(a)}"); return a
    except Exception as e: log.warning(f"Domaines: {e}"); return []

def scrape_commissaires():
    try:
        a = _parse("https://www.commissaires-justice.fr/ventes-aux-encheres/vehicules",
                   r"/lot|/vehicule|/voiture", "https://www.commissaires-justice.fr", "⚖️ Commissaires", "enchere_etat", 15)
        log.info(f"   Commissaires: {len(a)}"); return a
    except Exception as e: log.warning(f"Commissaires: {e}"); return []

def scrape_leboncoin():
    try:
        r = get_url(f"https://www.leboncoin.fr/recherche?category=2&price={PRIX_MIN}-{PRIX_MAX}&locations=France&sort=time&order=desc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/voitures/\d+"))[:25]:
            href = item.get("href","")
            m2 = re.search(r"/(\d+)", href)
            if not m2: continue
            text = item.get_text(" ", strip=True)
            out.append(build("lbc_"+m2.group(1), "🟠 LeBonCoin", text, extraire_prix(text), extraire_km(text), extraire_annee(text), "https://www.leboncoin.fr"+href, "occasion"))
        log.info(f"   LeBonCoin: {len(out)}"); return out
    except Exception as e: log.warning(f"LeBonCoin: {e}"); return []

def scrape_lacentrale():
    try:
        r = get_url(f"https://www.lacentrale.fr/listing?mileageMax={KM_MAX}&priceMin={PRIX_MIN}&priceMax={PRIX_MAX}&sortBy=creationDate&sortOrder=desc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/voiture-occasion"))[:20]:
            href = item.get("href","")
            if not href: continue
            text = item.get_text(" ", strip=True)
            prix = extraire_prix(text)
            if not prix: continue
            url_f = "https://www.lacentrale.fr" + href if href.startswith("/") else href
            out.append(build("lc_"+hid(href), "🔵 La Centrale", text, prix, extraire_km(text), extraire_annee(text), url_f, "occasion"))
        log.info(f"   LaCentrale: {len(out)}"); return out
    except Exception as e: log.warning(f"LaCentrale: {e}"); return []

def scrape_autoscout():
    try:
        r = get_url(f"https://www.autoscout24.fr/lst?sort=age&desc=1&ustate=N%2CU&size=20&priceto={PRIX_MAX}&pricefrom={PRIX_MIN}&mileageto={KM_MAX}&cy=F&atype=C")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/offres/"))[:20]:
            href = item.get("href","")
            if not href: continue
            text = item.get_text(" ", strip=True)
            prix = extraire_prix(text)
            if not prix: continue
            url_f = "https://www.autoscout24.fr" + href if href.startswith("/") else href
            out.append(build("as_"+hid(href), "🟡 AutoScout24", text, prix, extraire_km(text), extraire_annee(text), url_f, "occasion"))
        log.info(f"   AutoScout24: {len(out)}"); return out
    except Exception as e: log.warning(f"AutoScout: {e}"); return []

def scrape_reezocar():
    try:
        r = get_url(f"https://www.reezocar.com/voiture/occasion/?price_max={PRIX_MAX}&price_min={PRIX_MIN}&km_max={KM_MAX}&sort=date_desc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/voiture/"))[:15]:
            href = item.get("href","")
            if not href or "/occasion/" in href: continue
            text = item.get_text(" ", strip=True)
            prix = extraire_prix(text)
            if not prix: continue
            url_f = "https://www.reezocar.com" + href if href.startswith("/") else href
            out.append(build("rz_"+hid(href), "🔍 Reezocar", text, prix, extraire_km(text), extraire_annee(text), url_f, "occasion"))
        log.info(f"   Reezocar: {len(out)}"); return out
    except Exception as e: log.warning(f"Reezocar: {e}"); return []

def scrape_gpa26():
    try:
        r = get_url("https://revente.gpa26.com/fr/")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for link in soup.find_all("a", href=re.compile(r"/fr/\d{5,}"))[:25]:
            href = link["href"]
            m2 = re.search(r"/fr/(\d+)", href)
            if not m2: continue
            text = link.get_text(" ", strip=True)
            out.append(build("gpa_"+m2.group(1), "⚫ GPA26 Pro", text, extraire_prix(text), extraire_km(text), extraire_annee(text), "https://revente.gpa26.com"+href, "pro"))
        log.info(f"   GPA26: {len(out)}"); return out
    except Exception as e: log.warning(f"GPA26: {e}"); return []

def scrape_paruvendu():
    try:
        a = _parse(f"https://www.paruvendu.fr/voiture-occasion/toutes-marques/?px1={PRIX_MIN}&px2={PRIX_MAX}&km2={KM_MAX}&tri=date_desc",
                   r"/voiture-occasion/[^/]+/[^/]+/\d", "https://www.paruvendu.fr", "🟣 ParuVendu", "occasion", 15)
        log.info(f"   ParuVendu: {len(a)}"); return a
    except Exception as e: log.warning(f"ParuVendu: {e}"); return []

def scrape_vivastreet():
    try:
        a = _parse(f"https://www.vivastreet.com/voitures/france?prix_min={PRIX_MIN}&prix_max={PRIX_MAX}&tri=date",
                   r"/annonce/\d+|/voitures/\d+", "https://www.vivastreet.com", "🟤 VivaStreet", "occasion", 15)
        log.info(f"   VivaStreet: {len(a)}"); return a
    except Exception as e: log.warning(f"VivaStreet: {e}"); return []

def scrape_aramisauto():
    try:
        a = _parse(f"https://www.aramisauto.com/voitures-occasion/?priceMin={PRIX_MIN}&priceMax={PRIX_MAX}&mileageMax={KM_MAX}&sortBy=price_asc",
                   r"/voitures-occasion/[^/]+/[^/]+/\d", "https://www.aramisauto.com", "🔶 Aramisauto", "pro", 15)
        log.info(f"   Aramisauto: {len(a)}"); return a
    except Exception as e: log.warning(f"Aramisauto: {e}"); return []

def scrape_zoomcar():
    try:
        a = _parse(f"https://www.zoomcar.fr/voiture-occasion/?prix_max={PRIX_MAX}&km_max={KM_MAX}",
                   r"/voiture/\d+|/annonce/\d+", "https://www.zoomcar.fr", "🟢 ZoomCar", "pro", 15)
        log.info(f"   ZoomCar: {len(a)}"); return a
    except Exception as e: log.warning(f"ZoomCar: {e}"); return []

def scrape_caroom():
    try:
        a = _parse(f"https://www.caroom.fr/annonces?price_to={PRIX_MAX}&price_from={PRIX_MIN}&mileage_to={KM_MAX}&sort=date_desc",
                   r"/annonce/\d+|/voiture/\d+", "https://www.caroom.fr", "🔵 Caroom", "occasion", 15)
        log.info(f"   Caroom: {len(a)}"); return a
    except Exception as e: log.warning(f"Caroom: {e}"); return []

SCRAPERS = [
    scrape_alcopa, scrape_bca, scrape_agorastore, scrape_interencheres,
    scrape_autobid, scrape_drouot, scrape_domaines, scrape_commissaires,
    scrape_gpa26, scrape_leboncoin, scrape_lacentrale, scrape_autoscout,
    scrape_reezocar, scrape_paruvendu, scrape_vivastreet, scrape_aramisauto,
    scrape_zoomcar, scrape_caroom,
]

def scan_tout():
    out = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(s): s.__name__ for s in SCRAPERS}
        for f in as_completed(futures):
            try: out.extend(f.result())
            except Exception as e: log.warning(f"Scraper: {e}")
    return out

# ══════════════════════════════════════════════════════════════
# ANALYSE IA — MAX Expert
# ══════════════════════════════════════════════════════════════
def analyser_ia(a, score_pre):
    if not ANTHROPIC_KEY: return None

    mot = analyser_moteur(a["titre"])
    infos_moteur = ""
    if mot:
        infos_moteur = f"""
MOTEUR IDENTIFIÉ : {mot[0]}
Fiabilité : {mot[1]}/10  |  Score ajustement : {mot[1]:+d}
Conseil expert : {mot[3]}"""

    type_label = {
        "enchere":      "ENCHÈRE — prix marteau 30-50% sous marché souvent",
        "enchere_etat": "ENCHÈRE JUDICIAIRE/ÉTAT — mise à prix très basse, peu de concurrence = meilleures affaires",
        "pro":          "VENTE PRO — véhicule traçable, historique dispo",
        "occasion":     "OCCASION particulier ou pro",
    }.get(a.get("type",""), "")

    prompt = f"""Tu es MAX, le meilleur expert automobile achat-revente au monde. 50 ans d'expérience. 50 000 véhicules vendus. Tu connais L'Argus par coeur.

MISSION : dire si c'est une PÉPITE pour un marchand auto-entrepreneur qui achète bas pour revendre avec marge.

ANNONCE :
Titre: {a['titre']}
Prix demandé: {a['prix']}€
Kilométrage: {a['km'] or '?'} km
Année: {a['annee'] or '?'}
Carburant détecté: {a.get('_carburant','?')}
Boîte détectée: {a.get('_boite','?')}
Source: {a['source']} — {type_label}
Pré-score MAX: {score_pre}/100
{infos_moteur}

CADRE FINANCIER AUTO-ENTREPRENEUR :
- Budget max: {PRIX_MAX}€
- Cotisations AE: {COTISATIONS_AE}% sur prix revente
- TVA sur marge (régime VO): sur marge brute uniquement
- Marge nette minimum souhaitée: {MARGE_NETTE_MIN}€ après AE+TVA
- Décote minimum vs Argus: {DECOTE_MIN}%

RÈGLE ABSOLUE : Ne valider comme pépite QUE si :
1. Prix demandé < Argus d'au moins {DECOTE_MIN}%
2. Marge nette après AE+TVA ≥ {MARGE_NETTE_MIN}€
3. Pas de défaut rédhibitoire non chiffré

Réponds UNIQUEMENT en JSON valide :
{{
  "score": <0-100>,
  "est_pepite": <true/false — respecter les 3 règles ci-dessus>,
  "verdict": "<AFFAIRE EXCEPTIONNELLE 💎 / EXCELLENTE AFFAIRE 🔥 / BONNE AFFAIRE ✅ / PASSABLE ⚠️ / À ÉVITER ❌>",
  "prix_argus": <cote Argus réelle €>,
  "decote_pct": <% décote vs Argus>,
  "economies_argus": <économie vs Argus €>,
  "prix_revente_bas": <prix revente prudent €>,
  "prix_revente_haut": <prix revente optimiste €>,
  "marge_brute": <marge brute €>,
  "tva_marge": <TVA sur marge à reverser €>,
  "cotisations_ae": <cotisations AE à payer €>,
  "marge_nette": <marge nette réelle en poche €>,
  "roi": <ROI % net>,
  "prix_achat_max": <ne jamais dépasser ce prix €>,
  "delai_revente": "<ex: 1-2 semaines>",
  "points_forts": "<3 arguments concrets>",
  "risques": "<risques chiffrés>",
  "negociation": "<tactique de négociation précise>",
  "verifications": "<5 points précis à vérifier à la visite>",
  "conseil_max": "<verdict MAX en 1 phrase directe et franche>",
  "urgence": <true si score>=88 et marge_nette>1000>
}}"""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":700,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=30)
        txt = r.json()["content"][0]["text"].strip()
        txt = re.sub(r"```json|```","",txt).strip()
        return json.loads(txt)
    except Exception as e:
        log.warning(f"IA: {e}"); return None

# ══════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════
def send(msg, urgente=False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML",
                  "disable_web_page_preview":False,"disable_notification":not urgente},
            timeout=10)
    except Exception as e: log.error(f"Telegram: {e}")

def format_alerte(a, an):
    score   = an.get("score",0)
    emoji,_ = get_note_pepite(score)
    barre   = barre_score(score)
    urgence = an.get("urgence",False)

    carb_e  = {"DIESEL":"⛽","ESSENCE":"🔴","HYBRIDE":"🟢","ELECTRIQUE":"⚡","GPL":"🟡","ETHANOL":"🌿"}.get(a.get("_carburant",""),"⛽")
    boite_e = {"MANUELLE":"🔧","AUTOMATIQUE":"🔄","DSG":"⚙️","EDC":"⚙️","CVT":"🔄","ROBOTISEE":"⚙️"}.get(a.get("_boite",""),"🔧")

    km_str  = f" · {a['km']:,}km".replace(",",".") if a.get("km") else ""
    an_str  = f" · {a['annee']}" if a.get("km") else ""

    entete = ("🚨 <b>ALERTE URGENTE</b> 🚨\n" if urgence else "") + \
             f"{emoji} <b>{an.get('verdict','')}</b>\n" + \
             f"📊 NOTE MAX : <b>{score}/100</b>  <code>[{barre}]</code>"

    # Moteur
    mot = analyser_moteur(a["titre"])
    mot_line = ""
    if mot:
        fid_e = "✅" if mot[1] > 0 else "⚠️" if mot[1] > -15 else "❌"
        mot_line = f"\n🔧 Moteur: <b>{mot[0][:25]}</b> {fid_e} Fiabilité {mot[2]}/10\n<i>{mot[3][:80]}</i>\n"

    # Détail score
    detail = a.get("_detail_score",{})
    detail_str = " · ".join([f"{k}:{v}" for k,v in list(detail.items())[:4]])

    return (
        f"{entete}\n\n"
        f"🚘 <b>{a['titre'][:70]}</b>{an_str}{km_str}\n"
        f"{carb_e} {a.get('_carburant','?')} · {boite_e} {a.get('_boite','?')}\n"
        f"{mot_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💶 Prix demandé :  <b>{a['prix']:,}€</b>\n".replace(",",".") +
        f"📊 Cote Argus :    <b>{an.get('prix_argus',0):,}€</b>\n".replace(",",".") +
        f"🎯 Sous Argus :    <b>-{an.get('economies_argus',0):,}€  (-{an.get('decote_pct',0)}%)</b> ✅\n".replace(",",".") +
        f"💰 Revente :       <b>{an.get('prix_revente_bas',0):,}€ → {an.get('prix_revente_haut',0):,}€</b>\n".replace(",",".") +
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>VOTRE MARGE RÉELLE (AE) :</b>\n"
        f"   Marge brute :        +{an.get('marge_brute',0):,}€\n".replace(",",".") +
        f"   TVA sur marge :      -{an.get('tva_marge',0):,}€\n".replace(",",".") +
        f"   Cotis. AE {COTISATIONS_AE}% :    -{an.get('cotisations_ae',0):,}€\n".replace(",",".") +
        f"   ➡️ <b>NET EN POCHE : +{an.get('marge_nette',0):,}€</b>\n".replace(",",".") +
        f"🔄 ROI : <b>{an.get('roi',0)}%</b>  ·  ⏱️ Délai : <b>{an.get('delai_revente','?')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <i>{an.get('points_forts','')}</i>\n"
        f"⚠️ <i>{an.get('risques','')}</i>\n\n"
        f"🤝 Prix max à payer : <b>{an.get('prix_achat_max',0):,}€</b>\n".replace(",",".") +
        f"💬 Négo : <i>{an.get('negociation','')}</i>\n"
        f"🔍 À vérifier : <i>{an.get('verifications','')}</i>\n\n"
        f"💡 <b>MAX :</b> <i>{an.get('conseil_max','')}</i>\n\n"
        f"📍 {a['source']}\n"
        f"🧮 <i>{detail_str}</i>\n"
        f"🔗 <a href='{a['url']}'>👉 Voir l'annonce →</a>\n"
        f"⏰ {datetime.now().strftime('%d/%m à %H:%M:%S')}"
    )

def format_rapport(stats, pepites):
    top3 = sorted(pepites, key=lambda x: x.get("score",0), reverse=True)[:3]
    return (
        f"📊 <b>RAPPORT QUOTIDIEN — MAX</b>\n"
        f"📅 {datetime.now().strftime('%A %d/%m/%Y à %Hh%M')}\n\n"
        f"🔍 Scannées :    <b>{stats['scanne']:,}</b>\n".replace(",",".") +
        f"🧠 Analysées :   <b>{stats['analyse']:,}</b>\n".replace(",",".") +
        f"🔥 Pépites :     <b>{stats['pepites']}</b>\n"
        f"💰 Marge totale: <b>{stats['marge']:,}€</b>\n\n".replace(",",".") +
        f"<b>🏆 Top 3 :</b>\n" +
        "".join([f"• {p.get('titre','')[:40]} — {p.get('score',0)}/100 · +{p.get('marge_nette',0):,}€\n".replace(",",".") for p in top3]) +
        f"\n⚡ Scan toutes les {CHECK_INTERVAL}s · 18 sources actives\n"
        f"✅ <i>MAX veille pour vous 24h/24 🔥</i>"
    )

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    log.info("="*60)
    log.info("💎 MAX v5 ULTRA — LE MEILLEUR CHASSEUR DE PÉPITES")
    log.info(f"   18 sources · Scan toutes les {CHECK_INTERVAL}s · Parallèle")
    log.info(f"   Prix: {PRIX_MIN}€→{PRIX_MAX}€ · KM: {KM_MIN}-{KM_MAX} · {ANNEE_MIN}-{ANNEE_MAX}")
    log.info(f"   Score min: {SCORE_MIN}/100 · Marge nette min: {MARGE_NETTE_MIN}€ · Décote min: {DECOTE_MIN}%")
    log.info(f"   AE: {COTISATIONS_AE}% · TVA marge: {TVA_MARGE}")
    log.info("="*60)

    known     = load_known()
    stats     = load_stats()
    pepites   = load_pepites()
    checks    = 0
    dernier_rapport = datetime.now().replace(hour=0, minute=0, second=0)

    send(
        "💎 <b>MAX v5 ULTRA — DÉMARRÉ</b> 💎\n\n"
        "📡 <b>18 sources surveillées en parallèle</b>\n\n"
        "<b>🔨 Enchères :</b> Alcopa · BCA · Agorastore\n"
        "Interenchères · Autobid · Drouot\n"
        "Domaines État 🇫🇷 · Commissaires judiciaires\n\n"
        "<b>🚗 Occasions :</b> GPA26 · LeBonCoin · La Centrale\n"
        "AutoScout24 · Reezocar · ParuVendu\n"
        "VivaStreet · Aramisauto · ZoomCar · Caroom\n\n"
        f"⚡ Scan toutes les <b>{CHECK_INTERVAL}s</b>\n"
        f"🎯 Filtre strict : score ≥{SCORE_MIN} · marge ≥{MARGE_NETTE_MIN}€ · décote ≥{DECOTE_MIN}%\n"
        f"💼 AE {COTISATIONS_AE}% · TVA marge déduits automatiquement\n"
        f"📋 Rapport toutes les <b>{RAPPORT_INTERVAL} minutes</b>\n\n"
        "✅ <b>MAX est en chasse — vous serez le PREMIER alerté ! 🔥</b>"
    )

    while True:
        checks += 1
        now = datetime.now()
        stats["checks"] = checks

        # Rapport toutes les X minutes
        if (now - dernier_rapport).total_seconds() >= RAPPORT_INTERVAL * 60:
            send(format_rapport(stats, pepites))
            dernier_rapport = now

        log.info(f"\n{'='*55}")
        log.info(f"[Check #{checks}] {now.strftime('%H:%M:%S')} — Scan 18 sources...")

        toutes = scan_tout()
        stats["scanne"] += len(toutes)
        log.info(f"   Total: {len(toutes)} annonces")

        # Filtre + dédup
        nouvelles = [a for a in toutes if a["id"] not in known and matches_filter(a)]
        log.info(f"   → {len(nouvelles)} nouvelles après filtres")

        # Pré-score + tri (meilleures en premier)
        for a in nouvelles:
            a["_score_pre"] = scorer(a)
        nouvelles.sort(key=lambda x: x["_score_pre"], reverse=True)

        for a in nouvelles:
            known.add(a["id"])
            src_k = re.sub(r"[^\w]","",a["source"])[:12]
            stats["sources"][src_k] = stats["sources"].get(src_k,0) + 1

            score_pre = a.get("_score_pre", 50)

            # Pré-filtre rapide
            if score_pre < (SCORE_MIN - 20):
                log.info(f"   ⏭  Pré-score {score_pre} trop bas — ignoré")
                continue
            if not a["prix"] and a["type"] not in ["enchere","enchere_etat"]:
                continue

            if ANTHROPIC_KEY:
                log.info(f"   🧠 [{score_pre}/100] {a['titre'][:45]}...")
                analyse = analyser_ia(a, score_pre)
                stats["analyse"] += 1

                if analyse:
                    score       = analyse.get("score", 0)
                    est_pepite  = analyse.get("est_pepite", False)
                    marge_nette = analyse.get("marge_nette", 0)
                    decote      = analyse.get("decote_pct", 0)
                    prix_argus  = analyse.get("prix_argus", 0)
                    urgence     = analyse.get("urgence", False) or score >= SCORE_URGENTE

                    # ── CRITÈRES STRICTS ──
                    ok_score   = score >= SCORE_MIN
                    ok_marge   = marge_nette >= MARGE_NETTE_MIN
                    ok_decote  = decote >= DECOTE_MIN
                    ok_argus   = prix_argus > 0 and a["prix"] < prix_argus
                    ok_ia      = est_pepite

                    if ok_score and ok_marge and ok_decote and ok_argus and ok_ia:
                        log.info(f"   💎 PÉPITE ! {score}/100 · -{decote}% Argus · +{marge_nette}€ net")
                        stats["pepites"] += 1
                        stats["marge"]    = stats.get("marge",0) + marge_nette

                        pepite_rec = {**analyse, "titre":a["titre"][:60],
                                      "url":a["url"], "source":a["source"],
                                      "date":now.isoformat()}
                        pepites.append(pepite_rec)
                        save_pepites(pepites)

                        send(format_alerte(a, analyse), urgente=urgence)
                        time.sleep(0.3)
                    else:
                        raisons = []
                        if not ok_score:  raisons.append(f"score {score}<{SCORE_MIN}")
                        if not ok_marge:  raisons.append(f"marge {marge_nette}€<{MARGE_NETTE_MIN}€")
                        if not ok_decote: raisons.append(f"décote {decote}%<{DECOTE_MIN}%")
                        if not ok_argus:  raisons.append(f"prix≥Argus")
                        if not ok_ia:     raisons.append("IA: pas pépite")
                        log.info(f"   ⏭  Rejeté: {' | '.join(raisons)}")

                else:
                    # Sans analyse réussie — enchère État uniquement
                    if a.get("type") == "enchere_etat" and a["prix"] > 0:
                        send(
                            f"🏛️ <b>SAISIE ÉTAT — À analyser manuellement</b>\n"
                            f"🚘 {a['titre'][:60]}\n"
                            f"💶 Mise à prix: <b>{a['prix']:,}€</b>\n".replace(",",".") +
                            f"📍 {a['source']}\n"
                            f"🔗 <a href='{a['url']}'>👉 Voir →</a>"
                        )
            else:
                # Sans clé IA — pré-score uniquement
                if score_pre >= SCORE_MIN:
                    emoji, label = get_note_pepite(score_pre)
                    send(
                        f"{emoji} <b>{label}</b>\n"
                        f"📊 NOTE MAX : <b>{score_pre}/100</b>  <code>[{barre_score(score_pre)}]</code>\n\n"
                        f"🚘 {a['titre'][:60]}\n"
                        f"💶 {a['prix']:,}€{' · '+str(a['km'])+'km' if a.get('km') else ''}\n".replace(",",".") +
                        f"⛽ {a.get('_carburant','?')} · 🔧 {a.get('_boite','?')}\n"
                        f"📍 {a['source']}\n"
                        f"🔗 <a href='{a['url']}'>👉 Voir →</a>\n"
                        f"<i>⚠️ Ajoutez ANTHROPIC_KEY pour l'analyse MAX complète</i>"
                    )

        save_known(known)
        save_stats(stats)
        log.info(f"   ✅ Check #{checks} — prochaine vérif dans {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
