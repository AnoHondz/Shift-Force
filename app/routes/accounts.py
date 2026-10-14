"""Store-scoped account controls and authenticator-based verification."""

import hashlib
import secrets
import time
from datetime import timedelta
from io import BytesIO

import pyotp
import qrcode
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user

from ..account_security import (
    consume_factor,
    decrypt_secret,
    encrypt_secret,
    factor_rate_key,
    matching_step,
    recovery_codes,
    require_recent_password,
)
from ..extensions import db, limiter
from ..models import (
    AccountReset,
    OperationalReview,
    PayrollRecord,
    Role,
    User,
    create_audit_log,
    create_notification,
    utcnow,
)
from ..stores import hub_url
from ..utils import valid_password
from .auth import get_dashboard_endpoint

accounts_bp = Blueprint("accounts", __name__, url_prefix="/account")
STAFF_ROLES = (Role.EMPLOYEE, Role.MANAGER, Role.HR, Role.PAYROLL)


def require_owner():
    if current_user.role not in {Role.ADMIN, Role.EXECUTIVE}:
        abort(403)


@accounts_bp.route("/operations", methods=("GET", "POST"))
@login_required
def operations():
    require_owner()
    from ..payroll_review import POLICY_SCOPE

    if request.method == "POST":
        require_recent_password()
        reference = request.form.get("reference", "").strip()
        checks = ("coverage", "rates", "workweek", "breaks", "records", "provider")
        if not 10 <= len(reference) <= 255 or any(
            request.form.get(check) != "on" for check in checks
        ):
            abort(400, description="Complete every review item and provide the evidence reference.")
        db.session.add(
            OperationalReview(
                review_type="virginia_payroll_policy",
                reference=reference,
                scope=POLICY_SCOPE,
                reviewed_by_id=current_user.id,
            )
        )
        create_audit_log(
            current_user,
            "payroll_policy_reviewed",
            "OperationalReview",
            None,
            "Owner recorded a Virginia hourly payroll review with an evidence reference. This does not certify legal compliance or reconcile imported rows.",
        )
        db.session.commit()
        flash("Payroll review recorded for the supported hourly policy.", "success")
        return redirect(url_for("accounts.operations"))
    reviews = (
        OperationalReview.query.filter_by(review_type="virginia_payroll_policy")
        .order_by(OperationalReview.id.desc())
        .limit(10)
        .all()
    )
    imported = (
        PayrollRecord.query.filter_by(legacy_import=True)
        .order_by(PayrollRecord.id)
        .limit(100)
        .all()
    )
    unresolved = PayrollRecord.query.filter(
        PayrollRecord.legacy_import.is_(True), PayrollRecord.status.notin_(["Rejected", "Voided"])
    ).count()
    return render_template(
        "operational_review.html",
        reviews=reviews,
        imported=imported,
        unresolved=unresolved,
        page_title="Payroll readiness",
        active_page="manage",
    )


@accounts_bp.route("/staff", methods=("GET", "POST"))
@login_required
@limiter.limit("20 per hour", methods=["POST"])
def staff():
    require_owner()
    reset_link = None
    if request.method == "POST":
        require_recent_password()
        target_id = request.form.get("user_id", "")
        if not target_id.isascii() or not target_id.isdigit() or len(target_id) > 18:
            abort(400, description="Choose a staff account.")
        target = db.get_or_404(User, int(target_id))
        info = current_app.config.get("STORE_INFO") or {}
        if (
            target.id == current_user.id
            or target.id == info.get("owner_account_id")
            or target.role not in STAFF_ROLES
            or not target.is_active
        ):
            abort(
                403,
                description="Owner and administrator accounts require their own security controls.",
            )
        action = request.form.get("action")
        if action == "role":
            role = request.form.get("role")
            if role not in STAFF_ROLES:
                abort(400, description="Choose an available staff role.")
            old = target.role
            target.role = role
            target.revoke_sessions()
            create_audit_log(
                current_user,
                "role_updated",
                "User",
                str(target.id),
                f"Staff access changed from {old} to {role}; existing sessions revoked.",
            )
            create_notification(
                target,
                "Account access changed",
                f"Your access is now {Role.label(role)}. Sign in again to use your account.",
                "/account/security",
                "security",
            )
            flash("Staff access updated. Existing sign-ins were removed.", "success")
        elif action == "reset":
            token = secrets.token_urlsafe(32)
            # Older links are invalidated immediately; the password changes only on acceptance.
            AccountReset.query.filter_by(user_id=target.id, consumed_at=None).update(
                {"consumed_at": utcnow()}
            )
            db.session.add(
                AccountReset(
                    user_id=target.id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    auth_token=target.auth_token,
                    created_by_id=current_user.id,
                    expires_at=utcnow() + timedelta(minutes=30),
                )
            )
            reset_link = url_for("accounts.reset", token=token, _external=True)
            create_audit_log(
                current_user,
                "password_reset_requested",
                "User",
                str(target.id),
                "A single-use, 30-minute password recovery link was created. Identity must be checked before handoff.",
            )
        elif action == "revoke":
            target.revoke_sessions()
            create_audit_log(
                current_user,
                "sessions_revoked",
                "User",
                str(target.id),
                "Owner revoked all staff sessions and outstanding password recovery links.",
            )
            flash("All sign-ins for this person were removed.", "success")
        else:
            abort(400, description="Choose an account action.")
        db.session.commit()
        if not reset_link:
            return redirect(url_for("accounts.staff"))
    users = User.query.join(User.employee).filter_by(archived=False).order_by(User.username).all()
    return render_template(
        "account_staff.html",
        users=users,
        staff_roles=STAFF_ROLES,
        reset_link=reset_link,
        page_title="Staff access",
        active_page="manage",
    )


