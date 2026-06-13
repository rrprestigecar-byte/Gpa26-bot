import os, time, json, logging, requests, re, hashlib, random, unicodedata
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# CONFIG — Variables Railway
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_KEY", "")

NTFY_TOPIC       = os.environ.get("NTFY_TOPIC", "")
NTFY_URL         = os.environ.get("NTFY_URL", "https://ntfy.sh")
DISCORD_WEBHOOK  = os.environ.get("DISCORD_WEBHOOK", "")
EMAIL_DEST       = os.environ.get("EMAIL_DEST", "rrprestigecar@hotmail.com")
EMAIL_SMTP_USER  = os.environ.get("EMAIL_SMTP_USER", "")
EMAIL_SMTP_PASS  = os.environ.get("EMAIL_SMTP_PASS", "")

CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "30"))
PRIX_MIN         = int(os.environ.get("PRIX_MIN", "500"))
PRIX_MAX         = int(os.environ.get("PRIX_MAX", "15000"))
KM_MIN           = int(os.environ.get("KM_MIN", "0"))
KM_MAX           = int(os.environ.get("KM_MAX", "250000"))
ANNEE_MIN        = int(os.environ.get("ANNEE_MIN", "2005"))
ANNEE_MAX        = int(os.environ.get("ANNEE_MAX", "2025"))

CARBURANTS_INCLUS = [c.strip().upper() for c in os.environ.get("CARBURANTS_INCLUS","").split(",") if c.strip()]
CARBURANTS_EXCLUS = [c.strip().upper() for c in os.environ.get("CARBURANTS_EXCLUS","").split(",") if c.strip()]
BOITES_INCLUSES   = [b.strip().upper() for b in os.environ.get("BOITES_INCLUSES","").split(",") if b.strip()]
BOITES_EXCLUES    = [b.strip().upper() for b in os.environ.get("BOITES_EXCLUES","DSG,EDC,ROBOTISEE").split(",") if b.strip()]
MARQUES           = [m.strip().upper() for m in os.environ.get("MARQUES","").split(",") if m.strip()]
MARQUES_BLACKLIST = [m.strip().upper() for m in os.environ.get("MARQUES_BLACKLIST","MICROCAR,CHATENET,LIGIER,AIXAM,BELLIER").split(",") if m.strip()]
MOTS_CLES         = [m.strip().lower() for m in os.environ.get("MOTS_CLES","").split(",") if m.strip()]

SCORE_MIN        = int(os.environ.get("SCORE_MIN", "45"))
MARGE_NETTE_MIN  = int(os.environ.get("MARGE_NETTE_MIN", "400"))
DECOTE_MIN       = int(os.environ.get("DECOTE_MIN", "10"))
SCORE_URGENTE    = int(os.environ.get("SCORE_URGENTE", "88"))
COTISATIONS_AE   = float(os.environ.get("COTISATIONS_AE", "12.3"))
TVA_MARGE        = os.environ.get("TVA_MARGE", "true").lower() == "true"
RAPPORT_INTERVAL = int(os.environ.get("RAPPORT_INTERVAL", "240"))
HEARTBEAT_MAX    = int(os.environ.get("HEARTBEAT_MAX", "30"))
MAX_WORKERS      = int(os.environ.get("MAX_WORKERS", "10"))

# ══════════════════════════════════════════════════════════════
# PERSISTANCE
# ══════════════════════════════════════════════════════════════
_vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data")
DATA_DIR     = Path(_vol if os.path.exists(_vol) else ".")
KNOWN_FILE   = DATA_DIR / "known_max.json"
STATS_FILE   = DATA_DIR / "stats_max.json"
PEPITES_FILE = DATA_DIR / "pepites_max.json"
SUIVI_FILE   = DATA_DIR / "suivi_max.json"
CONFIG_FILE  = DATA_DIR / "config_max.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("MAX")

# ══════════════════════════════════════════════════════════════
# CONFIG DYNAMIQUE TELEGRAM
# ══════════════════════════════════════════════════════════════
_cfg = {}

def load_config_runtime():
    global _cfg
    try:
        with open(CONFIG_FILE) as f: _cfg = json.load(f)
    except: _cfg = {}

def save_config_runtime():
    try:
        with open(CONFIG_FILE, "w") as f: json.dump(_cfg, f, ensure_ascii=False)
    except Exception as e: log.warning(f"Config save: {e}")

def cfg(key, default): return _cfg.get(key, default)
def is_paused(): return _cfg.get("paused", False)

# ══════════════════════════════════════════════════════════════
# USER AGENTS + ANTI-BAN
# ══════════════════════════════════════════════════════════════
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
]
_ua = 0

def get_headers():
    global _ua
    _ua = (_ua + 1) % len(UA_LIST)
    return {
        "User-Agent": UA_LIST[_ua],
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Referer": "https://www.google.fr/",
    }

def get_url(url, timeout=15, retries=3, delay_min=1.0, delay_max=3.5):
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(delay_min, delay_max))
            r = requests.get(url, headers=get_headers(), timeout=timeout)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning(f"Rate limited — attente {wait}s")
                time.sleep(wait); continue
            if r.status_code in (403, 503):
                log.warning(f"Bloqué {r.status_code} — tentative {attempt+1}/{retries}")
                time.sleep(5 * (attempt + 1)); continue
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            log.warning(f"Timeout ({url[:50]}) — tentative {attempt+1}/{retries}")
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            log.warning(f"Réseau ({url[:50]}): {e}")
            time.sleep(2 * (attempt + 1))
    return None

# ══════════════════════════════════════════════════════════════
# VÉRIFICATION STOCK EN TEMPS RÉEL
# ══════════════════════════════════════════════════════════════
MOTS_INDISPO = [
    "cette annonce n'est plus disponible", "annonce expirée", "annonce supprimée",
    "vendu", "sold", "this listing is no longer", "introuvable", "page not found",
    "lot terminé", "vente terminée", "adjugé", "plus disponible", "retiré"
]

def verifier_stock(a):
    try:
        r = get_url(a["url"], timeout=8, retries=1, delay_min=0.3, delay_max=0.8)
        if not r: return True
        texte = r.text.lower()
        if any(m in texte for m in MOTS_INDISPO):
            log.info(f"   ⏭  Stock épuisé : {a['titre'][:40]}")
            return False
        return True
    except:
        return True

