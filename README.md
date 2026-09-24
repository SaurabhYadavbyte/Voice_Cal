# Voice Calculator

A Flask voice and keypad calculator with SQLite accounts, email verification, and personal history.

## Run locally

Use Python 3.10 or newer. From `voice_calc_project`:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated value into `SECRET_KEY=` in `.env`. On Windows, copy with `copy .env.example .env`. Set `SESSION_COOKIE_SECURE=0` **only for local HTTP development**. Configure SMTP and `PUBLIC_BASE_URL` if you want to test signup locally over HTTPS. The website continues serving legacy accounts without email until SMTP is configured; new signups require working mail.

```bash
python setup_db.py
python app.py
```

The SQLite database is created under `instance/` unless `SQLITE_DB_PATH` is set. Back up this file and keep it and `.env` private. `setup_db.py` is an additive database migration; run it after every update. It preserves existing accounts and calculation history.

## Deploy on PythonAnywhere

1. Clone the repository to `/home/Voicecalc/voice-calculator`, create a virtual environment matching the Python version in the Web tab, and install `requirements.txt`.
2. In `voice_calc_project/.env`, keep the existing `SECRET_KEY` unchanged. Add:
   ```text
   PUBLIC_BASE_URL=https://voicecalc.pythonanywhere.com
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=your_gmail_address@gmail.com
   SMTP_PASSWORD=your_google_app_password
   ```
   Use a [Google app password](https://support.google.com/accounts/answer/185833) after enabling two-step verification. Keep it in `.env`, not in Git, screenshots, or chat. PythonAnywhere [documents Gmail SMTP for free accounts](https://helpdev.pythonanywhere.com/pages/SMTPForFreeUsers/). SMTP failures are reported on the verification page and in the PythonAnywhere error log.
3. Run `python setup_db.py` in the activated virtual environment. The migration adds email and verification tables while retaining old users and history. Existing users without email must add and verify one at their next login after SMTP is configured.
4. In the Web tab, use Manual configuration with the same virtual environment. WSGI should import:
   ```python
   import sys
   project_dir = "/home/Voicecalc/voice-calculator/voice_calc_project"
   if project_dir not in sys.path:
       sys.path.insert(0, project_dir)
   from app import app as application
   ```
   Map `/static/` to `/home/Voicecalc/voice-calculator/voice_calc_project/static`. Reload the web app.
5. To deploy updates, run `cd ~/voice-calculator && git pull --ff-only`, activate the virtual environment, run `python setup_db.py`, then click **Reload** in the Web tab. Test signup with an email you own, click the verification link, then sign in.

Do not change `SECRET_KEY` on every deploy; that invalidates sessions. Use HTTPS in production; secure cookies are enabled by default. Browser voice input needs Web Speech recognition and microphone permission. Calculation history belongs to the signed-in user and is limited to the most recent 100 entries on display.

### Scientific voice examples

Tap **Tap to Speak** and say phrases such as:

- **sine thirty degrees**
- **square root of eighty one**
- **five factorial**
- **two to the power of eight**
- **log base ten of one hundred**
- **natural log of e**
- **open bracket two plus three close bracket times four**

Say **degrees** or **radians** in a trigonometry command to switch the angle mode.

## Tests

From the voice_calc_project directory, run:

    python -m unittest discover -s tests -v
    node tests/test_voice_parser.js

The Python tests mock SMTP; no real email is sent.
