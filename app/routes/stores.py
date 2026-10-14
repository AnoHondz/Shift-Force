"""Owner locations and employee invitations; workplace records stay in each store."""

import re
import secrets
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user
from sqlalchemy import or_

from ..extensions import db, limiter
from ..models import Employee, Role, Store, User, create_audit_log
from ..stores import STORE_CODE, hub_url, invite_serializer, store_snapshot

stores_bp = Blueprint("stores", __name__)


def require_hub_owner():
    if current_app.config.get("STORE_SLUG"):
        abort(404)
    if current_user.role not in {Role.OWNER, Role.ADMIN, Role.EXECUTIVE}:
        abort(403)


@stores_bp.get("/stores/find")
def find():
    if current_app.config.get("STORE_SLUG"):
        return redirect(hub_url("/stores/find"))
    query = request.args.get("q", "").strip()
    if len(query) > 100:
        abort(
            400,
            description="Search with a business name, street, or store code under 100 characters.",
        )
    matches = []
    if query:
        stores = Store.query
        for word in query.split():
            pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stores = stores.filter(
                or_(
                    Store.name.ilike(pattern, escape="\\"),
                    Store.location.ilike(pattern, escape="\\"),
                    Store.slug.ilike(pattern, escape="\\"),
                )
            )
        matches = stores.order_by(Store.name, Store.location).limit(12).all()
    return render_template(
        "find_store.html", page_title="Find your store", matches=matches, query=query
    )


@stores_bp.route("/stores", methods=("GET", "POST"))
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def index():
    require_hub_owner()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        slug = request.form.get("slug", "").strip().lower()
        if not slug:
            text = (
                unicodedata.normalize("NFKD", name + " " + location)
                .encode("ascii", "ignore")
                .decode()
                .replace("'", "")
            )
            slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].rstrip("-")
            if len(slug) < 3:
                slug = "store-" + secrets.token_hex(4)
        zone = request.form.get("timezone", "America/New_York")
        if (
            not 1 <= len(name) <= 100
            or not 1 <= len(location) <= 160
            or not STORE_CODE.fullmatch(slug)
        ):
            abort(
                400,
                description="Enter a business name, location, and a 3–60 character store code using letters, numbers, and hyphens.",
            )
        try:
            if len(zone) > 64:
                raise ValueError
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            abort(400, description="Choose a valid time zone.")
        if Store.query.filter_by(slug=slug).first():
            abort(
                409,
                description="That store code is already in use. Add a street or location to make it unique.",
            )
        employee = current_user.employee
        owner = dict(
            username=current_user.username,
            first_name=employee.first_name if employee else current_user.username,
            last_name=employee.last_name if employee else "Owner",
            email=employee.email if employee else f"owner-{current_user.id}@example.invalid",
        )
        store = Store(
            owner_id=current_user.id,
            name=name,
            location=location,
            slug=slug,
            timezone=zone,
            database_key=secrets.token_hex(16),
            owner_account_id=1,
        )
        db.session.add(store)
        db.session.flush()
        info = store_snapshot(store)
        router = current_app.extensions["stores"]
        child = router.provision(info, owner)
        create_audit_log(
            current_user,
            "store_created",
            "Store",
            str(store.id),
            f"Created {name}, {location}; store code {slug}.",
        )
        db.session.commit()
        with router.lock:
            router.apps[info["database_key"]] = child
        flash("Store ready. Open it to add staff, shifts, and channels.", "success")
        return redirect(url_for("stores.index"))
    stores = (
        Store.query.filter_by(owner_id=current_user.id).order_by(Store.created_at, Store.id).all()
    )
    return render_template(
        "stores.html", stores=stores, active_page="stores", page_title="My stores"
    )


@stores_bp.post("/stores/<slug>/open")
@login_required
def open_store(slug):
    require_hub_owner()
    store = Store.query.filter_by(slug=slug, owner_id=current_user.id).first_or_404()
    info = store_snapshot(store)
    router = current_app.extensions["stores"]
    child = router.get_app(slug)
    response = redirect(request.script_root + "/s/" + slug + "/dashboard")
    user_agent = request.headers.get("User-Agent", "")
    remote_address = request.remote_addr
    hub_token = current_user.auth_token
    hub_mfa_verified = session.get("mfa_verified") == hub_token
    hub_mfa_verified_at = session.get("mfa_verified_at", 0)
    # The hub's CSRF-protected, ownership-checked POST issues only this store's cookie.
    with child.test_request_context(
        "/",
        method="POST",
        headers={"User-Agent": user_agent},
        environ_base={"REMOTE_ADDR": remote_address},
    ):
        account = db.session.get(User, info["owner_account_id"])
        if not account or not account.is_active:
            abort(403, description="Your store account is inactive.")
        session.clear()
        session.update(
            hub_owner_token=hub_token,
            hub_mfa_verified=hub_mfa_verified,
            hub_mfa_verified_at=hub_mfa_verified_at,
        )
        login_user(account, remember=False)
        create_audit_log(
            account,
            "store_opened",
            "Store",
            str(info["id"]),
            "Owner opened this location from My stores.",
        )
        db.session.commit()
        child.session_interface.save_session(child, session, response)
    return response


@stores_bp.post("/employees/<int:employee_id>/invite")
@login_required
@limiter.limit("20 per hour")
def invite(employee_id):
    if not current_app.config.get("STORE_SLUG"):
        abort(404)
    if current_user.role not in Role.PEOPLE_EDIT:
        abort(403)
    employee = db.get_or_404(Employee, employee_id)
    if employee.archived or employee.user:
        abort(409, description="Choose an active employee who does not already have an account.")
    token = invite_serializer().dumps({"employee_id": employee.id})
    link = url_for("auth.signup", invite=token, _external=True)
    create_audit_log(
        current_user,
        "store_invite_created",
        "Employee",
        str(employee.id),
        "Created a seven-day invitation for this store.",
    )
    db.session.commit()
    return render_template(
        "store_invite.html", employee=employee, invite_link=link, page_title="Invite coworker"
    )
