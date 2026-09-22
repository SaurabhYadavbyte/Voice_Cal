"""Voice calculator Flask application."""

import ast
import logging
import operator
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Set SECRET_KEY in the environment or in voice_calc_project/.env")
app.config.update(
    DATABASE=Path(os.environ.get("SQLITE_DB_PATH", BASE_DIR / "instance" / "voice_calc.sqlite3")).resolve(),
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
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
            row = connection.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(row["id"], row["username"]) if row else None
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

    # The old POST must never be retried automatically. Show a new form and token.
    session.pop("csrf_token", None)
    target = request.endpoint if request.endpoint in {"login", "register"} else (
        "history" if request.endpoint == "clear_history" else "calculator"
    )
    return redirect(url_for(target, session_expired="1"), code=303)


@app.after_request
def prevent_stale_forms(response):
    if request.endpoint in {"login", "register", "calculator", "history"} and response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, private"
    return response


def evaluate(expression):
    """Evaluate short arithmetic expressions without executing Python code."""
    if not isinstance(expression, str) or not 0 < len(expression) <= 120:
        raise ValueError("Enter an expression of up to 120 characters.")
    if not all(char in "0123456789.+-*/() \t" for char in expression):
        raise ValueError("Use numbers and arithmetic operators only.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid expression.") from exc
    if sum(1 for _ in ast.walk(tree)) > 60:
        raise ValueError("Expression is too complex.")

    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = visit(node.left), visit(node.right)
            if abs(left) > Decimal("1e12") or abs(right) > Decimal("1e12"):
                raise ValueError("Number is too large.")
            value = binary[type(node.op)](left, right)
            if not value.is_finite() or abs(value) > Decimal("1e12"):
                raise ValueError("Result is too large.")
            return value
        raise ValueError("Invalid expression.")

    try:
        with localcontext() as context:
            context.prec = 28
            result = visit(tree)
    except (DecimalException, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("Invalid calculation.") from exc
    if not result.is_finite() or abs(result) > Decimal("1e12"):
        raise ValueError("Result is too large.")
    return format(result.normalize(), "f")


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
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not 3 <= len(username) <= 50 or not all(c.isalnum() or c in "_-" for c in username):
            flash("Username must be 3–50 letters, numbers, underscores, or hyphens.", "danger")
        elif not 8 <= len(password) <= 128:
            flash("Password must be 8–128 characters.", "danger")
        else:
            try:
                with db_connection() as connection:
                    connection.execute(
                        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (username, generate_password_hash(password)),
                    )
                flash("Account created. Please sign in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Username already exists.", "danger")
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
            try:
                with db_connection() as connection:
                    row = connection.execute(
                        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
                    ).fetchone()
                if row and check_password_hash(row["password_hash"], password):
                    session.clear()
                    login_user(User(row["id"], row["username"]))
                    return redirect(url_for("calculator"))
                flash("Invalid username or password.", "danger")
            except sqlite3.Error:
                app.logger.exception("Login failed")
                flash("Could not sign in. Please try again.", "danger")
    return render_template("login.html")


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
        result = evaluate(expression)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
