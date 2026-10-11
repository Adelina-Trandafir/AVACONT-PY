"""
robots.txt and sitemap.xml of the public pages of K-BOT (k-bot.ro).

Only the pages meant for search engines are listed; the portal, the API and the
registration steps are kept out.
"""
from flask import Blueprint, Response

from routes.landing.landing import _load_content

seo_bp = Blueprint("seo", __name__)

_BASE = "https://k-bot.ro"
_PAGES = [
    ("/", "1.0"),
    ("/detalii", "0.4"),
]
# Listed only once content.json says "alop_page": {"published": true}.
_ALOP = ("/alop-eroare-adobe-acrobat-pro", "0.9")


@seo_bp.route("/robots.txt", methods=["GET"])
def robots():
    body = (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /portal\n"
        "Disallow: /inregistrare\n"
        "Allow: /\n"
        "\n"
        "Sitemap: " + _BASE + "/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@seo_bp.route("/sitemap.xml", methods=["GET"])
def sitemap():
    pages = list(_PAGES)
    if _load_content().get("alop_page", {}).get("published"):
        pages.insert(1, _ALOP)
    rows = "".join(
        "<url><loc>%s%s</loc><priority>%s</priority></url>" % (_BASE, path, prio)
        for path, prio in pages
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + rows + "</urlset>"
    )
    return Response(xml, mimetype="application/xml")
