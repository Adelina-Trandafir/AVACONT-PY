"""
The public «PDF Inteligent» page of K-BOT: GET /alop-eroare-adobe-acrobat-pro
(the old address /pdf-inteligent answers with a permanent redirect to it)

A static article (Adobe Acrobat Reader vs. Pro for the ALOP forms of OMF 1.140/2025), opened
from the red button in the top bar of the presentation page. Pre-login, no database, no
cookies, no forms. Same page headers and same look (site.css) as the presentation page.
"""
import logging

from flask import Blueprint, abort, current_app, make_response, redirect, render_template

from routes.landing.landing import _load_content

logger = logging.getLogger(__name__)

pdf_inteligent_bp = Blueprint("pdf_inteligent", __name__, template_folder="templates")

_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-cache",
}


def _published():
    """True once content.json says "alop_page": {"published": true}; a draft is kept off search engines."""
    return bool(_load_content().get("alop_page", {}).get("published"))


@pdf_inteligent_bp.route("/pdf-inteligent", methods=["GET"])
@pdf_inteligent_bp.route("/pdf-inteligent/", methods=["GET"])
def pdf_inteligent_old():
    if not _published():
        abort(404)
    return redirect("/alop-eroare-adobe-acrobat-pro", code=301)


@pdf_inteligent_bp.route("/alop-eroare-adobe-acrobat-pro", methods=["GET"])
@pdf_inteligent_bp.route("/alop-eroare-adobe-acrobat-pro/", methods=["GET"])
def pdf_inteligent_page():
    try:
        published = _published()
        response = make_response(render_template("pdf_inteligent/index.html", published=published))
    except Exception:
        logger.exception("pdf_inteligent_page failed")
        return current_app.response_class("Eroare.", status=500, mimetype="text/plain")
    response.headers.update(_PAGE_HEADERS)
    if not published:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