@accounts_bp.route("/reset", methods=("GET", "POST"))
@limiter.limit("10 per hour", methods=["POST"])
def reset():
    token = request.args.get("token") or request.form.get("token", "")
    if len(token) != 43:
        abort(
            400,
            description="This recovery link is invalid or expired. Ask your store owner for a new one.",
        )
    record = AccountReset.query.filter_by(
        token_hash=hashlib.sha256(token.encode()).hexdigest(), consumed_at=None
    ).first()
    user = db.session.get(User, record.user_id) if record else None
    if (
        not record
        or record.expires_at <= utcnow()
        or not user
        or not user.is_active
        or user.auth_token != record.auth_token
    ):
        abort(
            400,
            description="This recovery link is invalid or expired. Ask your store owner for a new one.",
        )
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if not valid_password(password) or password != request.form.get("confirm_password"):
            error = "Use matching passwords with 12–128 characters."
        else:
            claimed = db.session.execute(
                db.update(AccountReset)
                .where(
                    AccountReset.id == record.id,
                    AccountReset.consumed_at.is_(None),
                    AccountReset.expires_at > utcnow(),
                )
                .values(consumed_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                db.session.rollback()
                abort(409, description="This recovery link was already used.")
            # Recheck account state under the same write transaction before mutation.
            db.session.refresh(user)
            if user.auth_token != record.auth_token or not user.is_active:
                db.session.rollback()
                abort(400, description="This recovery link is no longer valid.")
            user.set_password(password)
            create_audit_log(
                None,
                "password_reset",
                "User",
                str(user.id),
                "Staff accepted a single-use recovery link; sessions and other links revoked. MFA retained.",
            )
            db.session.commit()
            flash("Password changed. Sign in with your new password.", "success")
            return redirect(url_for("auth.login"))
    return render_template("account_reset.html", token=token, error=error)


@accounts_bp.route("/verify", methods=("GET", "POST"))
@limiter.limit("100 per minute", methods=["POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=factor_rate_key)
def mfa():
    pending = session.get("pending_login_token")
    user = (
        User.query.filter_by(auth_token=pending).first()
        if pending and time.time() - session.get("pending_login_at", 0) <= 300
        else None
    )
    if current_user.is_authenticated:
        user = current_user._get_current_object()
    if not user or not user.is_active or not user.mfa_secret:
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        if consume_factor(user, request.form.get("code", "").strip()):
            remember = bool(session.get("pending_remember")) if pending else session.permanent
            next_url = session.get("pending_next") if pending else None
            token = user.auth_token
            session.clear()
            session.permanent = remember
            login_user(user, remember=remember)
            session.update(mfa_verified=token, mfa_verified_at=time.time())
            create_audit_log(
                user, "mfa_verified", "User", str(user.id), "Two-step verification completed."
            )
            db.session.commit()
            return redirect(next_url or url_for(get_dashboard_endpoint(user)))
        db.session.rollback()
        error = "That code is invalid or already used. Use a new code or an unused recovery code."
    return render_template("account_mfa.html", error=error)


@accounts_bp.route("/security", methods=("GET", "POST"))
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def security():
    codes = None
    user = current_user._get_current_object()
    info = current_app.config.get("STORE_INFO")
    if info and user.id == info["owner_account_id"]:
        return redirect(hub_url("/account/security"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "begin":
            require_recent_password()
            if user.mfa_secret:
                abort(400, description="Two-step verification is already enabled.")
            user.mfa_pending_secret = encrypt_secret(pyotp.random_base32())
            db.session.commit()
            return redirect(url_for("accounts.security"))
        if action == "enable":
            require_recent_password()
            step = (
                matching_step(
                    decrypt_secret(user.mfa_pending_secret), request.form.get("code", "").strip()
                )
                if user.mfa_pending_secret
                else None
            )
            if step is None:
                abort(400, description="Enter the current six-digit code from your authenticator.")
            user.mfa_secret, user.mfa_pending_secret, user.mfa_last_step = (
                user.mfa_pending_secret,
                None,
                step,
            )
            codes, user.mfa_recovery_hashes = recovery_codes()
            user.revoke_sessions()
            create_audit_log(
                user,
                "mfa_enabled",
                "User",
                str(user.id),
                "Authenticator verification enabled; recovery codes issued and other sessions revoked.",
            )
        elif action == "password":
            require_recent_password()
            password = request.form.get("password", "")
            if not valid_password(password) or password != request.form.get("confirm_password"):
                abort(400, description="Use matching passwords with 12–128 characters.")
            user.set_password(password)
            create_audit_log(
                user,
                "password_changed",
                "User",
                str(user.id),
                "Account password changed; other sessions and recovery links revoked.",
            )
        else:
            abort(400, description="Choose a security action.")
        db.session.commit()
        remember = session.permanent
        session.clear()
        login_user(user, remember=remember)
        session.permanent = remember
        if user.mfa_secret:
            session.update(mfa_verified=user.auth_token, mfa_verified_at=time.time())
        if not codes:
            flash("Password changed. Other sign-ins were removed.", "success")
            return redirect(url_for("accounts.security"))
    secret = decrypt_secret(user.mfa_pending_secret) if user.mfa_pending_secret else None
    return render_template(
        "account_security.html",
        secret=secret,
        recovery=codes,
        page_title="Account security",
        active_page="account",
    )


@accounts_bp.get("/authenticator.png")
@login_required
def qr():
    if not current_user.mfa_pending_secret:
        abort(404)
    secret = decrypt_secret(current_user.mfa_pending_secret)
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=current_user.username,
        issuer_name="Shiftspace"
        + (
            " · " + current_app.config["STORE_SLUG"] if current_app.config.get("STORE_SLUG") else ""
        ),
    )
    output = BytesIO()
    qrcode.make(uri).save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png")


@accounts_bp.route("/confirm", methods=("GET", "POST"))
@login_required
@limiter.limit("10 per minute", methods=["POST"])
def confirm():
    info = current_app.config.get("STORE_INFO")
    if info and current_user.id == info["owner_account_id"] and request.method == "POST":
        token = session.get("hub_owner_token")
        hub = current_app.extensions["store_hub"]
        with hub.app_context():
            owner = db.session.get(User, info["owner_id"])
            password = request.form.get("current_password", "")
            if (
                not owner
                or not owner.is_active
                or owner.auth_token != token
                or len(password) > 128
                or not owner.check_password(password)
            ):
                abort(403, description="Enter your owner account password.")
            if owner.mfa_secret and not consume_factor(owner, request.form.get("code", "").strip()):
                db.session.rollback()
                abort(403, description="Use a new authenticator code or an unused recovery code.")
            session.update(hub_mfa_verified=bool(owner.mfa_secret), hub_mfa_verified_at=time.time())
            db.session.commit()
        return redirect(url_for("accounts.staff"))
    if request.method == "POST":
        password = request.form.get("current_password", "")
        if len(password) > 128 or not current_user.check_password(password):
            abort(403, description="Enter your current password.")
        if current_user.mfa_secret and not consume_factor(
            current_user, request.form.get("code", "").strip()
        ):
            db.session.rollback()
            abort(403, description="Use a new authenticator code or an unused recovery code.")
        session.update(mfa_verified=current_user.auth_token, mfa_verified_at=time.time())
        db.session.commit()
        return redirect(url_for("accounts.security"))
    owner_mfa = False
    if info and current_user.id == info["owner_account_id"]:
        with current_app.extensions["store_hub"].app_context():
            owner_mfa = bool(db.session.get(User, info["owner_id"]).mfa_secret)
    return render_template(
        "account_confirm.html",
        page_title="Confirm your identity",
        require_code=owner_mfa or bool(current_user.mfa_secret),
    )
