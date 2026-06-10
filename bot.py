import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
PORT = int(os.environ.get("PORT", "8080"))
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ══════════════════════════════
# CONFIG — via variables d'environnement
# ══════════════════════════════
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "60"))   # secondes
PRIX_MIN         = int(os.environ.get("PRIX_MIN", "0"))
PRIX_MAX         = int(os.environ.get("PRIX_MAX", "0"))          # 0 = illimité
KM_MAX           = int(os.environ.get("KM_MAX", "0"))            # 0 = illimité
CARBURANT        = os.environ.get("CARBURANT", "").lower()       # diesel, essence, electrique...
PROCEDURE        = os.environ.get("PROCEDURE", "").lower()       # pro, particulier, pieces
MARQUES          = [m.strip().upper() for m in os.environ.get("MARQUES", "").split(",") if m.strip()]
MOTS_CLES        = [m.strip().lower() for m in os.environ.get("MOTS_CLES", "").split(",") if m.strip()]
LOCALISATION     = os.environ.get("LOCALISATION", "").upper()    # GPA 26, GPA 49, GPA 60

# Fichier pour stocker les IDs déjà vus
KNOWN_IDS_FILE = "known_ids.json"

# ══════════════════════════════
# LOGGING
# ══════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("GPA26Bot")

# ══════════════════════════════
# STOCKAGE IDs CONNUS
# ══════════════════════════════
def load_known_ids():
    try:
        with open(KNOWN_IDS_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_known_ids(ids):
    with open(KNOWN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)

# ══════════════════════════════
# SCRAPING GPA26
# ══════════════════════════════
def fetch_annonces():
    url = "https://revente.gpa26.com/fr/"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return parse_annonces(r.text)
    except Exception as e:
        log.error(f"Erreur fetch: {e}")
        return []

def parse_annonces(html):
    soup = BeautifulSoup(html, "html.parser")
    annonces = []
    seen_ids = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Format: https://revente.gpa26.com/fr/123456
        import re
        m = re.search(r"/fr/(\d{5,})", href)
        if not m:
            continue
        annonce_id = m.group(1)
        if annonce_id in seen_ids:
            continue
        seen_ids.add(annonce_id)

        text = link.get_text(" ", strip=True)
        if len(text) < 5:
            continue

        # Extraire titre
        lines = [l.strip() for l in text.split() if l.strip()]
        titre = " ".join(lines[:8]) if lines else text[:80]

        # Extraire prix
        prix = 0
        prix_match = re.search(r"(\d[\d\s]*)\s*€", text)
        if prix_match:
            prix = int(prix_match.group(1).replace(" ", "").replace("\u202f", ""))

        # Extraire km
        km = 0
        km_match = re.search(r"(\d[\d\s]{2,})\s*km", text, re.IGNORECASE)
        if km_match:
            km = int(km_match.group(1).replace(" ", "").replace("\u202f", ""))

        # Localisation
        loc = ""
        for gpa in ["GPA 26", "GPA 49", "GPA 60"]:
            if gpa in text.upper():
                loc = gpa
                break

        # Procédure
        procedure = ""
        text_lower = text.lower()
        if "professionnel" in text_lower:
            procedure = "pro"
        elif "particulier" in text_lower:
            procedure = "particulier"
        elif "pièces" in text_lower or "pieces" in text_lower:
            procedure = "pieces"

        # Carburant
        carbu = ""
        titre_up = titre.upper()
        if any(x in titre_up for x in ["GAZOLE","DIESEL","HDI","DCI","TDI","CDTI","BLUEDCI"]):
            carbu = "diesel"
        elif any(x in titre_up for x in ["ELECTR","ÉLECTR","BEV","KWH"]):
            carbu = "electrique"
        elif any(x in titre_up for x in ["HYBRID","HYBRIDE","E-TECH","PHEV"]):
            carbu = "hybride"
        elif any(x in titre_up for x in ["GPL","LPG"]):
            carbu = "gpl"
        elif any(x in titre_up for x in ["ESSENCE","TCE","TSI","TFSI","PURETECH","ECOBOOST","SCE"]):
            carbu = "essence"

        # Marque
        marques_connues = ["RENAULT","PEUGEOT","CITROEN","VOLKSWAGEN","BMW","MERCEDES",
                          "AUDI","FORD","TOYOTA","OPEL","DACIA","FIAT","SKODA","SEAT",
                          "NISSAN","HONDA","HYUNDAI","KIA","VOLVO","LAND ROVER","TESLA"]
        marque = next((m for m in marques_connues if m in titre_up), "")

        annonces.append({
            "id":        annonce_id,
            "titre":     titre[:100],
            "prix":      prix,
            "km":        km,
            "localisation": loc,
            "procedure": procedure,
            "carburant": carbu,
            "marque":    marque,
            "url":       f"https://revente.gpa26.com/fr/{annonce_id}",
        })

    return annonces

# ══════════════════════════════
# FILTRES
# ══════════════════════════════
def matches_filter(a):
    # Prix
    if PRIX_MIN and a["prix"] > 0 and a["prix"] < PRIX_MIN:
        return False
    if PRIX_MAX and a["prix"] > PRIX_MAX and a["prix"] > 0:
        return False

    # KM
    if KM_MAX and a["km"] > KM_MAX and a["km"] > 0:
        return False

    # Carburant
    if CARBURANT and a["carburant"] and CARBURANT not in a["carburant"]:
        return False

    # Procédure
    if PROCEDURE and a["procedure"] != PROCEDURE:
        return False

    # Localisation
    if LOCALISATION and a["localisation"] and LOCALISATION not in a["localisation"].upper():
        return False

    # Marques
    if MARQUES and a["marque"] not in MARQUES:
        return False

    # Mots-clés
    if MOTS_CLES:
        titre_lower = a["titre"].lower()
        if not any(kw in titre_lower for kw in MOTS_CLES):
            return False

    return True

# ══════════════════════════════
# TELEGRAM
# ══════════════════════════════
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configuré — message non envoyé")
        log.info(f"Message: {msg}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        r.raise_for_status()
        log.info("✅ Notification Telegram envoyée")
    except Exception as e:
        log.error(f"Erreur Telegram: {e}")

def format_message(annonce):
    proc_emoji = {"pro": "🏢", "particulier": "👤", "pieces": "🔧"}.get(annonce["procedure"], "🚗")
    carbu_emoji = {"diesel": "🚗", "essence": "⛽", "electrique": "⚡", "hybride": "🔋", "gpl": "🟢"}.get(annonce["carburant"], "")

    lines = [
        f"🚨 <b>NOUVELLE ANNONCE GPA26</b>",
        f"",
        f"🚘 <b>{annonce['titre']}</b>",
        f"",
    ]
    if annonce["prix"]:
        lines.append(f"💶 <b>{annonce['prix']:,} €</b>".replace(",", " "))
    if annonce["km"]:
        lines.append(f"🛣️ {annonce['km']:,} km".replace(",", " "))
    if annonce["carburant"]:
        lines.append(f"{carbu_emoji} {annonce['carburant'].capitalize()}")
    if annonce["localisation"]:
        lines.append(f"📍 {annonce['localisation']}")
    if annonce["procedure"]:
        proc_label = {"pro": "Professionnels", "particulier": "Particuliers", "pieces": "Pour pièces"}.get(annonce["procedure"], "")
        lines.append(f"{proc_emoji} {proc_label}")
    lines.append(f"")
    lines.append(f"🔗 <a href='{annonce['url']}'>Voir l'annonce →</a>")
    lines.append(f"")
    lines.append(f"⏰ {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}")

    return "\n".join(lines)

# ══════════════════════════════
# BOUCLE PRINCIPALE
# ══════════════════════════════
def main():
    log.info("=" * 50)
    log.info("🚗 GPA26 Bot — Démarrage")
    log.info(f"   Intervalle : {CHECK_INTERVAL}s")
    log.info(f"   Prix       : {PRIX_MIN}€ → {PRIX_MAX or '∞'}€")
    log.info(f"   KM max     : {KM_MAX or '∞'}")
    log.info(f"   Carburant  : {CARBURANT or 'tous'}")
    log.info(f"   Procédure  : {PROCEDURE or 'toutes'}")
    log.info(f"   Marques    : {MARQUES or 'toutes'}")
    log.info(f"   Mots-clés  : {MOTS_CLES or 'tous'}")
    log.info("=" * 50)

    known_ids = load_known_ids()
    checks = 0

    # Envoi messasend_telegram(
    f"✅ <b>GPA26 Bot démarré</b>\n"
    ...
    f"Je vous alerterai dès qu'une nouvelle annonce correspond 🎯"
)ge de démarrage
    s
        f"✅ <b>GPA26 Bot démarré</b>\n"
        f"Surveillance toutes les {CHECK_INTERVAL}s\n"
        f"Prix: {PRIX_MIN}€ → {PRIX_MAX or '∞'}€\n"
        f"Carburant: {CARBURANT or 'tous'}\n"
        f"Procédure: {PROCEDURE or 'toutes'}\n"
        f"Je vous alerterai dès qu'une nouvelle annonce correspond 🎯"
)
    
class KA(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass
threading.Thread(target=lambda: HTTPServer(("0.0.0.0",PORT),KA).serve_forever(), daemon=True).start()
    while True:
        checks += 1
        log.info(f"[Check #{checks}] Vérification GPA26...")

        annonces = fetch_annonces()
        log.info(f"   → {len(annonces)} annonce(s) trouvée(s)")

        nouvelles = []
        for a in annonces:
            if a["id"] not in known_ids:
                known_ids.add(a["id"])
                if matches_filter(a):
                    nouvelles.append(a)

        save_known_ids(known_ids)

        if nouvelles:
            log.info(f"   🚨 {len(nouvelles)} NOUVELLE(S) ANNONCE(S) !")
            for a in nouvelles:
                send_telegram(format_message(a))
                time.sleep(0.5)  # éviter flood Telegram
        else:
            log.info(f"   ✓ Aucune nouveauté")

        log.info(f"   Prochaine vérif. dans {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
