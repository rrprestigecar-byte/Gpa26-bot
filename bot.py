
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
