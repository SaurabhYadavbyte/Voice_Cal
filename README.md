# Voice Calculator

A Flask voice and keypad calculator with accounts and personal calculation history. Data is stored in a local SQLite file; no Supabase service is needed.

## Run locally

Use Python 3.10 or newer:

```bash
cd voice_calc_project
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated value after `SECRET_KEY=` in `.env`. On Windows, copy the file with `copy .env.example .env`.

```bash
python setup_db.py
python app.py
```

Open http://127.0.0.1:5000. The SQLite database is created in `voice_calc_project/instance/` unless `SQLITE_DB_PATH` is set to an absolute path. Keep `.env` and the database private and backed up.

## Deploy on PythonAnywhere

1. Clone this repository in a PythonAnywhere Bash console. Open `voice_calc_project` in the cloned directory.
2. Create a virtual environment using the Python version selected for the web app, then run `pip install -r requirements.txt` in that environment.
3. Copy `.env.example` to `.env` and set a fresh, random `SECRET_KEY`. Set `SQLITE_DB_PATH` there if you want the database somewhere else; choose a writable absolute path outside the repository if you plan to replace the checkout.
4. Run `python setup_db.py` once in that environment.
5. In the **Web** tab, create a **Manual configuration** web app with the same Python version. Set its virtual environment to the one above.
6. Edit the web app's WSGI file (replace the username and repository directory with yours):

```python
import sys
project_dir = "/home/yourusername/voice-calculator/voice_calc_project"
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)
from app import app as application
```

7. Set the Web tab static files mapping `/static/` to `/home/yourusername/voice-calculator/voice_calc_project/static`, then reload the web app.

Do not start `app.py` as a background process for the website; PythonAnywhere imports `app` through WSGI. Browser voice input requires a browser with Web Speech recognition and microphone permission. The keypad works without voice support.

Existing Supabase data is not automatically copied into the new SQLite database. If you need old accounts or calculations, export and migrate them before deleting the old Supabase project. Rotate the old Supabase database password because it was previously embedded in source code.

## Tests

From `voice_calc_project`, run `python -m unittest discover -s tests -v`.
