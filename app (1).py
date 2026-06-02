from flask import Flask, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import threading
import time

app = Flask(__name__)
CORS(app)

# Cache global
cache = {"biens": [], "last_update": None}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
}

REGIONS_MAP = {
    "paris": "Paris / Île-de-France", "île-de-france": "Paris / Île-de-France",
    "idf": "Paris / Île-de-France", "75": "Paris / Île-de-France",
    "92": "Paris / Île-de-France", "93": "Paris / Île-de-France", "94": "Paris / Île-de-France",
    "var": "Var", "83": "Var", "toulon": "Var", "fréjus": "Var", "hyères": "Var",
    "alpes-maritimes": "Alpes-Maritimes", "06": "Alpes-Maritimes",
    "nice": "Alpes-Maritimes", "cannes": "Alpes-Maritimes", "antibes": "Alpes-Maritimes",
    "bordeaux": "Bordeaux", "33": "Bordeaux", "gironde": "Bordeaux",
    "normandie": "Normandie", "rouen": "Normandie", "caen": "Normandie",
    "76": "Normandie", "14": "Normandie", "27": "Normandie",
    "la rochelle": "La Rochelle", "17": "La Rochelle", "charente": "La Rochelle",
}

def detect_region(text):
    text_lower = text.lower()
    for key, region in REGIONS_MAP.items():
        if key in text_lower:
            return region
    return "Autre"

def detect_type(text):
    text_lower = text.lower()
    if any(w in text_lower for w in ["appartement", "studio", "t1", "t2", "t3", "t4", "t5", "duplex"]):
        return "Appartement"
    if any(w in text_lower for w in ["maison", "villa", "pavillon"]):
        return "Maison"
    if any(w in text_lower for w in ["immeuble", "résidence"]):
        return "Immeuble"
    if any(w in text_lower for w in ["local commercial", "boutique", "commerce", "fonds"]):
        return "Local commercial"
    if any(w in text_lower for w in ["terrain", "parcelle", "foncier"]):
        return "Terrain"
    return "Bien immobilier"

def detect_surface(text):
    match = re.search(r'(\d+)\s*m[²2]', text)
    return int(match.group(1)) if match else None

def detect_pli(text):
    text_lower = text.lower()
    return any(w in text_lower for w in ["pli cacheté", "pli scellé", "offre sous pli", "sous pli"])

def detect_prix(text):
    match = re.search(r'(\d[\d\s]*)\s*[€eur]', text, re.IGNORECASE)
    if match:
        return int(re.sub(r'\s', '', match.group(1)))
    return None

# ── SCRAPER 1 : repreneurs.com ──────────────────────────────────────────────
def scrape_repreneurs():
    results = []
    try:
        url = "https://www.repreneurs.com/actifs-a-vendre-via-mandataires-judiciaires.php?type=immobilier"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Chercher les liens d'actifs immobiliers
        links = soup.find_all("a", href=re.compile(r"/actif-\d+"))
        seen = set()
        for link in links[:20]:
            href = link.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            titre = link.get_text(strip=True)
            if not titre or len(titre) < 5:
                continue
            full_url = "https://www.repreneurs.com" + href if href.startswith("/") else href
            region = detect_region(titre + " " + href)
            results.append({
                "id": "rep_" + href.split("-")[1] if "-" in href else href,
                "titre": titre,
                "type": detect_type(titre),
                "surface": detect_surface(titre),
                "region": region,
                "prix": None,
                "pliCachete": detect_pli(titre),
                "source": "repreneurs",
                "sourceName": "Repreneurs.com",
                "sourceColor": "#c0392b",
                "dateOffre": None,
                "description": titre,
                "nouveaute": True,
                "url": full_url,
                "ref": href.split("/")[-1][:20]
            })
    except Exception as e:
        print(f"[repreneurs] Erreur: {e}")
    return results

# ── SCRAPER 2 : aspaj.fr ────────────────────────────────────────────────────
def scrape_aspaj():
    results = []
    try:
        url = "https://www.aspaj.fr/annonces/?cat=immobilier"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"post|annonce|item"))
        for article in articles[:15]:
            titre_el = article.find(["h2", "h3", "h4", "a"])
            if not titre_el:
                continue
            titre = titre_el.get_text(strip=True)
            if len(titre) < 5:
                continue
            link = article.find("a", href=True)
            href = link["href"] if link else "#"
            full_url = href if href.startswith("http") else "https://www.aspaj.fr" + href
            texte = article.get_text(" ", strip=True)
            region = detect_region(titre + " " + texte)

            # Chercher date limite
            date_match = re.search(r'(\d{2}/\d{2}/\d{4})', texte)
            date_str = date_match.group(1) if date_match else None

            results.append({
                "id": "aspaj_" + re.sub(r'\W+', '', titre)[:15],
                "titre": titre,
                "type": detect_type(titre + " " + texte),
                "surface": detect_surface(texte),
                "region": region,
                "prix": detect_prix(texte),
                "pliCachete": detect_pli(texte),
                "source": "aspaj",
                "sourceName": "ASPAJ",
                "sourceColor": "#1a3a6b",
                "dateOffre": date_str,
                "description": texte[:200],
                "nouveaute": True,
                "url": full_url,
                "ref": "ASPAJ-" + re.sub(r'\W+', '', titre)[:8].upper()
            })
    except Exception as e:
        print(f"[aspaj] Erreur: {e}")
    return results

