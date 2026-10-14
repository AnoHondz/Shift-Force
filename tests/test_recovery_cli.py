import secrets

from conftest import TEST_PASSWORD

from app.account_security import encrypt_secret
from app.extensions import db
from app.models import AuditLog, User


def test_trusted_mfa_reset_requires_identity_and_revokes_authentication(app, users):
    with app.app_context():
        user = db.session.get(User, users["admin_id"])
        user.mfa_secret = encrypt_secret(secrets.token_hex(16))
        user.mfa_pending_secret = encrypt_secret(secrets.token_hex(16))
        user.mfa_recovery_hashes = "[]"
        old_token = user.auth_token
        db.session.commit()
    runner = app.test_cli_runner()
    arguments = ["reset-mfa", "admin_user", "--identity-reference", "verified-identity-ticket-123"]
    assert runner.invoke(args=[*arguments, "--confirmed-user", "other_user"]).exit_code != 0
    with app.app_context():
        assert db.session.get(User, users["admin_id"]).auth_token == old_token
    assert runner.invoke(args=[*arguments, "--confirmed-user", "admin_user"]).exit_code == 0
    with app.app_context():
        user = db.session.get(User, users["admin_id"])
        assert user.mfa_secret is None and user.mfa_pending_secret is None
        assert user.mfa_recovery_hashes is None and user.mfa_last_step == -1
        assert user.auth_token != old_token and user.check_password(TEST_PASSWORD)
        assert AuditLog.query.filter_by(action_type="mfa_reset").count() == 1


def test_revoke_all_sessions_is_explicit_and_invalidates_remembered_access(app, users):
    client = app.test_client()
    client.post(
        "/login", data={"username": "employee_user", "password": TEST_PASSWORD, "remember_me": "on"}
    )
    runner = app.test_cli_runner()
    assert (
        runner.invoke(args=["revoke-all-sessions", "--confirmed-scope", "wrong scope"]).exit_code
        != 0
    )
    assert client.get("/schedule").status_code == 200
    assert (
        runner.invoke(args=["revoke-all-sessions", "--confirmed-scope", "all accounts"]).exit_code
        == 0
    )
    assert client.get("/schedule").status_code == 302
