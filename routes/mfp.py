import re
import time
import logging
import requests
import pikepdf
from io import BytesIO
from pathlib import Path
from lxml import etree  # type: ignore
from bs4 import BeautifulSoup, Tag
from flask import Blueprint, request, send_file, jsonify
from datetime import datetime
from utils.security import require_api_key

# --- CONFIGURARE ---
mfp_bp = Blueprint('mfp', __name__)
logger = logging.getLogger(__name__)

GHIDURI_URL = "https://mfinante.gov.ro/web/forexepublic/ghiduri-si-manuale"
CACHE_DIR = Path("cache/pdf_templates")
CACHE_DIR_VBNET = Path("cache/vbnet")

MAX_XML_SIZE = 5 * 1024 * 1024  # 5MB

SAFE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    dtd_validation=False,
    remove_comments=True,
    huge_tree=False
)

def get_latest_mfp_info(doc_type="DDF"):
    """Scrape pe site-ul MFP pentru a gasi ultima versiune a documentului specificat."""
    
    # Mapare intre tipul documentului si textul care apare in link-ul de pe site-ul MFP
    # "Ordonantar" acopera variante ca "Ordonantari" sau "Ordonantare"
    keywords = {
        "DDF": "DocumentFundamentare",
        "ORD": "Ordonantar" 
    }
    
    search_key = keywords.get(doc_type, doc_type)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    session = requests.Session()
    session.headers.update(headers)

    try:
        logger.info(f"Accesare MFP pentru {doc_type}: {GHIDURI_URL}")
        response = None
        for attempt in range(3):
            try:
                if attempt > 0: time.sleep(2)
                response = session.get(GHIDURI_URL, timeout=20, verify=True)
                response.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2: raise
                logger.warning(f"Tentativa {attempt+1} esuata: {e}")

        if response is None: return None, None
        soup = BeautifulSoup(response.text, 'html.parser')
        pdf_link = None
        target_filename = ""

        for a in soup.find_all('a', href=True):
            if not isinstance(a, Tag): continue
            href = str(a.get('href', ''))
            
            # Conditie adaptata pentru parametrul cerut
            if search_key in href and "cu_xml" in href:
                pdf_link = href if href.startswith('http') else f"https://mfinante.gov.ro{href}"
                parts = href.split('/')
                target_filename = next((p for p in parts if ".pdf" in p), "template.pdf")
                break

        if not pdf_link: return None, None
        
        version_match = re.search(r'_(\d{3})_', target_filename)
        version = version_match.group(1) if version_match else "000"
        date_match = re.search(r'(\d{4}_\d{2}_\d{2})', target_filename)
        date_str = date_match.group(1) if date_match else "unknown"

        # Formeaza numele in functie de doc_type (ex: ORD_V001_2024_02_25.pdf)
        return pdf_link, f"{doc_type}_V{version}_{date_str}.pdf"
    except Exception as e:
        logger.error(f"Eroare parser MFP: {str(e)}")
        return None, None
    
# def get_latest_mfp_info():
#     """Scrape pe site-ul MFP pentru a gasi ultima versiune a DDF."""
#     headers = {
#         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
#         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
#     }
#     session = requests.Session()
#     session.headers.update(headers)

#     try:
#         logger.info(f"Accesare MFP: {GHIDURI_URL}")
#         response = None
#         for attempt in range(3):
#             try:
#                 if attempt > 0: time.sleep(2)
#                 response = session.get(GHIDURI_URL, timeout=20, verify=True)
#                 response.raise_for_status()
#                 break
#             except requests.exceptions.RequestException as e:
#                 if attempt == 2: raise
#                 logger.warning(f"Tentativa {attempt+1} esuata: {e}")

#         if response is None: return None, None
#         soup = BeautifulSoup(response.text, 'html.parser')
#         pdf_link = None
#         target_filename = ""

#         for a in soup.find_all('a', href=True):
#             if not isinstance(a, Tag): continue
#             href = str(a.get('href', ''))
#             if "DocumentFundamentare" in href and "cu_xml" in href:
#                 pdf_link = href if href.startswith('http') else f"https://mfinante.gov.ro{href}"
#                 parts = href.split('/')
#                 target_filename = next((p for p in parts if ".pdf" in p), "template.pdf")
#                 break