# ══════════════════════════════════════════════════════════════
# BASE MOTEURS
# ══════════════════════════════════════════════════════════════
MOTEURS_DB = {
    "EP6|1.6 THP|PRINCE":           (-30, 2, "Chaîne distrib fragile → casse 1500-3000€. Fuyez avant 2016."),
    "1.2 TCE 115|1.2 TCE 120":      (-20, 3, "Joint culasse fréquent → 800-2000€. Rappels 2014-2018."),
    "N47|116D|118D|120D|2.0D BMW":  (-30, 2, "Chaîne côté boîte → dépose moteur 3000-5000€. Bannir."),
    "N20":                           (-15, 4, "Chaîne BMW côté boîte + pompe eau plastique. Risqué."),
    "OM651|220 CDI|200 CDI MERC":   (-20, 3, "Injecteurs Piezo + swirl flaps → 2000-5000€."),
    "M271|KOMPRESSOR":               (-10, 5, "Chaîne côté boîte Mercedes. Vérifier bruit démarrage."),
    "1.4 TSI DSG|CAXA|CTHD":        (-20, 3, "DSG7 embrayage sec défaillant → 700-2000€."),
    "2.0 TFSI|BWE|BWT|AXX":         (-15, 4, "Consommation huile 1L/1000km. Recours collectif USA."),
    "TWINAIR|875CC|0.9 TWINAIR":    (-25, 2, "Catastrophe. Courroie 3ans, vibrations, conso × 2."),
    "1.0 ECOBOOST|FOXON":           (-15, 4, "Joint culasse avant 2016 → 800-1500€. OK après 2017."),
    "DV6|1.6 HDI 90|1.6 HDI 92":   (-10, 5, "EGR + FAP coûteux ville. Route = correct."),
    "1.2 PURETECH|EB2|EB4":         (-15, 4, "Courroie bain huile → casse moteur. OK après 2019."),
    "2.0 TDI M9R|2.0 DCI LAG":     (-10, 5, "Injecteurs Siemens fragiles >150k."),
    "A16DTH|1.6 CDTI OPEL":        (-8,  5, "Chaîne Opel problématique. Vérifier bruit démarrage."),
    "1.4 T-JET":                    (-5,  5, "Courroie obligatoire 60 000km. Turbine 170ch fragile."),
    "K9K|1.5 DCI|1.5DCI":          (+20, 9, "MEILLEUR diesel. Taxis 400 000km. Yeux fermés."),
    "2ZR-FXE|HYBRID TOYOTA|YARIS H|AURIS H|COROLLA H|PRIUS": (+25, 10, "Légendaire. 600 000km. PÉPITE ABSOLUE."),
    "1NZ-FE|1.5 VVTI TOYOTA":      (+18, 9, "Toyota increvable. JD Power N°1 fiabilité."),
    "1.9 TDI|ALH|AXR|AGR":         (+18, 9, "Légendaire VW. 500 000km réguliers."),
    "2.0 TDI CFHC|2.0 TDI 2009":   (+12, 7, "Bon TDI après 2009. Solide si entretien."),
    "K4M|K4J|1.6 16V RENAULT":     (+15, 8, "Excellent atmosphérique Renault. Simple et fiable."),
    "1.3 MULTIJET|MULTIJET 75":    (+12, 8, "Meilleur petit diesel Fiat. Fiable et éco."),
    "R18A|1.8 VTEC HONDA":         (+18, 9, "Honda 400 000km. Fiabilité japonaise absolue."),
    "G4FA|1.2 MPI HYUNDAI":        (+15, 8, "Coréens très fiables depuis 2010."),
    "U2|1.6 CRDI KIA|1.6 CRDI HYU":(+10, 7, "Bon diesel coréen. Fiabilité correcte."),
    "HR16|1.6 NISSAN|MR20":        (+15, 8, "Nissan fiable. Peu de problèmes connus."),
    "K12B|1.2 SUZUKI SWIFT":       (+15, 8, "Suzuki Swift = pépite légère. 300 000km rapportés."),
    "DW10|2.0 HDI|BLUEHDI 150":    (+5,  7, "Bon diesel PSA. Fiable si entretien. FAP à surveiller."),
    "1.6 TDI VW|CLHB|CAYC":       (+10, 7, "Bon petit TDI VW. Économique et fiable."),
    "HR15DE|1.5 DCI NISSAN":       (+12, 8, "Petit diesel Nissan fiable. Peu de problèmes."),
}

def analyser_moteur(titre):
    titre_up = titre.upper()
    best, best_len = None, 0
    for cles, (score, fid, conseil) in MOTEURS_DB.items():
        for cle in cles.split("|"):
            cle = cle.strip()
            if len(cle) > 3 and cle in titre_up and len(cle) > best_len:
                best = (cles.split("|")[0], score, fid, conseil)
                best_len = len(cle)
    return best

# ══════════════════════════════════════════════════════════════
# DÉTECTION CARBURANT / BOÎTE
# ══════════════════════════════════════════════════════════════
_DIESEL     = ["TDI","HDI","DCI","CDTI","TDCI","BLUEHDI","MULTIJET","CRDI","D4D","SDI","2.0D","1.5D","1.6D","1.9D","DIESEL"]
_HYBRIDE    = ["HYBRID","HYBRIDE","HEV","PHEV","E-HYBRID","RECHARGEABLE","PLUG-IN"]
_ELECTRIQUE = ["ELECTRIQUE","ELECTRIC","EV","BEV","ZOE","LEAF","IONIQ","E-TRON","MODEL 3","MEGANE E"]
_ESSENCE    = ["TSI","VTI","GTI","TFSI","TCE","PURETECH","MPI","16V","TURBO","ESSENCE","1.0","1.2","1.4","1.6 E","1.8","2.0 E"]
_GPL        = ["GPL","LPG","BIFUEL"]
_ETHANOL    = ["E85","ETHANOL","FLEX","FLEXFUEL"]
_DSG        = ["DSG","S-TRONIC","PDK"]
_EDC        = ["EDC","DCT","POWERSHIFT","EAT6","EAT8"]
_CVT        = ["CVT","XTRONIC","MULTITRONIC","LINEARTRONIC"]
_ROBOT      = ["ROBOTISEE","ROBOTISÉE","AMT","EASYTRONIC","SENSODRIVE"]
_AUTO       = ["AUTOMATIQUE","BVA","TIPTRONIC","AUTO "]
_MANUELLE   = ["MANUELLE","BVM","BV5","BV6","MT ","BOITE MANUELLE"]

def detecter_carburant(t):
    t = t.upper()
    if any(m in t for m in _ELECTRIQUE): return "ELECTRIQUE"
    if any(m in t for m in _HYBRIDE):    return "HYBRIDE"
    if any(m in t for m in _GPL):        return "GPL"
    if any(m in t for m in _ETHANOL):    return "ETHANOL"
    if any(m in t for m in _DIESEL):     return "DIESEL"
    if any(m in t for m in _ESSENCE):    return "ESSENCE"
    return "?"

def detecter_boite(t):
    t = t.upper()
    if any(m in t for m in _DSG):      return "DSG"
    if any(m in t for m in _EDC):      return "EDC"
    if any(m in t for m in _CVT):      return "CVT"
    if any(m in t for m in _ROBOT):    return "ROBOTISEE"
    if any(m in t for m in _AUTO):     return "AUTOMATIQUE"
    if any(m in t for m in _MANUELLE): return "MANUELLE"
    return "?"

