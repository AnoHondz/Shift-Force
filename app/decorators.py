from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role not in roles:
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("employees.dashboard"))
            return view(*args, **kwargs)

        return wrapped_view

    return decorator
