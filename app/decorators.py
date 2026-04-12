from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def _unauthorized_redirect():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if current_user.has_admin_access():
        return redirect(url_for("employees.dashboard"))
    return redirect(url_for("employees.employee_dashboard"))


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role not in roles:
                flash("You do not have permission to access that page.", "error")
                return _unauthorized_redirect()
            return view(*args, **kwargs)

        return wrapped_view

    return decorator