# ══════════════════════════════════════════════════════════════
# FISCAL AE
# ══════════════════════════════════════════════════════════════
def calcul_ae(achat, revente):
    if revente <= achat: return {"brute":0,"tva":0,"cot":0,"nette":0,"roi":0}
    brute = revente - achat
    tva   = round(brute / 1.20 * 0.20) if TVA_MARGE else 0
    cot   = round(revente * COTISATIONS_AE / 100)
    nette = brute - tva - cot
    roi   = round((nette / achat) * 100, 1) if achat > 0 else 0
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
MOTS_SUSPECTS = ["accidenté","sinistre","epave","pièces","ne démarre","moteur hs",
                  "boite cassée","rouillé","inondé","grêle","brûlé","flood","hail"]
MOTS_POSITIFS = ["1er main","première main","faible km","peu kilométré","révisé",
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

    for m, b in MODELES_BONUS.items():
        if m in t_up:
            score += b; detail["modèle"] = f"+{b}pts ({m})"; break

    mot = analyser_moteur(a["titre"])
    if mot:
        score += mot[1]; detail["moteur"] = f"{mot[1]:+d}pts ({mot[0][:20]})"

    bs = {"enchere_etat":25,"enchere":15,"pro":10,"occasion":0}.get(a.get("type",""),0)
    if bs: score += bs; detail["source"] = f"+{bs}pts ({a['type']})"

    km = a.get("km",0)
    if km > 0:
        if km < 50000:    b=18
        elif km < 80000:  b=14
        elif km < 120000: b=10
        elif km < 160000: b=5
        elif km < 200000: b=2
        else:             b=-12
        score += b; detail["km"] = f"{b:+d}pts ({km:,}km)".replace(",",".")

    an = a.get("annee",0)
    if an > 0:
        age = 2024 - an
        if age <= 3:    b=15
        elif age <= 6:  b=12
        elif age <= 10: b=8
        elif age <= 15: b=4
        else:           b=-8
        score += b; detail["année"] = f"{b:+d}pts ({an})"

    carb = a.get("_carburant","?")
    cb = {"HYBRIDE":10,"ELECTRIQUE":8,"DIESEL":5,"GPL":3,"ESSENCE":2}.get(carb,0)
    if cb: score += cb; detail["carburant"] = f"+{cb}pts ({carb})"

    boite = a.get("_boite","?")
    bb = {"AUTOMATIQUE":8,"MANUELLE":3,"DSG":-8,"EDC":-5,"CVT":-3,"ROBOTISEE":-6}.get(boite,0)
    if bb: score += bb; detail["boîte"] = f"{bb:+d}pts ({boite})"

    for mot in MOTS_SUSPECTS:
        if mot in t_low:
            score -= 25; detail["⚠️suspect"] = f"-25pts ({mot})"; break

    bp = 0
    for mot in MOTS_POSITIFS:
        if mot in t_low and bp < 15: bp += 3
    if bp: score += bp; detail["positifs"] = f"+{bp}pts"

    if a.get("_cg_incluse"): score += 5; detail["CG"] = "+5pts"

    a["_detail_score"] = detail
    return max(0, min(100, score))

# ══════════════════════════════════════════════════════════════
# STOCKAGE — protégé contre corruption
# ══════════════════════════════════════════════════════════════
def load_json(f, default):
    try:
        with open(f) as fp: return json.load(fp)
    except: return default

def save_json(f, data):
    try:
        tmp = str(f) + ".tmp"
        with open(tmp, "w") as fp: json.dump(data, fp, ensure_ascii=False)
        os.replace(tmp, f)
    except Exception as e: log.warning(f"Save {f}: {e}")

def load_known():    return set(load_json(KNOWN_FILE, []))
def save_known(s):   save_json(KNOWN_FILE, list(s)[-10000:])
def load_stats():    return load_json(STATS_FILE, {"scanne":0,"analyse":0,"pepites":0,"marge":0,"checks":0,"sources":{}})
def save_stats(s):   save_json(STATS_FILE, s)
def load_pepites():  return load_json(PEPITES_FILE, [])
def save_pepites(p): save_json(PEPITES_FILE, p[-300:])
def load_suivi():    return load_json(SUIVI_FILE, {})
def save_suivi(s):   save_json(SUIVI_FILE, s)

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

def normaliser(t):
    t = t.lower()
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', t).strip()

def hid(s): return hashlib.md5(s.encode()).hexdigest()[:10]

def build(id, src, titre, prix, km, annee, url, typ="occasion"):
    a = {"id":id,"source":src,"titre":titre[:120],"prix":prix,"km":km,
         "annee":annee,"url":url,"type":typ}
    a["_carburant"]   = detecter_carburant(titre)
    a["_boite"]       = detecter_boite(titre)
    a["_titre_norm"]  = normaliser(titre)
    a["_cg_incluse"]  = False
    return a

# ══════════════════════════════════════════════════════════════
# FILTRE ANNONCES VENDUES
# ══════════════════════════════════════════════════════════════
MOTS_VENDU = ["vendu","sold","indisponible","réservé","reserve",
               "plus disponible","retiré","retire"]

def est_vendu(texte, soup_item=None):
    t = texte.lower()
    if any(m in t for m in MOTS_VENDU): return True
    if soup_item:
        classes = " ".join(soup_item.get("class", [])).lower()
        if any(m in classes for m in ["sold","vendu","unavailable","disabled"]): return True
        parent = soup_item.parent
        if parent:
            pc = " ".join(parent.get("class", [])).lower()
            if any(m in pc for m in ["sold","vendu","unavailable","disabled"]): return True
    return False

def matches_filter(a):
    if est_vendu(a["titre"]): return False
    px_min = cfg("prix_min", PRIX_MIN)
    px_max = cfg("prix_max", PRIX_MAX)
    km_max = cfg("km_max", KM_MAX)
    if px_max <= 0: px_max = PRIX_MAX  # Protection contre prix_max=0
    if a["prix"]  > 0 and a["prix"]  < px_min:  return False
    if a["prix"]  > 0 and a["prix"]  > px_max:  return False
    if a["km"]    > 0 and a["km"]    < KM_MIN:  return False
    if a["km"]    > 0 and a["km"]    > km_max:  return False
    if a["annee"] > 0 and a["annee"] < ANNEE_MIN: return False
    if a["annee"] > 0 and a["annee"] > ANNEE_MAX: return False
    t = a["titre"].upper()
    marques = cfg("marques", MARQUES)
    if marques and not any(m in t for m in marques): return False
    if any(m in t for m in MARQUES_BLACKLIST): return False
    if MOTS_CLES and not any(k in a["titre"].lower() for k in MOTS_CLES): return False
    carb = a.get("_carburant","?")
    if CARBURANTS_INCLUS and carb not in CARBURANTS_INCLUS and carb != "?": return False
    if CARBURANTS_EXCLUS and carb in CARBURANTS_EXCLUS: return False
    boite = a.get("_boite","?")
    if BOITES_INCLUSES and boite not in BOITES_INCLUSES and boite != "?": return False
    if BOITES_EXCLUES  and boite in BOITES_EXCLUES: return False
    return True

# ══════════════════════════════════════════════════════════════
# SUIVI PRIX EN BAISSE
# ══════════════════════════════════════════════════════════════
def checker_baisse_prix(a, suivi):
    url = a["url"]
    prix_actuel = a["prix"]
    if not prix_actuel: return
    if url in suivi:
        ancien = suivi[url]["prix"]
        if ancien > 0 and prix_actuel < ancien:
            baisse_pct = round((ancien - prix_actuel) / ancien * 100, 1)
            if baisse_pct >= 5:
                msg = (
                    f"📉 <b>BAISSE DE PRIX !</b>\n"
                    f"🚘 {a['titre'][:60]}\n"
                    f"💶 {ancien:,}€ → <b>{prix_actuel:,}€</b> (-{baisse_pct}%)\n".replace(",",".") +
                    f"📍 {a['source']}\n"
                    f"🔗 <a href='{url}'>👉 Voir →</a>"
                )
                send(msg)
                send_ntfy(f"📉 Baisse {baisse_pct}% — {a['titre'][:50]}",
                          f"{ancien}€ → {prix_actuel}€\n{url}")
                send_discord(f"📉 Baisse de prix — {a['titre'][:60]}",
                             f"**{ancien}€ → {prix_actuel}€** (-{baisse_pct}%)\n{url}")
    suivi[url] = {"prix": prix_actuel, "titre": a["titre"][:60], "date": datetime.now().isoformat()}

# ══════════════════════════════════════════════════════════════
# GPA26 — vérification détail
# ══════════════════════════════════════════════════════════════
def gpa26_check_detail(url_detail):
    try:
        r = get_url(url_detail, timeout=10, delay_min=0.5, delay_max=1.5)
        if not r: return False, False
        texte = r.text.lower()
        soup  = BeautifulSoup(r.text, "html.parser")
        tp    = soup.get_text(" ", strip=True).lower()
        vendu_part = any(m in tp for m in [
            "vendu à un particulier","vendu particulier","cédé à un particulier",
            "vendu en l'état","vendu sans garantie","vente directe particulier"
        ])
        carte_grise = any(m in tp for m in [
            "carte grise","carte grise incluse","carte grise remise",
            "cg incluse","titre de circulation","certificat d'immatriculation"
        ])
        return vendu_part, carte_grise
    except:
        return False, False

# ══════════════════════════════════════════════════════════════
# SCRAPERS — 18 SOURCES
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
        annonces.append(build(f"{src[:3]}_{hid(href)}", src, text,
                              extraire_prix(text), extraire_km(text),
                              extraire_annee(text), full, typ))
    return annonces

def scrape_alcopa():
    try:
        r = get_url(f"https://www.alcopa-auction.fr/recherche?prixMax={cfg('prix_max',PRIX_MAX)}&prixMin={cfg('prix_min',PRIX_MIN)}&kmMax={cfg('km_max',KM_MAX)}&tri=dateDesc")
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
        log.info(f"   Alcopa: {len(out)}"); return out
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
        r = get_url(f"https://www.autobid.de/fr/recherche?priceTo={cfg('prix_max',PRIX_MAX)}&priceFrom={cfg('prix_min',PRIX_MIN)}&mileageTo={cfg('km_max',KM_MAX)}&country=FR&sort=date_desc")
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
        r = get_url("https://www.leboncoin.fr/recherche?category=2&locations=Occitanie&price=0-3000&fuel=essence,diesel&sort=price&order=asc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/voitures/\d+"))[:25]:
            href = item.get("href","")
            m2 = re.search(r"/(\d+)", href)
            if not m2: continue
            text = item.get_text(" ", strip=True)
            if not text: continue
            out.append(build("lbc_"+m2.group(1), "🟠 LeBonCoin", text, extraire_prix(text), extraire_km(text), extraire_annee(text), "https://www.leboncoin.fr"+href, "occasion"))
        log.info(f"   LeBonCoin: {len(out)}"); return out
    except Exception as e: log.warning(f"LeBonCoin: {e}"); return []

