# ImmobilienHelp Schadenmanagement – produktionsnaher MVP

Diese Version ist deutlich näher an einer echten Live-App:

- Login mit Passwort-Hashing
- SQLite-Datenbank in persistentem Datenordner
- Datei-Uploads in persistentem Upload-Ordner
- Render-Konfiguration (`render.yaml`) inklusive Disk-Mount
- Admin-Einstellungen für Firmenname, Slogan, Support-Kontakt und Passwort
- Health-Check Endpoint `/health`
- Gunicorn als Produktionsserver

## Lokal starten

```bash
pip install -r requirements.txt
python app.py
```

Dann im Browser öffnen:

```bash
http://127.0.0.1:5000
```

## Standard-Login beim ersten Start

Wenn keine ENV-Variablen gesetzt sind:

- Benutzername: `admin`
- Passwort: `admin123`

Danach im Adminbereich sofort das Passwort ändern.

## Wichtige Umgebungsvariablen

- `SECRET_KEY`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `COMPANY_NAME`
- `SUPPORT_PHONE`
- `SUPPORT_EMAIL`
- `DATA_DIR`
- `UPLOAD_DIR`
- `DATABASE_PATH`
- `COOKIE_SECURE`

## Empfohlen für Render

In `render.yaml` ist bereits vorbereitet:

- Python Web Service
- `gunicorn --bind 0.0.0.0:$PORT app:app`
- persistent disk auf `/var/data`
- Health Check auf `/health`

## Ordnerstruktur

- `app.py` – Hauptanwendung
- `templates/` – HTML-Seiten
- `static/` – CSS und Logo
- `data/` – lokale Datenbank lokal
- `uploads/` bzw. produktiv `/var/data/uploads` – hochgeladene Dateien

## Noch nicht enthalten

Für große Live-Kunden solltest du später noch ergänzen:

- echte Rollen/Rechte für mehrere Mitarbeiter
- E-Mail-Benachrichtigungen
- Mandantenfähigkeit für mehrere Hausverwaltungen
- PostgreSQL statt SQLite bei größerer Nutzung
- automatisierte Backups außerhalb des Hosters
