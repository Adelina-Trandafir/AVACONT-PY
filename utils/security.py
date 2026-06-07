from functools import wraps
from flask import request, jsonify
import logging
from config import API_KEY

logger = logging.getLogger(__name__)

def require_api_key(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        client_ip = request.remote_addr
        endpoint = request.path
        
        if request.headers.get('X-Api-Key') != API_KEY:
            logger.warning(f"ACCES REFUZAT! IP: {client_ip} a incercat sa acceseze {endpoint} cu cheie gresita.")
            return jsonify({"error": "Unauthorized"}), 401
        
        return func(*args, **kwargs)
    return wrapper