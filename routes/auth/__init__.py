# routes/auth/__init__.py
"""
Blueprint AUTH — login-ul aplicatiei K-BOT (nu autentificarea FOREXE).

Expune `auth_bp`. Rutele sunt definite in submodulul `auth.py` si se
inregistreaza pe auth_bp prin importul de la final (acelasi tipar ca
routes/forexe/__init__.py).
"""
from .auth import auth_bp  # noqa: F401