def scrape_lacentrale():
    try:
        r = get_url(f"https://www.lacentrale.fr/listing?makesModelsCommercialNames=&yearMin={ANNEE_MIN}&mileageMax={cfg('km_max',KM_MAX)}&priceMin={cfg('prix_min',PRIX_MIN)}&priceMax={cfg('prix_max',PRIX_MAX)}&sortBy=priceAsc")
        if not r: return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for item in soup.find_all("a", href=re.compile(r"/auto-occasion/"))[:20]:
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
        r = get_url(f"https://www.autoscout24.fr/lst?sort=age&desc=1&ustate=N%2CU&size=20&priceto={cfg('prix_max',PRIX_MAX)}&pricefrom={cfg('prix_min',PRIX_MIN)}&mileageto={cfg('km_max',KM_MAX)}&cy=F&atype=C")
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
        r = get_url(f"https://www.reezocar.com/voiture/occasion/?price_max={cfg('prix_max',PRIX_MAX)}&price_min={cfg('prix_min',PRIX_MIN)}&km_max={cfg('km_max',KM_MAX)}&sort=date_desc")
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
        out, ignores = [], 0
        for link in soup.find_all("a", href=re.compile(r"/fr/\d{5,}"))[:40]:
            href = link["href"]
            m2 = re.search(r"/fr/(\d+)", href)
            if not m2: continue
            text = link.get_text(" ", strip=True)
            if not text: continue
            if est_vendu(text, link):
                ignores += 1; continue
            url_full = "https://revente.gpa26.com" + href
            annonce = build("gpa_"+m2.group(1), "⚫ GPA26 Pro", text,
                            extraire_prix(text), extraire_km(text),
                            extraire_annee(text), url_full, "pro")
            vendu_part, cg = gpa26_check_detail(url_full)
            if vendu_part:
                ignores += 1
                log.info(f"   GPA26 ignoré (particulier) : {text[:40]}")
                continue
            if cg:
                annonce["titre"] += " [CG incluse]"
                annonce["_cg_incluse"] = True
                log.info(f"   GPA26 ✅ CG incluse : {text[:40]}")
            out.append(annonce)
        log.info(f"   GPA26: {len(out)} dispo ({ignores} ignorés)"); return out
    except Exception as e: log.warning(f"GPA26: {e}"); return []

def scrape_paruvendu():
    try:
        a = _parse(f"https://www.paruvendu.fr/voiture-occasion/toutes-marques/annonceauto/annonceauto/?px1={cfg('prix_min',PRIX_MIN)}&px2={cfg('prix_max',PRIX_MAX)}&km2={cfg('km_max',KM_MAX)}&tri=date_desc",
                   r"/voiture-occasion/[^/]+/[^/]+/\d", "https://www.paruvendu.fr", "🟣 ParuVendu", "occasion", 15)
        log.info(f"   ParuVendu: {len(a)}"); return a
    except Exception as e: log.warning(f"ParuVendu: {e}"); return []

