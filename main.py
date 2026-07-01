from flask import Flask
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
from routes.forexe import forexe_bp  # Importam Blueprint-ul pentru forexe
# 1. Initializam logger-ul global (ca sa scrie in fisierul .log)
logger = setup_logger()

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = None  # Dezactiveaza limita globala de content-length (pentru imagini mari)

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
app.register_blueprint(forexe_bp)  # Inregistram Blueprint-ul pentru forexe

logger.info("=== RUTE ÎNREGISTRATE ===")
for rule in app.url_map.iter_rules():
    logger.info(f"  {rule.methods} {rule.rule}")
logger.info("=== SFÂRȘIT RUTE ===")

# 3. Pornim serverul
if __name__ == '__main__':
    logger.info("--- SERVER PORNIT PE PORTUL 5008 (MODULAR) ---")
    app.run(host='0.0.0.0', port=5008)