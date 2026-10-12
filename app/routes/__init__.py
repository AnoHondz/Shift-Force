from .api import api_bp
from .assistant import assistant_bp
from .attendance import attendance_bp
from .audit import audit_bp
from .auth import auth_bp
from .employees import employee_bp
from .leaves import leave_bp
from .notifications import notifications_bp
from .reports import reports_bp
from .workplace import workplace_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(employee_bp)
    app.register_blueprint(leave_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(workplace_bp)