def scrape_vivastreet():
    try:
        a = _parse(f"https://www.vivastreet.com/voitures+france?prix_min={cfg('prix_min',PRIX_MIN)}&prix_max={cfg('prix_max',PRIX_MAX)}",
                   r"/annonce/\d+|/voitures/\d+", "https://www.vivastreet.com", "🟤 VivaStreet", "occasion", 15)
        log.info(f"   VivaStreet: {len(a)}"); return a
    except Exception as e: log.warning(f"VivaStreet: {e}"); return []

def scrape_aramisauto():
    try:
        a = _parse(f"https://www.aramisauto.com/voitures-occasion/?priceMin={cfg('prix_min',PRIX_MIN)}&priceMax={cfg('prix_max',PRIX_MAX)}&mileageMax={cfg('km_max',KM_MAX)}",
                   r"/voitures-occasion/[^/?]+/[^/?]+/\d", "https://www.aramisauto.com", "🔶 Aramisauto", "pro", 15)
        log.info(f"   Aramisauto: {len(a)}"); return a
    except Exception as e: log.warning(f"Aramisauto: {e}"); return []

def scrape_zoomcar():
    try:
        a = _parse(f"https://www.zoomcar.fr/voiture-occasion/?prix_max={cfg('prix_max',PRIX_MAX)}&km_max={cfg('km_max',KM_MAX)}",
                   r"/voiture/\d+|/annonce/\d+", "https://www.zoomcar.fr", "🟢 ZoomCar", "pro", 15)
        log.info(f"   ZoomCar: {len(a)}"); return a
    except Exception as e: log.warning(f"ZoomCar: {e}"); return []

def scrape_caroom():
    try:
        a = _parse(f"https://www.caroom.fr/recherche?budget_max={cfg('prix_max',PRIX_MAX)}&budget_min={cfg('prix_min',PRIX_MIN)}&kilometrage_max={cfg('km_max',KM_MAX)}&tri=date",
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

def dedup_cross_source(annonces):
    vus, out = {}, []
    for a in annonces:
        cle = f"{a['_titre_norm'][:40]}_{a['prix']}"
        if cle not in vus:
            vus[cle] = True; out.append(a)
    return out

# ══════════════════════════════════════════════════════════════
# ANALYSE IA — Gemini gratuit
# ══════════════════════════════════════════════════════════════
def analyser_ia(a, score_pre):
    if not ANTHROPIC_KEY: return None
    mot = analyser_moteur(a["titre"])
    infos_moteur = ""
    if mot:
        infos_moteur = f"\nMOTEUR : {mot[0]} | Fiabilité {mot[1]}/10\nConseil : {mot[3]}"
    type_label = {
        "enchere":      "ENCHÈRE — 30-50% sous marché",
        "enchere_etat": "ENCHÈRE JUDICIAIRE — mise à prix très basse",
        "pro":          "VENTE PRO — historique dispo",
        "occasion":     "OCCASION particulier ou pro",
    }.get(a.get("type",""), "")
    marge_min  = cfg("marge_min", MARGE_NETTE_MIN)
    decote_min = cfg("decote_min", DECOTE_MIN)
    px_max     = cfg("prix_max", PRIX_MAX)
    prompt = f"""Tu es MAX, expert automobile achat-revente. Tu connais L'Argus par coeur.
ANNONCE :
Titre: {a['titre']}
Prix: {a['prix']}€ | Km: {a['km'] or '?'} | Année: {a['annee'] or '?'}
Carburant: {a.get('_carburant','?')} | Boîte: {a.get('_boite','?')}
Source: {a['source']} — {type_label}
Pré-score: {score_pre}/100
{infos_moteur}
CG incluse: {a.get('_cg_incluse', False)}

CADRE AE : Budget max {px_max}€ | AE {COTISATIONS_AE}% | TVA marge {TVA_MARGE}
Marge nette min {marge_min}€ | Décote min {decote_min}%

RÈGLE : pépite seulement si prix < Argus -{decote_min}% ET marge nette ≥ {marge_min}€.

Réponds UNIQUEMENT en JSON valide :
{{"score":<0-100>,"est_pepite":<bool>,"verdict":"<label>","prix_argus":<€>,"decote_pct":<int>,"economies_argus":<€>,"prix_revente_bas":<€>,"prix_revente_haut":<€>,"marge_brute":<€>,"tva_marge":<€>,"cotisations_ae":<€>,"marge_nette":<€>,"roi":<float>,"prix_achat_max":<€>,"delai_revente":"<durée>","points_forts":"<3 args>","risques":"<risques>","negociation":"<tactique>","verifications":"<5 points>","conseil_max":"<1 phrase>","urgence":<bool>}}"""
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={ANTHROPIC_KEY}",
            headers={"Content-Type":"application/json"},
            json={"contents":[{"parts":[{"text":prompt}]}],
                  "generationConfig":{"maxOutputTokens":800,"temperature":0.1}},
            timeout=30)
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        txt = re.sub(r"```json|```","",txt).strip()
        return json.loads(txt)
    except Exception as e:
        log.warning(f"IA: {e}"); return None

# ══════════════════════════════════════════════════════════════
# NOTIFICATIONS — Telegram + Ntfy + Discord + Email
# ══════════════════════════════════════════════════════════════
def send(msg, urgente=False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML",
                  "disable_web_page_preview":False,"disable_notification":not urgente},
            timeout=10)
    except Exception as e: log.error(f"Telegram: {e}")

def send_ntfy(titre, message, urgente=False):
    if not NTFY_TOPIC: return
    try:
        requests.post(f"{NTFY_URL}/{NTFY_TOPIC}",
            data=message[:500].encode("utf-8"),
            headers={
                "Title": titre[:100],
                "Priority": "urgent" if urgente else "high",
                "Tags": "rotating_light,car" if urgente else "car,money",
                "Content-Type": "text/plain; charset=utf-8",
            }, timeout=10)
        log.info(f"   📲 Ntfy → {NTFY_TOPIC}")
    except Exception as e: log.warning(f"Ntfy: {e}")

def send_discord(titre, message, urgente=False):
    if not DISCORD_WEBHOOK: return
    try:
        requests.post(DISCORD_WEBHOOK, json={
            "username": "MAX — Chasseur de Pépites",
            "embeds": [{
                "title": f"{'🚨' if urgente else '💎'} {titre[:250]}",
                "description": message[:2000],
                "color": 0xFF0000 if urgente else 0xFFD700,
                "footer": {"text": f"MAX v7 · {datetime.now().strftime('%d/%m/%Y %H:%M')}"}
            }]
        }, timeout=10)
        log.info(f"   💬 Discord envoyé")
    except Exception as e: log.warning(f"Discord: {e}")

