"""SLICE-WB01-01: entry policy for the K-BOT web application only."""

KBOT_WEB_ROLES = frozenset({"contabil", "director"})


def can_enter_kbot(role):
    return str(role or "").strip().casefold() in KBOT_WEB_ROLES
