"""Validation and public projection. Internal author keys never leave this module."""
from datetime import timezone
from uuid import UUID

APPS = ("V", "A", "K")
MAX_TEXT = 4000
MAX_ID = 18446744073709551615


class ChatError(Exception):
    def __init__(self, message, status=400, reason="CHAT_DATE_INVALIDE"):
        super().__init__(message)
        self.status = status
        self.reason = reason


def object_body(value):
    if not isinstance(value, dict):
        raise ChatError("Datele mesajului nu sunt valide.")
    return value


def number(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ChatError("Identificatorul mesajului nu este valid.")
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 20:
        raise ChatError("Identificatorul mesajului nu este valid.")
    result = int(text)
    if result < (0 if zero else 1) or result > MAX_ID:
        raise ChatError("Identificatorul mesajului nu este valid.")
    return result


def application(value):
    if not isinstance(value, str) or value not in APPS:
        raise ChatError("Aplicația mesajului nu este validă.")
    return value


def filters(value):
    if not isinstance(value, list) or not value or len(value) > 3:
        raise ChatError("Selectați cel puțin o aplicație.")
    for item in value:
        application(item)
    return "".join(app for app in APPS if app in value)


def boolean(value):
    if not isinstance(value, bool):
        raise ChatError("Alegeți trimiterea cu nume sau cu pseudonim.")
    return value


def display_name(value):
    if not isinstance(value, str) or len(value.strip()) > 80:
        raise ChatError("Numele afișat poate avea maximum 80 de caractere.")
    name = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ChatError("Numele afișat nu poate conține caractere de control.")
    return name


def message_body(body):
    object_body(body)
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ChatError(f"Scrieți un mesaj de maximum {MAX_TEXT} de caractere.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ChatError("Mesajul conține caractere de control nepermise.")
    try:
        key = str(UUID(body.get("client_key", "")))
    except (ValueError, TypeError, AttributeError):
        raise ChatError("Identificatorul trimiterii nu este valid.") from None
    anonymous = boolean(body.get("anonymous"))
    name = display_name(body.get("display_name", ""))
    if not anonymous and not name:
        raise ChatError("Completați numele afișat sau trimiteți cu pseudonim.")
    reply = body.get("reply_to")
    return {"text": text, "application": application(body.get("application")),
            "anonymous": anonymous, "display_name": name, "client_key": key,
            "reply_to": number(reply) if reply is not None else None}


def public_message(row, viewer):
    """Snapshot name for named posts; pseudonym only for anonymous posts, including replies.

    No account, user id, stable common author token, or unused identity accompanies either mode.
    The requester alone gets `mine`, needed to distinguish their own bubbles.
    """
    def projected(prefix=""):
        anonymous = bool(row[prefix + "ANONIM"])
        return {"id": str(row[prefix + "ID_MESAJ"]),
                "application": row[prefix + "APLICATIE"],
                "anonymous": anonymous,
                "author": row[prefix + "PSEUDONIM"] if anonymous else row[prefix + "NUME_AUTOR"],
                "text": row[prefix + "CONTINUT"]}

    result = projected()
    result["mine"] = row["ID_UTILIZATOR"] == viewer
    created = row["CREAT_LA"]
    result["created_at"] = created.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    result["reply"] = projected("R_") if row.get("R_ID_MESAJ") is not None else None
    if result["reply"]:
        result["reply"]["text"] = result["reply"]["text"][:240]
    return result
