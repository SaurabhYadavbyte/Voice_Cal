"""Create the local SQLite schema. Safe to run again after deployment."""

from app import app, init_db


if __name__ == "__main__":
    init_db()
    print(f"Database ready: {app.config['DATABASE']}")
