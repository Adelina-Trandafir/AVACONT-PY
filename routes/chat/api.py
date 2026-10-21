"""SLICE-CHAT-02. Portal identity only, independent of unit/application permissions.

Uses the portal's existing deadline check without sliding/renewing it: chat polling
must never keep an otherwise expired session alive. No cookies or anonymous access.
"""
from functools import wraps
import logging
import traceback

import mysql.connector
from flask import Blueprint, g, jsonify, request

from routes.auth.guard import require_session_no_touch
from routes.portal.portal import _live_session
from . import contract
from .repository import session

chat_bp = Blueprint("chat", __name__, url_prefix="/api/chat")
logger = logging.getLogger(__name__)


def error_location(error):
    """Code locations only: no exception text, SQL, source text, locals or account data."""
    frames = traceback.extract_tb(error.__traceback__)
    return " > ".join(f"{frame.name}:{frame.lineno}" for frame in frames)


def authorized(fn):
    """Two doors, one chat. The web pages come with the portal token (X-Portal-Token); K-BOT.app
    comes with its bearer (Authorization: Bearer). Both end in the same account string, so one
    person has one identity, whichever door they used. Neither door slides the session."""
    @require_session_no_touch
    def by_bearer(*args, **kwargs):
        return serve(fn, g.session.username, args, kwargs)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        if request.headers.get("Authorization"):
            return by_bearer(*args, **kwargs)
        _, note, refusal = _live_session()
        if refusal is not None:
            return refusal
        return serve(fn, note["email"], args, kwargs)
    return wrapped


def serve(fn, account, args, kwargs):
    try:
        if request.content_length and request.content_length > 32768:
            raise contract.ChatError("Datele mesajului sunt prea mari.", 413)
        for attempt in range(3):
            try:
                with session(account) as repo:
                    payload, status = fn(repo, *args, **kwargs)
                break
            except mysql.connector.Error as error:
                # A first-time membership FK and a concurrent conversation send
                # may contend. Retry only fully rolled-back transactions.
                if error.errno not in (1205, 1213) or attempt == 2:
                    raise
        return jsonify(payload), status
    except contract.ChatError as error:
        if error.status >= 500:
            logger.error("chat unavailable (operation=%s reason=%s)", fn.__name__, error.reason)
        return jsonify(error=str(error), reason=error.reason), error.status
    except mysql.connector.Error as error:
        # No SQL, parameters, account strings, message bodies or author mapping in logs/responses.
        logger.error("chat database unavailable (operation=%s errno=%s sqlstate=%s location=%s)",
                     fn.__name__, error.errno, error.sqlstate, error_location(error))
        return jsonify(error="Chatul nu este momentan disponibil.", reason="CHAT_INDISPONIBIL"), 503
    except Exception as error:
        logger.error("chat request failed (operation=%s type=%s location=%s)",
                     fn.__name__, type(error).__name__, error_location(error))
        return jsonify(error="Chatul nu este momentan disponibil.", reason="CHAT_INDISPONIBIL"), 503


@chat_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def body():
    return contract.object_body(request.get_json(silent=True))


@chat_bp.get("/state")
@authorized
def state(repo):
    return repo.state(), 200


@chat_bp.get("/messages")
@authorized
def messages(repo):
    before = request.args.get("before")
    after = request.args.get("after")
    if before is not None and after is not None:
        raise contract.ChatError("Alegeți o singură direcție pentru istoric.")
    limit = contract.number(request.args.get("limit", "50"))
    if limit > 100:
        raise contract.ChatError("Se pot cere maximum 100 de mesaje odată.")
    result = repo.messages(before=contract.number(before) if before is not None else None,
                           after=contract.number(after, zero=True) if after is not None else None, limit=limit)
    result["state"] = repo.state()
    return result, 200


@chat_bp.post("/messages")
@authorized
def send(repo):
    message, created = repo.send(contract.message_body(body()))
    return {"message": message, "created": created, "state": repo.state()}, 201 if created else 200


@chat_bp.post("/preferences")
@authorized
def preferences(repo):
    data = body()
    apps = contract.filters(data["applications"]) if "applications" in data else None
    anonymous = contract.boolean(data["anonymous"]) if "anonymous" in data else None
    name = contract.display_name(data["display_name"]) if "display_name" in data else None
    repo.preferences_save(apps, anonymous, name)
    return repo.state(), 200


@chat_bp.post("/read")
@authorized
def read(repo):
    data = body().get("frontiers")
    if not isinstance(data, dict) or len(data) > 3:
        raise contract.ChatError("Poziția de citire nu este validă.")
    frontiers = {contract.application(app): contract.number(value, zero=True) for app, value in data.items()}
    repo.read(frontiers)
    return repo.state(), 200
