import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-only")
os.environ["SESSION_COOKIE_SECURE"] = "0"

from app import app, db_connection, evaluate, init_db  # noqa: E402


class VoiceCalcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.config.update(TESTING=True, DATABASE=Path(self.temp.name) / "test.sqlite3",
                          SESSION_COOKIE_SECURE=False)
        init_db()
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": "https://voicecalc.example",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "fake-test-password",
        })
        self.env.start()
        self.mail = patch("app.send_verification_email")
        self.sender = self.mail.start()
        self.client = app.test_client()

    def tearDown(self):
        self.mail.stop()
        self.env.stop()
        self.temp.cleanup()

    def token(self):
        self.client.get("/login", follow_redirects=True)
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def post(self, path, **data):
        self.client.get(path if path in ("/register", "/login", "/verify-email") else "/login")
        return self.client.post(path, data={"csrf_token": self.token(), **data})

    def register(self, username="alice", email="alice@example.com"):
        return self.post("/register", username=username, email=email, password="correct horse battery")

    def login(self, username="alice", password="correct horse battery"):
        return self.post("/login", username=username, password=password)

    def verify(self, token=None):
        if token is None:
            token = self.sender.call_args.args[1]
        link = "/verify-email/" + token
        self.assertEqual(self.client.get(link).status_code, 200)
        return self.client.post(link, data={"csrf_token": self.token()})

    def test_signup_requires_verification_and_token_is_single_use(self):
        self.assertEqual(self.register().status_code, 302)
        token = self.sender.call_args.args[1]
        with db_connection() as connection:
            row = connection.execute("SELECT token_hash FROM email_verifications").fetchone()
            self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(self.client.get("/calculator").status_code, 302)
        self.assertEqual(self.login().location, "/verify-email")
        self.assertEqual(self.client.get("/verify-email/" + token).status_code, 200)
        self.assertEqual(self.client.get("/calculator").status_code, 302)
        self.assertEqual(self.verify(token).location, "/login")
        self.assertEqual(self.client.post("/verify-email/" + token, data={"csrf_token": self.token()}).status_code, 400)
        self.assertEqual(self.login().location, "/calculator")
        self.assertEqual(self.client.get("/calculator").status_code, 200)
        response = self.client.post("/calculate", json={"expression": "sin(30)+sqrt(9)+fact(3)"},
                                    headers={"X-CSRF-Token": self.token()})
        self.assertEqual(response.json["result"], "9.5")
        invalid = self.client.post("/calculate", json={"expression": "1+1", "angle_mode": []},
                                   headers={"X-CSRF-Token": self.token()})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"sin(30)", self.client.get("/history").data)
        self.assertEqual(self.post("/clear_history").status_code, 302)
        self.assertNotIn(b"sin(30)", self.client.get("/history").data)

    def test_expiry_resend_and_email_change(self):
        self.register()
        first = self.sender.call_args.args[1]
        with db_connection() as connection:
            connection.execute("UPDATE email_verifications SET expires_at = ?, issued_at = ?",
                               (int(time.time()) - 1, int(time.time()) - 61))
        self.assertEqual(self.client.get("/verify-email/" + first).status_code, 400)
        self.assertEqual(self.post("/verify-email", action="resend").status_code, 302)
        second = self.sender.call_args.args[1]
        self.assertNotEqual(first, second)
        self.assertEqual(self.post("/verify-email", action="resend").status_code, 302)
        self.assertIn(b"Please wait a minute", self.client.get("/verify-email").data)
        self.assertEqual(self.post("/verify-email", action="set_email", email="new@example.com").status_code, 302)
        self.assertEqual(self.client.get("/verify-email/" + second).status_code, 400)
        self.assertEqual(self.verify().status_code, 302)

    def test_mail_outage_and_invalid_addresses(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": ""}):
            response = self.register()
            self.assertEqual(response.status_code, 503)
            self.assertIn(b"Email delivery is not configured", response.data)
        for email in ("x@bad", "bad..name@example.com", "bad space@example.com"):
            self.assertIn(b"Enter a valid email", self.register(email=email).data)
        self.sender.side_effect = OSError("mail unavailable")
        with self.assertLogs(app.logger, level="ERROR"):
            response = self.register()
        self.assertEqual(response.location, "/verify-email")
        self.assertIn(b"email could not be sent", self.client.get("/verify-email").data)
        self.sender.side_effect = None
        with db_connection() as connection:
            connection.execute("UPDATE email_verifications SET issued_at = ?", (int(time.time()) - 61,))
        self.assertEqual(self.post("/verify-email", action="resend").status_code, 302)
        self.assertEqual(self.verify().status_code, 302)

    def test_existing_accounts_migrate_without_data_loss(self):
        self.register()
        self.verify()
        self.login()
        self.client.post("/calculate", json={"expression": "17+4"}, headers={"X-CSRF-Token": self.token()})
        init_db()
        self.assertIn(b"17+4", self.client.get("/history").data)
        with db_connection() as connection:
            connection.execute("INSERT INTO users(username,password_hash) VALUES (?,?)",
                               ("legacy", __import__("werkzeug.security", fromlist=["generate_password_hash"])
                                .generate_password_hash("correct horse battery")))
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.assertEqual(self.login("legacy").location, "/verify-email")
        self.assertEqual(self.post("/verify-email", action="set_email", email="legacy@example.com").status_code, 302)
        self.assertEqual(self.verify().status_code, 302)
        self.assertEqual(self.login("legacy").location, "/calculator")

    def test_csrf_security_headers_and_login_throttle(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["Cache-Control"], "no-store, private")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        old = self.token()
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.post("/login", data={"username": "alice", "password": "irrelevant",
                                                     "csrf_token": old})
        self.assertEqual(response.status_code, 303)
        self.assertIn("session_expired=1", response.location)
        self.register()
        for _ in range(8):
            self.login(password="wrong")
        self.assertEqual(self.login().status_code, 429)
        self.assertEqual(self.client.get("/calculator").status_code, 302)

    def test_scientific_bounds(self):
        self.assertEqual(evaluate("0.1+0.2"), "0.3")
        self.assertEqual(evaluate("sin(30)"), "0.5")
        self.assertEqual(evaluate("2^3+fact(4)"), "32")
        self.assertEqual(evaluate("cos(pi)", "rad"), "-1")
        with self.assertRaises(ValueError):
            evaluate("1+1", ["deg"])
        for expression in ("1/0", "2**1000", "__import__('os')", "fact(99)", "sqrt(-1)",
                           "9" * 161, "2^[1,2]", "sin.__class__"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    evaluate(expression)


    def test_schema_migration_from_old_database(self):
        import sqlite3
        from werkzeug.security import generate_password_hash

        legacy_db = Path(self.temp.name) / "old.sqlite3"
        with sqlite3.connect(legacy_db) as connection:
            connection.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL);
                CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                      user_id INTEGER NOT NULL, expression TEXT NOT NULL,
                                      result TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
            """)
            connection.execute("INSERT INTO users(username, password_hash) VALUES (?, ?)",
                               ("old_user", generate_password_hash("correct horse battery")))
            connection.execute("INSERT INTO history(user_id, expression, result) VALUES (1, '8+9', '17')")
        connection.close()
        app.config["DATABASE"] = legacy_db
        init_db()
        with db_connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM history").fetchone()[0], 1)
            self.assertIsNone(connection.execute("SELECT email FROM users WHERE id=1").fetchone()["email"])
        self.assertEqual(self.login("old_user").location, "/verify-email")

    def test_email_change_cannot_verify_wrong_address(self):
        self.register()
        token = self.sender.call_args.args[1]
        with db_connection() as connection:
            connection.execute("UPDATE users SET email = ? WHERE username = ?",
                               ("changed@example.com", "alice"))
        self.assertEqual(self.client.get("/verify-email/" + token).status_code, 400)
        with db_connection() as connection:
            self.assertIsNone(connection.execute(
                "SELECT email_verified_at FROM users WHERE username = 'alice'"
            ).fetchone()["email_verified_at"])

    def test_legacy_without_smtp_still_works_but_unverified_email_does_not(self):
        from werkzeug.security import generate_password_hash

        with db_connection() as connection:
            connection.execute("INSERT INTO users(username,password_hash) VALUES (?,?)",
                               ("legacy", generate_password_hash("correct horse battery")))
            connection.execute("INSERT INTO users(username,email,password_hash) VALUES (?,?,?)",
                               ("pending", "pending@example.com",
                                generate_password_hash("correct horse battery")))
        with patch.dict(os.environ, {"SMTP_PASSWORD": ""}):
            self.assertEqual(self.login("legacy").location, "/calculator")
            self.client.post("/logout", data={"csrf_token": self.token()})
            self.assertEqual(self.login("pending").location, "/verify-email")
            self.assertEqual(self.client.get("/calculator").status_code, 302)

if __name__ == "__main__":
    unittest.main()