# ── SCRAPER 3 : mjassocies.eu ───────────────────────────────────────────────
def scrape_mjassocies():
    results = []
    try:
        url = "https://www.mjassocies.eu/biens/"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        items = soup.find_all(["article", "div"], class_=re.compile(r"bien|post|item|card|annonce"))
        for item in items[:15]:
            titre_el = item.find(["h2", "h3", "h4", "a"])
            if not titre_el:
                continue
            titre = titre_el.get_text(strip=True)
            if len(titre) < 5:
                continue
            link = item.find("a", href=True)
            href = link["href"] if link else "#"
            full_url = href if href.startswith("http") else "https://www.mjassocies.eu" + href
            texte = item.get_text(" ", strip=True)
            region = detect_region(titre + " " + texte)

            date_match = re.search(r'(\d{2}/\d{2}/\d{4})', texte)
            date_str = date_match.group(1) if date_match else None

            results.append({
                "id": "mja_" + re.sub(r'\W+', '', titre)[:15],
                "titre": titre,
                "type": detect_type(titre + " " + texte),
                "surface": detect_surface(texte),
                "region": region,
                "prix": detect_prix(texte),
                "pliCachete": detect_pli(texte),
                "source": "mjassocies",
                "sourceName": "ADN MJ",
                "sourceColor": "#1a3a6b",
                "dateOffre": date_str,
                "description": texte[:200],
                "nouveaute": True,
                "url": full_url,
                "ref": "ADN-" + re.sub(r'\W+', '', titre)[:8].upper()
            })
    except Exception as e:
        print(f"[mjassocies] Erreur: {e}")
    return results

# ── SCRAPER 4 : conciergeriejudiciaire.com ──────────────────────────────────
def scrape_conciergerie():
    results = []
    try:
        url = "https://www.conciergeriejudiciaire.com/collections/all"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        items = soup.find_all(["div", "li"], class_=re.compile(r"product|item|card"))
        for item in items[:10]:
            titre_el = item.find(["h2", "h3", "h4", "a", "span"])
            if not titre_el:
                continue
            titre = titre_el.get_text(strip=True)
            if len(titre) < 5:
                continue
            link = item.find("a", href=True)
            href = link["href"] if link else "#"
            full_url = href if href.startswith("http") else "https://www.conciergeriejudiciaire.com" + href
            texte = item.get_text(" ", strip=True)
            region = detect_region(titre + " " + texte)

            results.append({
                "id": "cj_" + re.sub(r'\W+', '', titre)[:15],
                "titre": titre,
                "type": detect_type(titre + " " + texte),
                "surface": detect_surface(texte),
                "region": region,
                "prix": detect_prix(texte),
                "pliCachete": detect_pli(texte),
                "source": "conciergerie",
                "sourceName": "Conciergerie Judiciaire",
                "sourceColor": "#6b1a1a",
                "dateOffre": None,
                "description": texte[:200],
                "nouveaute": True,
                "url": full_url,
                "ref": "CJ-" + re.sub(r'\W+', '', titre)[:8].upper()
            })
    except Exception as e:
        print(f"[conciergerie] Erreur: {e}")
    return results

def refresh_cache():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Actualisation des données...")
    all_biens = []
    all_biens += scrape_repreneurs()
    all_biens += scrape_aspaj()
    all_biens += scrape_mjassocies()
    all_biens += scrape_conciergerie()

    # Filtrer uniquement l'immobilier - filtre élargi
    immo_keywords = ["appartement", "maison", "villa", "immeuble", "local", "terrain",
                     "immobilier", "foncier", "résidence", "studio", "duplex", "loft",
                     "parking", "garage", "entrepôt", "bien", "actif", "fonds",
                     "commerce", "bureau", "atelier", "hangar", "murs", "cession",
                     "m²", "m2", "surface", "propriété", "locaux", "bâtiment"]
    filtered = [b for b in all_biens if any(k in b["titre"].lower() or k in b["description"].lower() for k in immo_keywords)]
    # Si aucun résultat avec filtre strict, retourner tous les biens
    if not filtered:
        filtered = all_biens

    # Déduplication par titre
    seen_titles = set()
    unique = []
    for b in filtered:
        key = re.sub(r'\W+', '', b["titre"].lower())[:20]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(b)

    cache["biens"] = unique
    cache["last_update"] = datetime.now().isoformat()
    print(f"[OK] {len(unique)} biens immobiliers trouvés")

def background_refresh():
    while True:
        refresh_cache()
        time.sleep(3600)  # toutes les heures

# Routes API
@app.route("/api/biens")
def get_biens():
    if not cache["biens"]:
        refresh_cache()
    return jsonify({
        "biens": cache["biens"],
        "last_update": cache["last_update"],
        "count": len(cache["biens"])
    })

@app.route("/api/refresh", methods=["POST"])
def force_refresh():
    refresh_cache()
    return jsonify({"ok": True, "count": len(cache["biens"])})

@app.route("/api/status")
def status():
    return jsonify({
        "status": "ok",
        "biens_count": len(cache["biens"]),
        "last_update": cache["last_update"]
    })

@app.route("/")
def home():
    return jsonify({"message": "ImmoJudiciaire API", "version": "1.0", "endpoints": ["/api/biens", "/api/refresh", "/api/status"]})

if __name__ == "__main__":
    # Lancer le refresh en arrière-plan
    t = threading.Thread(target=background_refresh, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=10000)
