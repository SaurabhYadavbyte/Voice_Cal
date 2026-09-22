"""VoiceCalc Flask application with SQLite accounts and verified email."""
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash, generate_password_hash

from calculator import evaluate
from mailer import mail_configured, send_verification_email

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Set SECRET_KEY in the environment or voice_calc_project/.env")
app.config.update(
    DATABASE=Path(os.environ.get("SQLITE_DB_PATH", BASE_DIR / "instance" / "voice_calc.sqlite3")).resolve(),
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") != "0",
)
login_manager = LoginManager(app)
login_manager.login_view = "login"


def get_db_connection():
    database = app.config["DATABASE"]
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def db_connection():
    connection = get_db_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    """Add email columns and token tables without removing legacy users or history."""
    with db_connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expression TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_history_user_created
                ON history (user_id, created_at DESC, id DESC);
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "email" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "email_verified_at" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        connection.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
                ON users(email COLLATE NOCASE) WHERE email IS NOT NULL;
            CREATE TABLE IF NOT EXISTS email_verifications (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                issued_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS email_send_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sent_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_email_send_user_time
                ON email_send_events(user_id, sent_at);
            CREATE TABLE IF NOT EXISTS auth_limits (
                key TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL,
                window_start INTEGER NOT NULL
            );
        """)
        verification_columns = {row["name"] for row in connection.execute("PRAGMA table_info(email_verifications)")}
        if "email" not in verification_columns:
            connection.execute("ALTER TABLE email_verifications ADD COLUMN email TEXT")


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = str(user_id)
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    if not user_id.isdecimal():
        return None
    try:
        with db_connection() as connection:
            row = connection.execute(
                "SELECT id, username, email, email_verified_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        # An existing account without email stays usable until SMTP is configured.
        if not row or (mail_configured() and not row["email_verified_at"]) or (
            row["email"] and not row["email_verified_at"]
        ):
            return None
        return User(row["id"], row["username"])
    except sqlite3.Error:
        app.logger.exception("Could not load user")
        return None


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


class CSRFError(BadRequest):
    description = "The form expired. Reload the page and try again."


@app.before_request
def check_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not submitted or not secrets.compare_digest(submitted, expected):
            raise CSRFError()


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.endpoint == "calculate" or request.is_json:
        return jsonify(success=False, error="Session expired. Refresh the page and try again."), 400
    session.pop("csrf_token", None)
    target = request.endpoint if request.endpoint in {"login", "register", "verification_pending"} else (
        "history" if request.endpoint == "clear_history" else "calculator"
    )
    return redirect(url_for(target, session_expired="1"), code=303)


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    if response.mimetype == "text/html" or request.endpoint == "calculate":
        response.headers["Cache-Control"] = "no-store, private"
    return response


def normalize_email(value):
    email = value.strip()
    if not 3 <= len(email) <= 254 or email.count("@") != 1 or not email.isascii():
        raise ValueError("Enter a valid email address.")
    local, domain = email.rsplit("@", 1)
    if not 1 <= len(local) <= 64 or not re.fullmatch(r"[A-Za-z0-9._%+\-]+", local):
        raise ValueError("Enter a valid email address.")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("Enter a valid email address.")
    labels = domain.lower().split(".")
    if len(labels) < 2 or not 2 <= len(labels[-1]) <= 63 or any(
        not 1 <= len(label) <= 63 or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Enter a valid email address.")
    return local + "@" + domain.lower()


def masked_email(email):
    local, domain = email.rsplit("@", 1)
    return local[:1] + "***@" + domain


def pending_user():
    user_id = session.get("pending_user_id")
    if not isinstance(user_id, int) or session.get("pending_until", 0) < time.time():
        session.pop("pending_user_id", None)
        session.pop("pending_until", None)
        return None
    with db_connection() as connection:
        return connection.execute(
            "SELECT id, username, email, email_verified_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def set_pending(user_id):
    session.clear()
    session["pending_user_id"] = user_id
    session["pending_until"] = int(time.time()) + 15 * 60


def limit_key(purpose, identifier):
    source = f"{purpose}:{request.remote_addr or 'unknown'}:{identifier}".encode()
    return hashlib.sha256(source).hexdigest()


def limit_reached(key, maximum, window):
    now = int(time.time())
    with db_connection() as connection:
        row = connection.execute("SELECT attempts, window_start FROM auth_limits WHERE key = ?", (key,)).fetchone()
    return bool(row and now - row["window_start"] < window and row["attempts"] >= maximum)


def record_attempt(key, window):
    now = int(time.time())
    with db_connection() as connection:
        connection.execute("DELETE FROM auth_limits WHERE window_start < ?", (now - 86400,))
        connection.execute("""
            INSERT INTO auth_limits(key, attempts, window_start) VALUES (?, 1, ?)
            ON CONFLICT(key) DO UPDATE SET
                attempts = CASE WHEN ? - window_start >= ? THEN 1 ELSE attempts + 1 END,
                window_start = CASE WHEN ? - window_start >= ? THEN ? ELSE window_start END
        """, (key, now, now, window, now, window, now))


def send_link(user_id, email):
    """Store only a hash. Keep one outstanding token; throttle per account."""
    if not mail_configured():
        return "unavailable"
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db_connection() as connection:
        previous = connection.execute(
            "SELECT issued_at FROM email_verifications WHERE user_id = ?", (user_id,)
        ).fetchone()
        if previous and now - previous["issued_at"] < 60:
            return "wait"
        connection.execute("DELETE FROM email_send_events WHERE sent_at < ?", (now - 86400,))
        daily = connection.execute(
            "SELECT count(*) FROM email_send_events WHERE user_id = ? AND sent_at > ?", (user_id, now - 86400)
        ).fetchone()[0]
        if daily >= 5:
            return "limit"
        connection.execute(
            "INSERT INTO email_verifications(user_id, token_hash, email, expires_at, issued_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "token_hash=excluded.token_hash, email=excluded.email, expires_at=excluded.expires_at, issued_at=excluded.issued_at",
            (user_id, digest, email, now + 1800, now),
        )
        event_id = connection.execute(
            "INSERT INTO email_send_events(user_id, sent_at) VALUES (?, ?)", (user_id, now)
        ).lastrowid
    try:
        send_verification_email(email, token)
    except Exception:
        app.logger.exception("Verification email delivery failed")
        with db_connection() as connection:
            connection.execute("DELETE FROM email_verifications WHERE token_hash = ?", (digest,))
            connection.execute("DELETE FROM email_send_events WHERE id = ?", (event_id,))
        return "failed"
    return "sent"


@app.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("calculator"))
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("calculator"))
    if request.method == "POST":
        signup_key = limit_key("register", "")
        try:
            if limit_reached(signup_key, 20, 3600):
                flash("Too many signup attempts. Please try again in an hour.", "danger")
                return render_template("register.html"), 429
            record_attempt(signup_key, 3600)
        except sqlite3.Error:
            app.logger.exception("Signup rate limit unavailable")
            flash("Could not create account. Please try again.", "danger")
            return render_template("register.html"), 503
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,50}", username):
                raise ValueError("Username must be 3Ã¢â‚¬â€œ50 letters, numbers, underscores, or hyphens.")
            email = normalize_email(email)
            if not 12 <= len(password) <= 128:
                raise ValueError("Password must be 12Ã¢â‚¬â€œ128 characters.")
            if not mail_configured():
                flash("Email delivery is not configured yet. Please try again later.", "danger")
                return render_template("register.html"), 503
            with db_connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                    (username, email, generate_password_hash(password)),
                )
                user_id = cursor.lastrowid
            set_pending(user_id)
            sent = send_link(user_id, email)
            if sent != "sent":
                flash("Account saved, but email could not be sent. Try resend in a moment.", "danger")
            else:
                flash("Check your email for a verification link.", "success")
            return redirect(url_for("verification_pending"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except sqlite3.IntegrityError:
            flash("Username or email is already in use.", "danger")
        except sqlite3.Error:
            app.logger.exception("Registration failed")
            flash("Could not create account. Please try again.", "danger")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("calculator"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Enter both username and password.", "danger")
        elif len(username) > 50 or len(password) > 128:
            flash("Invalid username or password.", "danger")
        else:
            key = limit_key("login", username.casefold())
            ip_key = limit_key("login-ip", "")
            try:
                if limit_reached(key, 8, 900) or limit_reached(ip_key, 40, 900):
                    flash("Too many login attempts. Please try again in 15 minutes.", "danger")
                    return render_template("login.html"), 429
                with db_connection() as connection:
                    row = connection.execute(
                        "SELECT id, username, password_hash, email, email_verified_at FROM users "
                        "WHERE username = ?", (username,)
                    ).fetchone()
                if row and check_password_hash(row["password_hash"], password):
                    with db_connection() as connection:
                        connection.execute("DELETE FROM auth_limits WHERE key = ?", (key,))
                    if not row["email_verified_at"] and (row["email"] or mail_configured()):
                        set_pending(row["id"])
                        if row["email"]:
                            flash("Verify your email before signing in.", "danger")
                        else:
                            flash("Add and verify an email to finish updating your account.", "danger")
                        return redirect(url_for("verification_pending"))
                    session.clear()
                    login_user(User(row["id"], row["username"]))
                    return redirect(url_for("calculator"))
                record_attempt(key, 900)
                record_attempt(ip_key, 900)
                flash("Invalid username or password.", "danger")
            except sqlite3.Error:
                app.logger.exception("Login failed")
                flash("Could not sign in. Please try again.", "danger")
    return render_template("login.html")


@app.route("/verify-email", methods=["GET", "POST"])
def verification_pending():
    try:
        user = pending_user()
    except sqlite3.Error:
        app.logger.exception("Verification lookup failed")
        flash("Could not check your account. Please sign in again.", "danger")
        return redirect(url_for("login"))
    if not user:
        return redirect(url_for("login"))
    if user["email_verified_at"]:
        return redirect(url_for("login"))
    if request.method == "POST":
        if not mail_configured():
            flash("Email delivery is unavailable. Please try again later.", "danger")
        elif request.form.get("action") == "set_email":
            try:
                email = normalize_email(request.form.get("email", ""))
                with db_connection() as connection:
                    connection.execute(
                        "UPDATE users SET email = ?, email_verified_at = NULL WHERE id = ?", (email, user["id"])
                    )
                    connection.execute("DELETE FROM email_verifications WHERE user_id = ?", (user["id"],))
                outcome = send_link(user["id"], email)
                flash("Check your email for a verification link." if outcome == "sent" else
                      "Email could not be sent. Try resend shortly.", "success" if outcome == "sent" else "danger")
                return redirect(url_for("verification_pending"))
            except ValueError as exc:
                flash(str(exc), "danger")
            except sqlite3.IntegrityError:
                flash("This email is already in use.", "danger")
            except sqlite3.Error:
                app.logger.exception("Could not update email")
                flash("Could not update email. Please try again.", "danger")
        elif request.form.get("action") == "resend" and user["email"]:
            try:
                outcome = send_link(user["id"], user["email"])
            except sqlite3.Error:
                app.logger.exception("Could not prepare verification email")
                flash("Could not send email right now. Please try again.", "danger")
                return redirect(url_for("verification_pending"))
            messages = {
                "sent": "A new verification link was sent.",
                "wait": "Please wait a minute before requesting another link.",
                "limit": "Daily email limit reached. Try again tomorrow.",
                "failed": "Email could not be sent. Please try later.",
                "unavailable": "Email delivery is unavailable.",
            }
            flash(messages[outcome], "success" if outcome == "sent" else "danger")
            return redirect(url_for("verification_pending"))
        else:
            flash("Choose a valid action.", "danger")
    return render_template("verify_pending.html", email=masked_email(user["email"]) if user["email"] else None)


@app.route("/verify-email/<token>", methods=["GET", "POST"])
def verify_email(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", token):
        return render_template("verify_link.html", valid=False), 400
    digest = hashlib.sha256(token.encode()).hexdigest()
    try:
        with db_connection() as connection:
            row = connection.execute(
                "SELECT v.user_id, v.expires_at, v.email AS token_email, u.email, u.email_verified_at "
                "FROM email_verifications v JOIN users u ON u.id = v.user_id WHERE v.token_hash = ?",
                (digest,),
            ).fetchone()
        if not row or row["expires_at"] < int(time.time()) or row["email_verified_at"] or row["token_email"] != row["email"]:
            return render_template("verify_link.html", valid=False), 400
        if request.method == "POST":
            with db_connection() as connection:
                # DELETE is conditional: only one concurrent request can consume this link.
                deleted = connection.execute(
                    "DELETE FROM email_verifications WHERE token_hash = ? AND expires_at >= ?",
                    (digest, int(time.time())),
                ).rowcount
                if not deleted:
                    return render_template("verify_link.html", valid=False), 400
                connection.execute(
                    "UPDATE users SET email_verified_at = datetime('now') WHERE id = ? AND email = ?",
                    (row["user_id"], row["email"]),
                )
            session.clear()
            flash("Email verified. Sign in to continue.", "success")
            return redirect(url_for("login"))
        return render_template("verify_link.html", valid=True, email=masked_email(row["email"]))
    except sqlite3.Error:
        app.logger.exception("Email verification failed")
        flash("Could not verify email right now. Please try again.", "danger")
        return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("landing"))


@app.route("/calculator")
@login_required
def calculator():
    return render_template("index.html")


@app.route("/history")
@login_required
def history():
    try:
        with db_connection() as connection:
            rows = connection.execute(
                "SELECT expression, result, created_at FROM history WHERE user_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 100", (current_user.id,)
            ).fetchall()
        records = [dict(row) for row in rows]
        for record in records:
            record["created_at"] = datetime.fromisoformat(record["created_at"])
    except sqlite3.Error:
        app.logger.exception("History query failed")
        flash("Could not load history. Please try again.", "danger")
        records = []
    return render_template("history.html", records=records)


@app.route("/calculate", methods=["POST"])
@login_required
def calculate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Send a JSON expression."), 400
    expression = data.get("expression", "")
    try:
        result = evaluate(expression, data.get("angle_mode", "deg"))
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    try:
        with db_connection() as connection:
            connection.execute(
                "INSERT INTO history (user_id, expression, result) VALUES (?, ?, ?)",
                (current_user.id, expression.strip(), result),
            )
    except sqlite3.Error:
        app.logger.exception("Could not save calculation")
        return jsonify(success=False, error="Could not save calculation. Please try again."), 500
    return jsonify(success=True, result=result, expression=expression.strip())


@app.route("/clear_history", methods=["POST"])
@login_required
def clear_history():
    try:
        with db_connection() as connection:
            connection.execute("DELETE FROM history WHERE user_id = ?", (current_user.id,))
        flash("History cleared.", "success")
    except sqlite3.Error:
        app.logger.exception("Could not clear history")
        flash("Could not clear history. Please try again.", "danger")
    return redirect(url_for("history"))


@app.route("/help")
@login_required
def help_page():
    return render_template("help.html")


@app.route("/privacy")
@login_required
def privacy():
    return render_template("privacy.html")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
