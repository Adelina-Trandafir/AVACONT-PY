import logging
from logging.handlers import RotatingFileHandler
from flask import request, has_request_context

class RequestIPFilter(logging.Filter):
    def filter(self, record):
        if has_request_context():
            record.ip = request.remote_addr
        else:
            record.ip = '-'
        return True

def setup_logger():
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(ip)s - %(message)s')
    
    log_handler = RotatingFileHandler('api_server.log', maxBytes=10*1024*1024, backupCount=5)
    log_handler.setFormatter(log_formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    if not logger.handlers:
        ip_filter = RequestIPFilter()
        
        log_handler.addFilter(ip_filter)
        logger.addHandler(log_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_formatter)
        console_handler.addFilter(ip_filter)
        logger.addHandler(console_handler)

    logger.info("--- SERVER LOGGING INITIALIZED ---")
    return logger