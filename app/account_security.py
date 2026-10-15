"""Encrypted authenticator secrets and atomic, single-use second factors."""

import base64
import hashlib
import hmac
import json
import secrets
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, redirect, request, session, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user

from .extensions import db
from .models import Role, User


def account_rate_key(identity):
    """Keep shared-IP users independent without putting account names in Redis keys."""
    value = json.dumps([get_remote_address(), identity], ensure_ascii=True).encode()
    return hmac.new(current_app.secret_key.encode(), value, hashlib.sha256).hexdigest()


def login_rate_key():
    return account_rate_key(request.form.get("username", "").strip().casefold())


def factor_rate_key():
    identity = (
        current_user.get_id()
        if current_user.is_authenticated
        else session.get("pending_login_token", "anonymous")
    )
    return account_rate_key(identity)


def cipher():
    key = current_app.config.get("MFA_ENCRYPTION_KEY")
    if not key:
        # Development only: production requires an independently retained key.
        key = base64.urlsafe_b64encode(hashlib.sha256(current_app.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret):
    return cipher().encrypt(secret.encode()).decode()


def decrypt_secret(encrypted):
    try:
        return cipher().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        abort(
            503,
            description="Account security is temporarily unavailable. Contact your administrator.",
        )


def recovery_codes():
    codes = [secrets.token_hex(8) for _ in range(10)]
    hashes = [hashlib.sha256(code.encode()).hexdigest() for code in codes]
    return codes, json.dumps(hashes)


def matching_step(secret, code):
    if not code.isascii() or not code.isdigit() or len(code) != 6:
        return None
    step = int(time.time()) // 30
    totp = pyotp.TOTP(secret)
    for candidate in (step, step - 1, step + 1):
        if hmac.compare_digest(totp.at(candidate * 30), code):
            return candidate
    return None


def consume_factor(user, code):
    """Conditional writes prevent reuse across processes, not only within a session."""
    if not user.mfa_secret or len(code) > 100:
        return False
    step = matching_step(decrypt_secret(user.mfa_secret), code)
    if step is not None:
        result = db.session.execute(
            db.update(User)
            .where(
                User.id == user.id,
                User.auth_token == user.auth_token,
                User.mfa_secret == user.mfa_secret,
                User.mfa_last_step < step,
            )
            .values(mfa_last_step=step)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
    stored = user.mfa_recovery_hashes or "[]"
    hashes = json.loads(stored)
    digest = hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()
    if not any(hmac.compare_digest(digest, item) for item in hashes):
        return False
    result = db.session.execute(
        db.update(User)
        .where(
            User.id == user.id,
            User.auth_token == user.auth_token,
            User.mfa_recovery_hashes == stored,
        )
        .values(mfa_recovery_hashes=json.dumps([item for item in hashes if item != digest]))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def mfa_required(user):
    return bool(
        current_app.config.get(
            "REQUIRE_PRIVILEGED_MFA", current_app.config.get("APP_ENV") == "production"
        )
        and user.role in (*Role.ADMIN_LIKE, Role.OWNER)
    )


def security_gate():
    if not current_user.is_authenticated:
        return None
    exempt = {
        "auth.logout",
        "accounts.security",
        "accounts.confirm",
        "accounts.qr",
        "accounts.mfa",
        "static",
        "ops.health",
        "ops.ready",
    }
    if request.endpoint in exempt:
        return None
    # Store owner entry validates the hub's active account and MFA on every request.
    info = current_app.config.get("STORE_INFO")
    if info and current_user.id == info["owner_account_id"]:
        return None
    if mfa_required(current_user) and not current_user.mfa_secret:
        if request.is_json or request.path.startswith("/api/"):
            abort(403, description="Set up two-step verification before using workplace tools.")
        return redirect(url_for("accounts.security"))
    if current_user.mfa_secret and session.get("mfa_verified") != current_user.auth_token:
        if request.is_json or request.path.startswith("/api/"):
            abort(403, description="Complete two-step verification.")
        return redirect(url_for("accounts.mfa"))
    return None


def require_recent_password():
    password = request.form.get("current_password", "")
    info = current_app.config.get("STORE_INFO")
    if info and current_user.id == info["owner_account_id"]:
        verified = session.get("hub_mfa_verified")
        verified_at = session.get("hub_mfa_verified_at", 0)
        token = session.get("hub_owner_token")
        hub = current_app.extensions["store_hub"]
        with hub.app_context():
            owner = db.session.get(User, info["owner_id"])
            if (
                not owner
                or not owner.is_active
                or owner.auth_token != token
                or len(password) > 128
                or not owner.check_password(password)
            ):
                abort(403, description="Enter your owner account password to confirm this change.")
            if owner.mfa_secret and (not verified or time.time() - verified_at > 300):
                abort(
                    403, description="Confirm two-step verification again before changing accounts."
                )
        return
    if len(password) > 128 or not current_user.check_password(password):
        abort(403, description="Enter your current password to confirm this change.")
    if current_user.mfa_secret and (
        session.get("mfa_verified") != current_user.auth_token
        or time.time() - session.get("mfa_verified_at", 0) > 300
    ):
        abort(403, description="Confirm two-step verification again before changing accounts.")
