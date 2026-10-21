"""Transactional chat storage on the K-BOT server. DML only; schema is supplied as SQL.

Lock order: identity -> participant -> conversation (send only). Conversation locking
serializes message commits so an after-cursor cannot skip a slower concurrent insert.
"""
from contextlib import contextmanager
import secrets

from utils.database import get_kbot_connection
from .contract import APPS, ChatError, public_message

MESSAGE_SELECT = """
SELECT M.*, U.PSEUDONIM,
       R.ID_MESAJ AS R_ID_MESAJ, R.APLICATIE AS R_APLICATIE,
       R.ANONIM AS R_ANONIM, R.NUME_AUTOR AS R_NUME_AUTOR,
       R.CONTINUT AS R_CONTINUT, RU.PSEUDONIM AS R_PSEUDONIM
FROM CHAT_MESAJE M
JOIN CHAT_IDENTITATI U ON U.ID_UTILIZATOR = M.ID_UTILIZATOR
LEFT JOIN CHAT_MESAJE R ON R.ID_MESAJ = M.RASPUNS_LA
    AND R.ID_CONVERSATIE = M.ID_CONVERSATIE
LEFT JOIN CHAT_IDENTITATI RU ON RU.ID_UTILIZATOR = R.ID_UTILIZATOR
"""


@contextmanager
def session(account):
    conn = get_kbot_connection("AVACONT_COMUN")
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        # UTC is per connection; never changes global server settings.
        cur.execute("SET time_zone = '+00:00'")
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cur.execute("SELECT ID_CONVERSATIE, TITLU FROM CHAT_CONVERSATII WHERE CHEIE = 'COMUN' AND TIP = 'COMUN'")
        conversation = cur.fetchone()
        if not conversation:
            raise ChatError("Chatul nu este încă disponibil.", 503, "CHAT_CONVERSATIE_LIPSA")
        # Random pseudonym, independent of internal ids and of the account string.
        for _ in range(3):
            cur.execute("INSERT INTO CHAT_IDENTITATI (CONT, PSEUDONIM) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE ID_UTILIZATOR = ID_UTILIZATOR",
                        (account, "Utilizator " + secrets.token_hex(6).upper()))
            cur.execute("SELECT ID_UTILIZATOR, PSEUDONIM, NUME_AFISAT FROM CHAT_IDENTITATI "
                        "WHERE CONT = %s FOR UPDATE", (account,))
            identity = cur.fetchone()
            if identity:
                break
        else:
            raise ChatError("Chatul nu este încă disponibil.", 503, "CHAT_INDISPONIBIL")
        ids = (conversation["ID_CONVERSATIE"], identity["ID_UTILIZATOR"])
        cur.execute("INSERT INTO CHAT_PARTICIPANTI (ID_CONVERSATIE, ID_UTILIZATOR) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE ID_UTILIZATOR = ID_UTILIZATOR", ids)
        cur.execute("SELECT FILTRU_APLICATII, ANONIM_IMPLICIT FROM CHAT_PARTICIPANTI "
                    "WHERE ID_CONVERSATIE = %s AND ID_UTILIZATOR = %s FOR UPDATE", ids)
        preferences = cur.fetchone()
        for app in APPS:
            cur.execute("INSERT INTO CHAT_CITIRI (ID_CONVERSATIE, ID_UTILIZATOR, APLICATIE) "
                        "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE ID_UTILIZATOR = ID_UTILIZATOR", ids + (app,))
        yield Repository(cur, conversation, identity, preferences)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