def send_email(sujet, corps_html):
    if not EMAIL_SMTP_USER or not EMAIL_SMTP_PASS: return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = sujet
        msg["From"]    = EMAIL_SMTP_USER
        msg["To"]      = EMAIL_DEST
        msg.attach(MIMEText(corps_html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as srv:
            srv.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASS)
            srv.sendmail(EMAIL_SMTP_USER, EMAIL_DEST, msg.as_string())
        log.info(f"   📧 Email → {EMAIL_DEST}")
    except Exception as e: log.warning(f"Email: {e}")

def notifier_tout(titre_court, msg_court, msg_long, msg_discord, urgente=False):
    """Envoie sur tous les canaux actifs."""
    send(msg_long, urgente=urgente)
    send_ntfy(titre_court, msg_court, urgente=urgente)
    send_discord(titre_court, msg_discord, urgente=urgente)
    sujet = f"💎 MAX — {titre_court}"
    corps = msg_long.replace("\n","<br>").replace("━","—")
    send_email(sujet, f"<pre style='font-family:Arial;font-size:13px'>{corps}</pre>")

# ══════════════════════════════════════════════════════════════
# COMMANDES TELEGRAM
# ══════════════════════════════════════════════════════════════
def get_updates(offset=0):
    if not TELEGRAM_TOKEN: return [], offset
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 1, "allowed_updates": ["message"]},
            timeout=10)
        if not r.ok: return [], offset
        updates = r.json().get("result", [])
        new_offset = updates[-1]["update_id"] + 1 if updates else offset
        return updates, new_offset
    except Exception as e:
        log.warning(f"getUpdates: {e}")
        return [], offset

def traiter_commande(texte, stats, pepites):
    t = texte.strip().lower()
    log.info(f"CMD Telegram: {t}")

    if t in ("/start","/aide","/help"):
        send(
            "🤖 <b>MAX v7 — Commandes</b>\n\n"
            "/pause — Pause le scan\n"
            "/resume — Reprendre\n"
            "/stats — Statistiques\n"
            "/top5 — Top 5 pépites\n"
            "/status — État complet\n"
            "/reset — Filtres par défaut\n\n"
            "<b>Filtres :</b>\n"
            "/prix_max 8000\n/prix_min 500\n/km_max 150000\n"
            "/marge_min 600\n/decote_min 15\n/score_min 55"
        )
    elif t == "/pause":
        _cfg["paused"] = True; save_config_runtime()
        send("⏸️ <b>MAX en pause.</b> /resume pour reprendre.")
    elif t == "/resume":
        _cfg["paused"] = False; save_config_runtime()
        send("▶️ <b>MAX reprend la chasse !</b> 🔥")
    elif t == "/stats":
        send(
            f"📊 <b>Stats MAX</b>\n"
            f"🔍 Scannées : {stats.get('scanne',0):,}\n".replace(",",".") +
            f"🧠 Analysées : {stats.get('analyse',0):,}\n".replace(",",".") +
            f"💎 Pépites : {stats.get('pepites',0)}\n"
            f"💰 Marge cumulée : {stats.get('marge',0):,}€\n".replace(",",".") +
            f"🔄 Checks : {stats.get('checks',0)}\n"
            f"⚡ Intervalle : {CHECK_INTERVAL}s\n"
            f"{'⏸️ EN PAUSE' if is_paused() else '✅ EN CHASSE'}"
        )
    elif t == "/top5":
        top = sorted(pepites, key=lambda x: x.get("score",0), reverse=True)[:5]
        if not top: send("Aucune pépite enregistrée.")
        else:
            msg = "🏆 <b>Top 5</b>\n\n"
            for i, p in enumerate(top, 1):
                msg += f"{i}. {p.get('titre','')[:40]}\n   {p.get('score',0)}/100 · +{p.get('marge_nette',0):,}€\n   <a href='{p.get('url','')}'>Voir →</a>\n\n".replace(",",".")
            send(msg)
    elif t == "/status":
        px_max = cfg('prix_max', PRIX_MAX)
        if px_max <= 0: px_max = PRIX_MAX
        send(
            f"🤖 <b>MAX v7 — Statut</b>\n"
            f"{'⏸️ EN PAUSE' if is_paused() else '✅ EN CHASSE 🔥'}\n\n"
            f"💶 Prix : {cfg('prix_min',PRIX_MIN)}€ → {px_max}€\n"
            f"🛣️ Km max : {cfg('km_max',KM_MAX):,}\n".replace(",",".") +
            f"📊 Score min : {cfg('score_min',SCORE_MIN)}\n"
            f"💰 Marge min : {cfg('marge_min',MARGE_NETTE_MIN)}€\n"
            f"📉 Décote min : {cfg('decote_min',DECOTE_MIN)}%\n"
            f"⚡ Intervalle : {CHECK_INTERVAL}s\n"
            f"💾 Data : {DATA_DIR}\n"
            f"📲 Ntfy : {'✅' if NTFY_TOPIC else '❌'} | 💬 Discord : {'✅' if DISCORD_WEBHOOK else '❌'} | 📧 Email : {'✅' if EMAIL_SMTP_USER else '❌'}"
        )
    elif t == "/reset":
        for k in ["prix_min","prix_max","km_max","marge_min","decote_min","score_min","marques","paused"]:
            _cfg.pop(k, None)
        save_config_runtime()
        send("🔄 Filtres remis par défaut.")
    else:
        for cmd, key, typ in [
            ("/prix_max","prix_max",int),("/prix_min","prix_min",int),
            ("/km_max","km_max",int),("/marge_min","marge_min",int),
            ("/decote_min","decote_min",int),("/score_min","score_min",int),
        ]:
            if t.startswith(cmd):
                try:
                    val = typ(t.split()[1])
                    _cfg[key] = val; save_config_runtime()
                    send(f"✅ <b>{key}</b> = <b>{val}</b>")
                except:
                    send(f"❌ Syntaxe : {cmd} <valeur>")
                return

