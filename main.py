import json

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from utils.logger import setup_logger

# Importam modulele (Blueprints) pe care le-am creat in folderul /routes
from routes.admin import admin_bp
from routes.nomenclatoare import nom_bp
# from routes.docfund import doc_bp
from routes.tools import tools_bp
from routes.salarii import salarii_bp
from routes.parteneri import parteneri_bp
from routes.clasificatii import clasificatii_bp
from routes.mfp import mfp_bp  
from routes.wfls import wfl_bp  
from routes.ftp import ftp_bp  # Importam Blueprint-ul de upload FTP 
from routes.ddf import ddf_bp  # Importam Blueprint-ul pentru DDF
from routes.ord import ord_bp
from routes.forexe import forexe_bp  # Importam Blueprint-ul FOREXE (ListaAngajamente)
from routes.auth import auth_bp  # Importam Blueprint-ul de login al aplicatiei K-BOT
from routes.forexe.seed import seed_bp
from routes.migrare.migrare import migrare_bp  # felia 0044: migrarea prin fisier .accdb impins

# 1. Initializam logger-ul global (ca sa scrie in fisierul .log)
logger = setup_logger()

app = Flask(__name__)

# Diacritice romanesti LITERALE (UTF-8) in raspunsurile JSON, nu escape-uri \uXXXX
# (Flask >=2.2: app.json.ensure_ascii). Fara asta, mesajele de eroare pentru operator
# ("Sesiune expirată", "Autentificați-vă") ajung ilizibile in corpul raspunsului.
try:
    app.json.ensure_ascii = False
except Exception:
    app.config["JSON_AS_ASCII"] = False   # fallback pentru Flask vechi

# Rulam in spatele nginx (un singur proxy). ProxyFix face ca request.remote_addr sa
# fie IP-ul REAL al clientului (din X-Forwarded-For), nu 127.0.0.1. De asta depinde
# limita anti-forta-bruta din routes/auth/ratelimit.py: fara ea, toti clientii ar
# imparti un singur bucket si s-ar bloca reciproc.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Limita globala de content-length (felia 0041). ERA `None` (dezactivata, „pentru imagini
# mari"); acum e 17 MB, plafonul practic al unui PDF semnat stocat pe server.
#
# ATENTIE — SCHIMBAREA ATINGE TOATE RUTELE, nu doar cele de PDF: incarcarile mari existente
# (atasamente base64, capturi de ecran, upload FTP) primesc de acum 413 daca depasesc pragul.
# Decizia operatorului (2026-08-17): limita GLOBALA, dar cu un mesaj care spune clar cat e
# plafonul si ce e de facut — vezi handler-ul de mai jos. Capturile de ecran trebuie comprimate
# la generare, inainte sa intre in XML.
app.config['MAX_CONTENT_LENGTH'] = 17 * 1024 * 1024

# Werkzeug taie cererea inainte sa ajunga la vreo ruta, deci 413-ul NU trece prin niciun
# handler de-al nostru — fara acesta, clientul ar primi pagina HTML implicita si operatorul
# un mesaj gol. K-BOT citeste campul «error», deci raspunsul trebuie sa fie JSON romanesc.
@app.errorhandler(413)
def _payload_prea_mare(_e):
    plafon_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    body = json.dumps({
        "error": f"Fișierul trimis depășește limita de {plafon_mb} MB acceptată de server. "
                 f"Reduceți dimensiunea documentului (comprimați capturile de ecran atașate) "
                 f"și încercați din nou.",
        "reason": "PAYLOAD_TOO_LARGE",
    }, ensure_ascii=False)
    return app.response_class(body, status=413, mimetype="application/json")

# 2. Inregistram Blueprints
# Aici practic ii spunem aplicatiei principale sa includa rutele din celelalte fisiere
app.register_blueprint(admin_bp)
app.register_blueprint(nom_bp)
# app.register_blueprint(doc_bp)
app.register_blueprint(tools_bp)
app.register_blueprint(salarii_bp)
app.register_blueprint(parteneri_bp)
app.register_blueprint(clasificatii_bp)
app.register_blueprint(mfp_bp)
app.register_blueprint(wfl_bp)
app.register_blueprint(ftp_bp)  # Inregistram Blueprint-ul de upload FTP
app.register_blueprint(ddf_bp)  # Inregistram Blueprint-ul pentru DDF
app.register_blueprint(ord_bp)
app.register_blueprint(forexe_bp)  # Inregistram Blueprint-ul FOREXE
app.register_blueprint(auth_bp)  # Inregistram Blueprint-ul de login
app.register_blueprint(seed_bp)
app.register_blueprint(migrare_bp)

logger.info("=== RUTE ÎNREGISTRATE ===")
for rule in app.url_map.iter_rules():
    logger.info(f"  {rule.methods} {rule.rule}")
logger.info("=== SFÂRȘIT RUTE ===")

# 3. Pornim serverul
if __name__ == '__main__':
    logger.info("--- SERVER PORNIT PE PORTUL 5008 (MODULAR) ---")
    app.run(host='0.0.0.0', port=5008)