#         if not pdf_link: return None, None
#         version_match = re.search(r'_(\d{3})_', target_filename)
#         version = version_match.group(1) if version_match else "000"
#         date_match = re.search(r'(\d{4}_\d{2}_\d{2})', target_filename)
#         date_str = date_match.group(1) if date_match else "unknown"

#         return pdf_link, f"DDF_V{version}_{date_str}.pdf"
#     except Exception as e:
#         logger.error(f"Eroare parser MFP: {str(e)}")
#         return None, None

def download_template(url, local_path):
    if local_path.exists(): return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': GHIDURI_URL}
    try:
        with requests.get(url, headers=headers, timeout=60, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=32768):
                    f.write(chunk)
    except Exception as e:
        if local_path.exists(): local_path.unlink()
        raise e

@mfp_bp.route('/api/mfp/template_ord', methods=['GET', 'HEAD'])
@require_api_key
def get_cached_template_ord():
    """Returnează cel mai nou template ORD (Ordonantare) din cache."""
    try:
        # Cauta fisierele care incep cu ORD_V
        matches = sorted(CACHE_DIR.glob("ORD_V*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not matches:
            return jsonify({"error": "Nu există niciun template ORD în cache"}), 404

        pdf_path = matches[0]

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_path.name
        )
    except Exception as e:
        logger.error(f"Eroare get_cached_template_ord: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
        
@mfp_bp.route('/api/mfp/template_ddf', methods=['GET', 'HEAD'])
@require_api_key
def get_cached_template():
    """Returnează cel mai nou template PDF din cache (după data modificării fișierului)."""
    try:
        matches = sorted(CACHE_DIR.glob("DDF_V*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not matches:
            return jsonify({"error": "Nu există niciun template în cache"}), 404

        pdf_path = matches[0]

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_path.name
        )
    except Exception as e:
        logger.error(f"Eroare get_cached_template: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@mfp_bp.route('/api/mfp/get_ef_token', methods=['GET'])
@require_api_key
def get_ef_token():
    """Endpoint care trimite inapoi fisierul token.zip din radacina proiectului."""
    try:
        token_path = Path("token.zip")
        if not token_path.exists():
            return jsonify({"error": "Fisierul token.zip nu exista pe server"}), 404
        return send_file(token_path, mimetype='application/zip', as_attachment=True, download_name=token_path.name)
        
    except Exception as e:
        logger.error(f"Eroare get_ef_token: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# @mfp_bp.route('/api/mfp/xfa_writter', methods=['GET', 'HEAD'])
# @require_api_key
# def get_cached_xfa_writter():
#     """Returnează cel mai nou template XFA WRITTER din cache (după data modificării fișierului)."""
#     try:
#         exe_path = CACHE_DIR_VBNET / "XFA_WRITTER.exe"
#         if not exe_path.exists():
#             return jsonify({"error": "XFA_WRITTER.exe nu există pe server"}), 404
#         return send_file(exe_path, mimetype='application/octet-stream', as_attachment=True, download_name=exe_path.name)
        
#     except Exception as e:
#         logger.error(f"Eroare get_cached_xfa_writter: {str(e)}", exc_info=True)
#         return jsonify({"error": str(e)}), 500

# @mfp_bp.route('/api/mfp/xfa_writter/version', methods=['GET'])
# @require_api_key
# def get_xfa_writter_version():
#     """Returneaza versiunea din fisierul XFA_WRITTER.txt."""
#     try:
#         version_path = CACHE_DIR_VBNET / "XFA_WRITTER.txt"
        
#         if not version_path.exists():
#             return jsonify({"error": "Fisierul de versiune nu exista"}), 404
            
#         # Citeste continutul si elimina spatiile albe/new lines
#         version_data = version_path.read_text().strip()
        
#         return jsonify({
#             "version": version_data,
#             "filename": "xfa_writter.txt"
#         }), 200
        
#     except Exception as e:
#         logger.error(f"Eroare get_xfa_writter_version: {str(e)}", exc_info=True)
#         return jsonify({"error": str(e)}), 500