# ══════════════════════════════════════════════════════════════
# FORMAT ALERTES
# ══════════════════════════════════════════════════════════════
def format_alerte(a, an):
    score   = an.get("score",0)
    emoji,_ = get_note_pepite(score)
    barre   = barre_score(score)
    urgence = an.get("urgence",False)
    carb_e  = {"DIESEL":"⛽","ESSENCE":"🔴","HYBRIDE":"🟢","ELECTRIQUE":"⚡","GPL":"🟡","ETHANOL":"🌿"}.get(a.get("_carburant",""),"⛽")
    boite_e = {"MANUELLE":"🔧","AUTOMATIQUE":"🔄","DSG":"⚙️","EDC":"⚙️","CVT":"🔄","ROBOTISEE":"⚙️"}.get(a.get("_boite",""),"🔧")
    km_str  = f" · {a['km']:,}km".replace(",",".") if a.get("km") else ""
    an_str  = f" · {a['annee']}" if a.get("annee") else ""
    entete  = ("🚨 <b>ALERTE URGENTE</b> 🚨\n" if urgence else "") + \
              f"{emoji} <b>{an.get('verdict','')}</b>\n" + \
              f"📊 NOTE MAX : <b>{score}/100</b>  <code>[{barre}]</code>"
    mot = analyser_moteur(a["titre"])
    mot_line = ""
    if mot:
        fid_e = "✅" if mot[1] > 0 else "⚠️" if mot[1] > -15 else "❌"
        mot_line = f"\n🔧 <b>{mot[0][:25]}</b> {fid_e} Fiabilité {mot[2]}/10\n<i>{mot[3][:80]}</i>\n"
    cg_line = "📄 <b>Carte grise incluse ✅</b>\n" if a.get("_cg_incluse") else ""
    detail  = a.get("_detail_score",{})
    detail_str = " · ".join([f"{k}:{v}" for k,v in list(detail.items())[:4]])
    return (
        f"{entete}\n\n"
        f"🚘 <b>{a['titre'][:70]}</b>{an_str}{km_str}\n"
        f"{carb_e} {a.get('_carburant','?')} · {boite_e} {a.get('_boite','?')}\n"
        f"{cg_line}{mot_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💶 Prix demandé :  <b>{a['prix']:,}€</b>\n".replace(",",".") +
        f"📊 Cote Argus :    <b>{an.get('prix_argus',0):,}€</b>\n".replace(",",".") +
        f"🎯 Sous Argus :    <b>-{an.get('economies_argus',0):,}€ (-{an.get('decote_pct',0)}%)</b>\n".replace(",",".") +
        f"💰 Revente :       <b>{an.get('prix_revente_bas',0):,}€ → {an.get('prix_revente_haut',0):,}€</b>\n".replace(",",".") +
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>MARGE RÉELLE (AE) :</b>\n"
        f"   Brute :     +{an.get('marge_brute',0):,}€\n".replace(",",".") +
        f"   TVA marge : -{an.get('tva_marge',0):,}€\n".replace(",",".") +
        f"   Cotis. AE : -{an.get('cotisations_ae',0):,}€\n".replace(",",".") +
        f"   ➡️ <b>NET : +{an.get('marge_nette',0):,}€</b>\n".replace(",",".") +
        f"🔄 ROI : <b>{an.get('roi',0)}%</b> · ⏱️ <b>{an.get('delai_revente','?')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <i>{an.get('points_forts','')}</i>\n"
        f"⚠️ <i>{an.get('risques','')}</i>\n\n"
        f"🤝 Max à payer : <b>{an.get('prix_achat_max',0):,}€</b>\n".replace(",",".") +
        f"💬 Négo : <i>{an.get('negociation','')}</i>\n"
        f"🔍 À vérifier : <i>{an.get('verifications','')}</i>\n\n"
        f"💡 <b>MAX :</b> <i>{an.get('conseil_max','')}</i>\n\n"
        f"📍 {a['source']} · 🧮 <i>{detail_str}</i>\n"
        f"🔗 <a href='{a['url']}'>👉 Voir l'annonce →</a>\n"
        f"⏰ {datetime.now().strftime('%d/%m à %H:%M:%S')}"
    )

