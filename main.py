import json

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from utils.logger import setup_logger
from utils import asociere_log

# ---------------------------------------------------------------------------
# THE ASSOCIATION JOURNAL SWITCH. True writes `asociere.log` next to
# `api_server.log`, one block per association request: the picture it read, what
# the run meant to do, every check with the numbers it compared, and what was
# written. False opens no file and builds no line.
#
# Flip it HERE and restart the server. `KBOT_ASOCIERE=0` in the environment
# outranks this switch (that is how the test suite keeps the file shut);
# `ASOCIERE_LOG_PATH` in config.py moves the file.
# ---------------------------------------------------------------------------
ASOCIERE_LOG_ENABLED = True
asociere_log.set_enabled(ASOCIERE_LOG_ENABLED)

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
from routes.update import update_bp  # slice 0067: application update channel (public, read-only)
from routes.inregistrare.inregistrare import inregistrare_bp  # felia 0075: pagina publica de inregistrare
from routes.landing.landing import landing_bp  # public presentation page: GET / (k-bot.ro)
from routes.landing.seo import seo_bp  # robots.txt + sitemap.xml
from routes.landing.vizite import vizite_bp  # slice 0110-09: POST /api/vizita (who looks at the page)
from routes.detalii.detalii import detalii_bp  # slice 0110-02: GET /detalii + POST /api/detalii
from routes.pdf_inteligent.pdf_inteligent import pdf_inteligent_bp  # public article: GET /pdf-inteligent (Reader vs Pro, ALOP forms)
from routes.portal.portal import portal_bp  # slice 0110-03: GET /portal + /api/portal/*
import routes.portal.date  # noqa: F401  slice 0110-06: registers /api/portal/date/* on portal_bp
import routes.portal.certificat  # noqa: F401  slice 0110-04: registers /api/portal/certificat/* on portal_bp
from routes.logs import logs_bp  # slice 0089: the caller's own server / timing journal lines
from routes.help_feedback import help_feedback_bp  # slice 0000-21: help questions + ratings (nothing about who)
from routes.error_report import error_report_bp  # slice 0112-04: POST /api/errors/report (error sent from the message window)
from routes.setari import setari_bp  # slice 0100-02: GET /api/setari (the settings the server decides)
from routes.access import access_bp  # slice 0104: POST /api/access/client-type (public: Access client or not)
from routes.efactura import efactura_bp  # slice 00EF-04: /api/efactura/token/* (ANAF e-Factura token, bearer)
from routes.adechit import adechit_bp
from routes.adechit.parent_portal import parent_portal_bp

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
app.register_blueprint(update_bp)
app.register_blueprint(inregistrare_bp)  # Inregistrarea publica a unei unitati noi
app.register_blueprint(landing_bp)  # GET / : the K-BOT presentation page
app.register_blueprint(seo_bp)  # robots.txt + sitemap.xml
app.register_blueprint(vizite_bp)  # slice 0110-09: POST /api/vizita
app.register_blueprint(detalii_bp)  # slice 0110-02: the «Cere mai multe detalii» form
app.register_blueprint(pdf_inteligent_bp)  # the «PDF Inteligent» article
app.register_blueprint(portal_bp)  # slice 0110-03: web area of registered users (read-only)
app.register_blueprint(logs_bp)  # slice 0089: GET /api/logs/server, /api/logs/timing
app.register_blueprint(help_feedback_bp)  # slice 0000-21: POST /api/help/feedback
app.register_blueprint(error_report_bp)  # slice 0112-04: POST /api/errors/report
app.register_blueprint(setari_bp)  # slice 0100-02: GET /api/setari
app.register_blueprint(access_bp)  # slice 0104: POST /api/access/client-type
app.register_blueprint(efactura_bp)  # slice 00EF-04: /api/efactura/token/start, /cod, /stare
app.register_blueprint(adechit_bp)
app.register_blueprint(parent_portal_bp)

logger.info("=== RUTE ÎNREGISTRATE ===")
for rule in app.url_map.iter_rules():
    logger.info(f"  {rule.methods} {rule.rule}")
logger.info("=== SFÂRȘIT RUTE ===")

# 3. Pornim serverul
if __name__ == '__main__':
    logger.info("--- SERVER PORNIT PE PORTUL 5008 (MODULAR) ---")
    app.run(host='0.0.0.0', port=5008)
