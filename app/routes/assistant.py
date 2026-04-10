from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required

assistant_bp = Blueprint("assistant", __name__)


@assistant_bp.route("/assistant/api", methods=("POST",))
@login_required
def api():
    payload = request.get_json(silent=True) or {}
    question = payload.get("question", "").strip().lower()
    if not question:
        return jsonify({"answer": "Please enter a question or choose a quick action.", "quick_actions": []})

    quick_actions = [
        {"label": "View Payroll", "url": url_for("employees.payroll")},
        {"label": "Submit Leave", "url": url_for("leave.submit")},
        {"label": "View Attendance", "url": url_for("attendance.index")},
        {"label": "Update Profile", "url": url_for("employees.profile")},
    ]

    if "leave" in question:
        return jsonify({
            "answer": "To submit leave, use the Leave Requests page and complete the request form. Managers and admins can approve or reject requests from the same page.",
            "quick_actions": quick_actions,
        })

    if "payroll" in question:
        return jsonify({
            "answer": "Payroll records are managed on the Payroll page, where you can log hours and review totals. Use the dashboard charts for trend insights.",
            "quick_actions": quick_actions,
        })

    if "profile" in question or "update" in question and "profile" in question:
        return jsonify({
            "answer": "Go to your Profile page to update contact information, email, and schedule details.",
            "quick_actions": quick_actions,
        })

    if "attendance" in question or "clock" in question:
        return jsonify({
            "answer": "Use the Attendance section to clock in and clock out. Managers and admins can filter attendance records by employee and date.",
            "quick_actions": quick_actions,
        })

    if "how" in question or "help" in question or "where" in question:
        return jsonify({
            "answer": "I can help you navigate payroll, leave, attendance, and your profile. Ask me to show a page or explain a workflow.",
            "quick_actions": quick_actions,
        })

    return jsonify({
        "answer": "I’m not sure about that yet, but I can help with payroll, leave, attendance, and profile tasks. Try a question like ‘How do I submit leave?’.",
        "quick_actions": quick_actions,
    })