def format_rapport(stats, pepites):
    top3 = sorted(pepites, key=lambda x: x.get("score",0), reverse=True)[:3]
    return (
        f"📊 <b>RAPPORT — MAX v7</b>\n"
        f"📅 {datetime.now().strftime('%A %d/%m/%Y à %Hh%M')}\n\n"
        f"🔍 Scannées :    <b>{stats['scanne']:,}</b>\n".replace(",",".") +
        f"🧠 Analysées :   <b>{stats['analyse']:,}</b>\n".replace(",",".") +
        f"💎 Pépites :     <b>{stats['pepites']}</b>\n"
        f"💰 Marge totale: <b>{stats['marge']:,}€</b>\n\n".replace(",",".") +
        f"<b>🏆 Top 3 :</b>\n" +
        "".join([f"• {p.get('titre','')[:40]} — {p.get('score',0)}/100 · +{p.get('marge_nette',0):,}€\n".replace(",",".") for p in top3]) +
        f"\n⚡ Scan toutes les {CHECK_INTERVAL}s · 18 sources\n"
        f"💾 Data : {DATA_DIR}\n"
        f"✅ <i>MAX veille 24h/24 🔥</i>"
    )

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    load_config_runtime()
    log.info("="*60)
    log.info("💎 MAX v7 ULTRA — CHASSEUR DE PÉPITES ZÉRO DÉFAUT")
    log.info(f"   18 sources · {CHECK_INTERVAL}s · Anti-ban · Stock check")
    log.info(f"   Telegram + Ntfy + Discord + Email · Persistance {DATA_DIR}")
    log.info("="*60)

    known   = load_known()
    stats   = load_stats()
    pepites = load_pepites()
    suivi   = load_suivi()
    checks  = 0
    # Charger offset Telegram depuis fichier pour éviter de retraiter les vieux messages
    tg_offset_file = DATA_DIR / "tg_offset.json"
    try:
        tg_offset = load_json(tg_offset_file, 0)
        if not isinstance(tg_offset, int): tg_offset = 0
    except: tg_offset = 0
    # Si offset=0, initialiser avec le dernier update_id connu pour ignorer les vieux messages
    if tg_offset == 0:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": -1}, timeout=10)
            if r.ok:
                updates = r.json().get("result", [])
                if updates:
                    tg_offset = updates[-1]["update_id"] + 1
                    save_json(tg_offset_file, tg_offset)
                    log.info(f"   Telegram offset initialisé à {tg_offset}")
        except: pass
    dernier_rapport   = datetime.now().replace(hour=0, minute=0, second=0)
    dernier_heartbeat = datetime.now()

    notifier_tout(
        "MAX v7 ULTRA DÉMARRÉ 🔥",
        f"18 sources · {CHECK_INTERVAL}s · Anti-ban · Stock check\nTapez /aide pour les commandes",
        "💎 <b>MAX v7 ULTRA — DÉMARRÉ</b> 💎\n\n"
        "📡 18 sources · Anti-ban · Vérif stock · 4 canaux alertes\n\n"
        "<b>🔨 Enchères :</b> Alcopa · BCA · Agorastore · Interenchères\n"
        "Autobid · Drouot · Domaines État 🇫🇷 · Commissaires\n\n"
        "<b>🚗 Occasions :</b> GPA26 · LeBonCoin · La Centrale\n"
        "AutoScout24 · Reezocar · ParuVendu · VivaStreet\n"
        "Aramisauto · ZoomCar · Caroom\n\n"
        f"⚡ Scan toutes les <b>{CHECK_INTERVAL}s</b>\n"
        "📱 Tapez /aide pour les commandes\n"
        "✅ <b>MAX est en chasse ! 🔥</b>",
        f"**MAX v7 ULTRA démarré** 🔥\n18 sources · {CHECK_INTERVAL}s · Anti-ban · Stock check",
    )

    while True:
        try:
            checks += 1
            now = datetime.now()
            stats["checks"] = checks

            # Commandes Telegram
            updates, tg_offset = get_updates(tg_offset)
            for upd in updates:
                msg = upd.get("message", {})
                texte = msg.get("text", "")
                if texte: traiter_commande(texte, stats, pepites)
            if updates:
                save_json(tg_offset_file, tg_offset)

            load_config_runtime()

            if is_paused():
                log.info("⏸️ En pause")
                time.sleep(10); continue

            # Rapport périodique
            if (now - dernier_rapport).total_seconds() >= RAPPORT_INTERVAL * 60:
                rpt = format_rapport(stats, pepites)
                send(rpt)
                send_discord("📊 Rapport MAX", rpt.replace("<b>","**").replace("</b>","**").replace("<i>","*").replace("</i>","*"))
                dernier_rapport = now

            # Heartbeat
            if (now - dernier_heartbeat).total_seconds() >= HEARTBEAT_MAX * 60:
                hb = f"💓 MAX en vie · Check #{checks} · {now.strftime('%H:%M')} · {stats.get('scanne',0)} scannées"
                send(f"💓 <b>MAX Heartbeat</b>\n✅ En vie · Check #{checks}\n⏰ {now.strftime('%d/%m à %H:%M')}\n🔍 Scannées : {stats.get('scanne',0):,}".replace(",","."))
                send_ntfy("💓 MAX Heartbeat", hb)
                dernier_heartbeat = now

            log.info(f"\n{'='*55}")
            log.info(f"[Check #{checks}] {now.strftime('%H:%M:%S')}")

            toutes = scan_tout()
            stats["scanne"] += len(toutes)
            toutes = dedup_cross_source(toutes)
            log.info(f"   Total: {len(toutes)} après dédup")

            for a in toutes:
                if a["prix"] > 0: checker_baisse_prix(a, suivi)
            save_suivi(suivi)

            nouvelles = [a for a in toutes if a["id"] not in known and matches_filter(a)]
            log.info(f"   → {len(nouvelles)} nouvelles")

            for a in nouvelles: a["_score_pre"] = scorer(a)
            nouvelles.sort(key=lambda x: x["_score_pre"], reverse=True)

            score_min_eff  = cfg("score_min", SCORE_MIN)
            marge_min_eff  = cfg("marge_min", MARGE_NETTE_MIN)
            decote_min_eff = cfg("decote_min", DECOTE_MIN)

            for a in nouvelles:
                known.add(a["id"])
                src_k = re.sub(r"[^\w]","",a["source"])[:12]
                stats["sources"][src_k] = stats["sources"].get(src_k,0) + 1

                score_pre = a.get("_score_pre", 50)
                if score_pre < (score_min_eff - 20): continue
                if not a["prix"] and a["type"] not in ["enchere","enchere_etat"]: continue

                if ANTHROPIC_KEY:
                    log.info(f"   🧠 [{score_pre}/100] {a['titre'][:45]}...")
                    analyse = analyser_ia(a, score_pre)
                    stats["analyse"] += 1

                    if analyse:
                        score       = analyse.get("score", 0)
                        marge_nette = analyse.get("marge_nette", 0)
                        decote      = analyse.get("decote_pct", 0)
                        urgence     = analyse.get("urgence", False) or score >= SCORE_URGENTE

                        ok_score  = score >= score_min_eff
                        ok_marge  = marge_nette >= marge_min_eff
                        ok_decote = decote >= decote_min_eff

                        if ok_score and ok_marge and ok_decote:
                            # Vérifier stock avant d'alerter
                            if not verifier_stock(a):
                                continue
                            log.info(f"   💎 PÉPITE ! {score}/100 · -{decote}% · +{marge_nette}€")
                            stats["pepites"] += 1
                            stats["marge"] = stats.get("marge",0) + marge_nette
                            pepites.append({**analyse,"titre":a["titre"][:60],
                                            "url":a["url"],"source":a["source"],
                                            "date":now.isoformat()})
                            save_pepites(pepites)
                            alerte = format_alerte(a, analyse)
                            ntfy_msg = (f"{analyse.get('verdict','')} | {score}/100\n"
                                        f"{a['titre'][:60]}\n"
                                        f"Prix: {a['prix']}€ | Net: +{marge_nette}€ | -{decote}%\n"
                                        f"{a['url']}")
                            discord_msg = (f"**{analyse.get('verdict','')}** | {score}/100\n\n"
                                           f"🚘 **{a['titre'][:70]}**\n"
                                           f"💶 {a['prix']:,}€ | Net: **+{marge_nette:,}€** | -{decote}%\n"
                                           f"📍 {a['source']}\n🔗 {a['url']}").replace(",",".")
                            notifier_tout(
                                f"{analyse.get('verdict','')} — {a['titre'][:50]}",
                                ntfy_msg, alerte, discord_msg, urgente=urgence
                            )
                            time.sleep(0.3)
                        else:
                            raisons = []
                            if not ok_score:  raisons.append(f"score {score}<{score_min_eff}")
                            if not ok_marge:  raisons.append(f"marge {marge_nette}€<{marge_min_eff}€")
                            if not ok_decote: raisons.append(f"décote {decote}%<{decote_min_eff}%")
                            log.info(f"   ⏭  {' | '.join(raisons)}")
                    else:
                        if a.get("type") == "enchere_etat" and a["prix"] > 0:
                            msg_etat = (
                                f"🏛️ <b>SAISIE ÉTAT</b>\n🚘 {a['titre'][:60]}\n"
                                f"💶 Mise à prix: <b>{a['prix']:,}€</b>\n".replace(",",".") +
                                f"📍 {a['source']}\n🔗 <a href='{a['url']}'>Voir →</a>"
                            )
                            send(msg_etat)
                            send_ntfy(f"🏛️ Saisie État", f"{a['titre'][:60]}\n{a['prix']}€\n{a['url']}")
                else:
                    if score_pre >= score_min_eff:
                        emoji, label = get_note_pepite(score_pre)
                        msg_simple = (
                            f"{emoji} <b>{label}</b> — {score_pre}/100\n"
                            f"🚘 {a['titre'][:60]}\n"
                            f"💶 {a['prix']:,}€{' · '+str(a['km'])+'km' if a.get('km') else ''}\n".replace(",",".") +
                            f"📍 {a['source']}\n🔗 <a href='{a['url']}'>Voir →</a>\n"
                            f"<i>⚠️ Ajoutez ANTHROPIC_KEY pour l'analyse complète</i>"
                        )
                        send(msg_simple)
                        send_ntfy(f"{emoji} {label}", f"{a['titre'][:60]}\n{a['prix']}€\n{a['url']}")

            save_known(known)
            save_stats(stats)
            log.info(f"   ✅ Check #{checks} — next dans {CHECK_INTERVAL}s")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("MAX arrêté manuellement.")
            break
        except Exception as e:
            log.error(f"Erreur boucle principale: {e}")
            time.sleep(30)  # Attente avant de relancer

if __name__ == "__main__":
    main()
