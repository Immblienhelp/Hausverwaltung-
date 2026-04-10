import csv
import io
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", DATA_DIR / "uploads"))
DB_PATH = Path(os.environ.get("DATABASE_PATH", DATA_DIR / "immobilienhelp.db"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["UPLOAD_DIR"] = str(UPLOAD_DIR)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"


# ------------------------
# Helpers
# ------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_reference_code(ticket_id: int) -> str:
    return f"IH-{datetime.now().strftime('%Y%m')}-{ticket_id:05d}"


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Bitte zuerst einloggen.", "error")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def bootstrap_default_user() -> None:
    default_username = os.environ.get("ADMIN_USERNAME", "admin")
    default_password = os.environ.get("ADMIN_PASSWORD")
    default_company = os.environ.get("COMPANY_NAME", "ImmobilienHelp")
    default_support_phone = os.environ.get("SUPPORT_PHONE", "+43 000 000000")
    default_support_email = os.environ.get("SUPPORT_EMAIL", "info@example.com")

    conn = get_db()
    existing = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing:
        if not default_password:
            default_password = "admin123"
        conn.execute(
            "INSERT INTO users (username, password_hash, company_name, support_phone, support_email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                default_username,
                generate_password_hash(default_password),
                default_company,
                default_support_phone,
                default_support_email,
                now_str(),
            ),
        )
    conn.commit()
    conn.close()

    if not get_setting("company_name"):
        set_setting("company_name", default_company)
    if not get_setting("support_phone"):
        set_setting("support_phone", default_support_phone)
    if not get_setting("support_email"):
        set_setting("support_email", default_support_email)
    if not get_setting("brand_tagline"):
        set_setting("brand_tagline", "Work can be so easy")


# ------------------------
# Database init
# ------------------------

def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            company_name TEXT,
            support_phone TEXT,
            support_email TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reference_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            address TEXT NOT NULL,
            unit_number TEXT,
            issue_type TEXT NOT NULL,
            description TEXT NOT NULL,
            urgency TEXT NOT NULL,
            emergency_flag INTEGER NOT NULL DEFAULT 0,
            available_time TEXT,
            status TEXT NOT NULL DEFAULT 'Offen',
            internal_note TEXT,
            attachment_path TEXT,
            attachment_original_name TEXT
        )
        """
    )

    conn.commit()

    # lightweight migration support for earlier SQLite versions of the app
    existing_columns = {
        row[1] for row in cur.execute("PRAGMA table_info(tickets)").fetchall()
    }
    if "updated_at" not in existing_columns:
        cur.execute("ALTER TABLE tickets ADD COLUMN updated_at TEXT")
        cur.execute("UPDATE tickets SET updated_at = created_at WHERE updated_at IS NULL")
    if "attachment_original_name" not in existing_columns:
        cur.execute("ALTER TABLE tickets ADD COLUMN attachment_original_name TEXT")
    conn.commit()
    conn.close()

    bootstrap_default_user()


# ------------------------
# Context
# ------------------------
@app.context_processor
def inject_branding():
    return {
        "company_name": get_setting("company_name", "ImmobilienHelp"),
        "support_phone": get_setting("support_phone", "+43 000 000000"),
        "support_email": get_setting("support_email", "info@example.com"),
        "brand_tagline": get_setting("brand_tagline", "Work can be so easy"),
    }


# ------------------------
# Public routes
# ------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": now_str()}


@app.get("/")
def landing():
    return render_template("landing.html", title="ImmobilienHelp")


@app.route("/meldung", methods=["GET", "POST"])
def report_issue():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        unit_number = request.form.get("unit_number", "").strip()
        issue_type = request.form.get("issue_type", "").strip()
        description = request.form.get("description", "").strip()
        urgency = request.form.get("urgency", "").strip()
        emergency_flag = 1 if request.form.get("emergency_flag") == "on" else 0
        available_time = request.form.get("available_time", "").strip()

        if not all([name, phone, address, issue_type, description, urgency]):
            flash("Bitte alle Pflichtfelder ausfüllen.", "error")
            return redirect(url_for("report_issue"))

        attachment_path = None
        attachment_original_name = None
        file = request.files.get("attachment")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Dateiformat nicht unterstützt. Erlaubt: PNG, JPG, WEBP, PDF.", "error")
                return redirect(url_for("report_issue"))

            attachment_original_name = file.filename
            safe_name = secure_filename(file.filename)
            final_name = f"{uuid.uuid4().hex}_{safe_name}"
            save_path = UPLOAD_DIR / final_name
            file.save(save_path)
            attachment_path = final_name

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tickets (
                created_at, updated_at, reference_code, name, phone, email, address,
                unit_number, issue_type, description, urgency, emergency_flag,
                available_time, status, internal_note, attachment_path, attachment_original_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_str(),
                now_str(),
                "TEMP",
                name,
                phone,
                email,
                address,
                unit_number,
                issue_type,
                description,
                urgency,
                emergency_flag,
                available_time,
                "Offen",
                "",
                attachment_path,
                attachment_original_name,
            ),
        )
        ticket_id = cur.lastrowid
        ref_code = generate_reference_code(ticket_id)
        cur.execute(
            "UPDATE tickets SET reference_code = ?, updated_at = ? WHERE id = ?",
            (ref_code, now_str(), ticket_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("thank_you", ref=ref_code))

    return render_template("report.html", title="Schaden melden")


@app.get("/danke")
def thank_you():
    ref = request.args.get("ref", "")
    return render_template("thank_you.html", ref=ref, title="Vielen Dank")


# ------------------------
# Auth
# ------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Erfolgreich angemeldet.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))

        flash("Ungültige Zugangsdaten.", "error")

    return render_template("login.html", title="Login")


@app.get("/logout")
def logout():
    session.clear()
    flash("Erfolgreich ausgeloggt.", "success")
    return redirect(url_for("landing"))


# ------------------------
# Admin
# ------------------------
@app.get("/admin")
@login_required
def dashboard():
    status_filter = request.args.get("status", "").strip()
    urgency_filter = request.args.get("urgency", "").strip()
    search = request.args.get("search", "").strip()

    query = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if urgency_filter:
        query += " AND urgency = ?"
        params.append(urgency_filter)
    if search:
        like = f"%{search}%"
        query += " AND (reference_code LIKE ? OR name LIKE ? OR address LIKE ? OR description LIKE ?)"
        params.extend([like, like, like, like])
    query += ' ORDER BY CASE status WHEN "Offen" THEN 1 WHEN "In Bearbeitung" THEN 2 ELSE 3 END, created_at DESC'

    conn = get_db()
    cur = conn.cursor()
    tickets = cur.execute(query, params).fetchall()
    stats = {
        "total": cur.execute("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"],
        "open": cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'Offen'").fetchone()["c"],
        "progress": cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'In Bearbeitung'").fetchone()["c"],
        "done": cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'Erledigt'").fetchone()["c"],
        "urgent": cur.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE urgency = 'Dringend' OR emergency_flag = 1"
        ).fetchone()["c"],
    }
    conn.close()

    return render_template(
        "dashboard.html",
        tickets=tickets,
        stats=stats,
        status_filter=status_filter,
        urgency_filter=urgency_filter,
        search=search,
        title="Verwaltungsansicht",
    )


@app.route("/admin/ticket/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def ticket_detail(ticket_id: int):
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        status = request.form.get("status", "").strip()
        internal_note = request.form.get("internal_note", "").strip()
        if status not in {"Offen", "In Bearbeitung", "Erledigt"}:
            flash("Ungültiger Status.", "error")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))

        cur.execute(
            "UPDATE tickets SET status = ?, internal_note = ?, updated_at = ? WHERE id = ?",
            (status, internal_note, now_str(), ticket_id),
        )
        conn.commit()
        flash("Ticket aktualisiert.", "success")

    ticket = cur.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()

    if not ticket:
        abort(404)

    return render_template("ticket_detail.html", ticket=ticket, title=ticket["reference_code"])


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(path)


@app.get("/admin/export")
@login_required
def export_csv():
    conn = get_db()
    tickets = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
    conn.close()

    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(
        [
            "Referenz",
            "Erstellt",
            "Aktualisiert",
            "Name",
            "Telefon",
            "E-Mail",
            "Adresse",
            "Top",
            "Kategorie",
            "Beschreibung",
            "Priorität",
            "Notfall",
            "Erreichbarkeit",
            "Status",
            "Interne Notiz",
        ]
    )
    for t in tickets:
        writer.writerow(
            [
                t["reference_code"],
                t["created_at"],
                t["updated_at"],
                t["name"],
                t["phone"],
                t["email"],
                t["address"],
                t["unit_number"],
                t["issue_type"],
                t["description"],
                t["urgency"],
                "Ja" if t["emergency_flag"] else "Nein",
                t["available_time"],
                t["status"],
                t["internal_note"],
            ]
        )

    output = io.BytesIO(sio.getvalue().encode("utf-8-sig"))
    output.seek(0)
    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="immobilienhelp_tickets.csv",
    )


@app.route("/admin/einstellungen", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip() or "ImmobilienHelp"
        support_phone = request.form.get("support_phone", "").strip()
        support_email = request.form.get("support_email", "").strip()
        brand_tagline = request.form.get("brand_tagline", "").strip() or "Work can be so easy"
        new_password = request.form.get("new_password", "")

        set_setting("company_name", company_name)
        set_setting("support_phone", support_phone)
        set_setting("support_email", support_email)
        set_setting("brand_tagline", brand_tagline)

        if new_password:
            conn = get_db()
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), session["user_id"]),
            )
            conn.commit()
            conn.close()

        flash("Einstellungen gespeichert.", "success")
        return redirect(url_for("settings_page"))

    current = {
        "company_name": get_setting("company_name", "ImmobilienHelp"),
        "support_phone": get_setting("support_phone", ""),
        "support_email": get_setting("support_email", ""),
        "brand_tagline": get_setting("brand_tagline", "Work can be so easy"),
    }
    return render_template("settings.html", current=current, title="Einstellungen")


@app.cli.command("create-admin")
def create_admin_command():
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin123")
    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        print(f"Admin '{username}' existiert bereits.")
    else:
        conn.execute(
            "INSERT INTO users (username, password_hash, company_name, support_phone, support_email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                generate_password_hash(password),
                get_setting("company_name", "ImmobilienHelp"),
                get_setting("support_phone", ""),
                get_setting("support_email", ""),
                now_str(),
            ),
        )
        conn.commit()
        print(f"Admin '{username}' wurde erstellt.")
    conn.close()


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