class Repository:
    def __init__(self, cur, conversation, identity, preferences):
        self.cur = cur
        self.conversation = conversation
        self.identity = identity
        self.preferences = preferences
        self.cid = conversation["ID_CONVERSATIE"]
        self.uid = identity["ID_UTILIZATOR"]

    def state(self):
        self.cur.execute("""
            SELECT C.APLICATIE, C.ULTIMUL_MESAJ,
                   COUNT(M.ID_MESAJ) AS NECITITE
            FROM CHAT_CITIRI C
            LEFT JOIN CHAT_MESAJE M ON M.ID_CONVERSATIE = C.ID_CONVERSATIE
                AND M.APLICATIE = C.APLICATIE AND M.ID_MESAJ > C.ULTIMUL_MESAJ
                AND M.ID_UTILIZATOR <> C.ID_UTILIZATOR
            WHERE C.ID_CONVERSATIE = %s AND C.ID_UTILIZATOR = %s
            GROUP BY C.APLICATIE, C.ULTIMUL_MESAJ
        """, (self.cid, self.uid))
        rows = self.cur.fetchall()
        apps = [app for app in APPS if app in self.preferences["FILTRU_APLICATII"]]
        counts = {row["APLICATIE"]: int(row["NECITITE"]) for row in rows}
        return {"title": self.conversation["TITLU"],
                "self": {"pseudonym": self.identity["PSEUDONIM"], "display_name": self.identity["NUME_AFISAT"]},
                "preferences": {"applications": apps, "anonymous": bool(self.preferences["ANONIM_IMPLICIT"])},
                "unread": sum(counts.get(app, 0) for app in apps), "unread_by_application": counts,
                "read_by_application": {row["APLICATIE"]: str(row["ULTIMUL_MESAJ"]) for row in rows}}

    def messages(self, before=None, after=None, limit=50):
        apps = list(self.preferences["FILTRU_APLICATII"])
        marks = ",".join(["%s"] * len(apps))
        where = f" WHERE M.ID_CONVERSATIE = %s AND M.APLICATIE IN ({marks})"
        values = [self.cid, *apps]
        if before is not None:
            where += " AND M.ID_MESAJ < %s"
            values.append(before)
        if after is not None:
            where += " AND M.ID_MESAJ > %s"
            values.append(after)
        direction = "ASC" if after is not None else "DESC"
        self.cur.execute(MESSAGE_SELECT + where + f" ORDER BY M.ID_MESAJ {direction} LIMIT %s", (*values, limit + 1))
        rows = self.cur.fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if after is None:
            rows.reverse()
        return {"messages": [public_message(row, self.uid) for row in rows], "has_more": more}

    def preferences_save(self, apps=None, anonymous=None, name=None):
        if apps is not None:
            self.preferences["FILTRU_APLICATII"] = apps
        if anonymous is not None:
            self.preferences["ANONIM_IMPLICIT"] = int(anonymous)
        self.cur.execute("UPDATE CHAT_PARTICIPANTI SET FILTRU_APLICATII = %s, ANONIM_IMPLICIT = %s "
                         "WHERE ID_CONVERSATIE = %s AND ID_UTILIZATOR = %s",
                         (self.preferences["FILTRU_APLICATII"], self.preferences["ANONIM_IMPLICIT"], self.cid, self.uid))
        if name is not None:
            self.cur.execute("UPDATE CHAT_IDENTITATI SET NUME_AFISAT = %s WHERE ID_UTILIZATOR = %s", (name, self.uid))
            self.identity["NUME_AFISAT"] = name

    def send(self, data):
        self.cur.execute("SELECT ID_CONVERSATIE FROM CHAT_CONVERSATII WHERE ID_CONVERSATIE = %s FOR UPDATE", (self.cid,))
        self.cur.fetchone()
        self.cur.execute("SELECT ID_MESAJ, ID_CONVERSATIE, CONTINUT, APLICATIE, ANONIM, NUME_AUTOR, RASPUNS_LA FROM CHAT_MESAJE "
                         "WHERE ID_UTILIZATOR = %s AND CHEIE_CLIENT = %s", (self.uid, data["client_key"]))
        existing = self.cur.fetchone()
        if existing:
            if existing["ID_CONVERSATIE"] != self.cid:
                raise ChatError("Această trimitere a fost deja folosită în altă conversație.", 409)
            expected = (data["text"], data["application"], int(data["anonymous"]),
                        "" if data["anonymous"] else data["display_name"], data["reply_to"])
            actual = tuple(existing[key] for key in ("CONTINUT", "APLICATIE", "ANONIM", "NUME_AUTOR", "RASPUNS_LA"))
            if actual != expected:
                raise ChatError("Această trimitere a fost deja folosită pentru alt mesaj.", 409)
            return self.message(existing["ID_MESAJ"]), False
        # Identity row locking also serializes sends from different tabs for this account.
        self.cur.execute("SELECT ID_MESAJ FROM CHAT_MESAJE WHERE ID_UTILIZATOR = %s "
                         "AND CREAT_LA > UTC_TIMESTAMP(6) - INTERVAL 2 SECOND LIMIT 1", (self.uid,))
        if self.cur.fetchone():
            raise ChatError("Așteptați două secunde între mesaje.", 429, "CHAT_PREA_REPEDE")
        if data["reply_to"] is not None:
            self.cur.execute("SELECT ID_MESAJ FROM CHAT_MESAJE WHERE ID_MESAJ = %s AND ID_CONVERSATIE = %s",
                             (data["reply_to"], self.cid))
            if not self.cur.fetchone():
                raise ChatError("Mesajul la care răspundeți nu este disponibil.", 404)
        self.cur.execute("""
            INSERT INTO CHAT_MESAJE
                (ID_CONVERSATIE, ID_UTILIZATOR, APLICATIE, ANONIM, NUME_AUTOR, CONTINUT, RASPUNS_LA, CHEIE_CLIENT)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (self.cid, self.uid, data["application"], int(data["anonymous"]),
              "" if data["anonymous"] else data["display_name"], data["text"], data["reply_to"], data["client_key"]))
        message_id = self.cur.lastrowid
        self.preferences_save(anonymous=data["anonymous"], name=data["display_name"])
        return self.message(message_id), True

    def message(self, message_id):
        self.cur.execute(MESSAGE_SELECT + " WHERE M.ID_MESAJ = %s AND M.ID_CONVERSATIE = %s", (message_id, self.cid))
        return public_message(self.cur.fetchone(), self.uid)

    def read(self, frontiers):
        for app, message_id in frontiers.items():
            # Mark only this application's messages; never advance a hidden application's watermark.
            if app not in self.preferences["FILTRU_APLICATII"]:
                continue
            self.cur.execute("SELECT MAX(ID_MESAJ) AS ULTIMUL FROM CHAT_MESAJE "
                             "WHERE ID_CONVERSATIE = %s AND APLICATIE = %s AND ID_MESAJ <= %s", (self.cid, app, message_id))
            capped = self.cur.fetchone()["ULTIMUL"] or 0
            self.cur.execute("UPDATE CHAT_CITIRI SET ULTIMUL_MESAJ = GREATEST(ULTIMUL_MESAJ, %s), "
                             "CITIT_LA = UTC_TIMESTAMP(6) WHERE ID_CONVERSATIE = %s AND ID_UTILIZATOR = %s AND APLICATIE = %s",
                             (capped, self.cid, self.uid, app))
