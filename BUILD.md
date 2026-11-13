# SMC – Tech Stack & Build

SMC (Sistem Monitor CCTV) is a Windows-hosted web application that monitors CCTV
NVRs on a local network: camera status, HDD health, snapshots, weekly reports and
incident tickets.

Tested with **Uniview (UNV)** and **Hikvision** NVRs.

## Tech stack

| Area | Technology | Used for |
|---|---|---|
| Languages | Python 3.13, HTML (Jinja2), JavaScript, CSS | Backend, server-side templates, UI |
| Web backend | Flask 3.1, Werkzeug, Jinja2 | Routes, templates, embedded HTTP server |
| Auth & forms | Flask-Login, Flask-WTF / WTForms | Session login (new session key on every server start), optional per-menu permissions |
| Database | PostgreSQL 16, SQLAlchemy 2.0, Flask-SQLAlchemy, psycopg2 | Devices, status history, tickets |
| Device integration | requests (HTTP Digest auth), JSON / XML (ElementTree), ThreadPoolExecutor | Parallel polling of NVR APIs: channel status, snapshots, HDD status |
| Security | cryptography (Fernet), PBKDF2-SHA512 | Encrypted NVR / SMTP passwords at rest, hashed user passwords |
| Reporting | openpyxl, Pillow | Excel exports with embedded snapshots |
| Notifications | smtplib (STARTTLS) | Ticket summary emails |
| Video / CV | OpenCV (DNN, MobileNet-SSD), MJPEG streaming | Optional live view with person detection (model files not included) |
| Desktop | Tkinter | Server control panel: start / stop, logs, backup & restore |
| Backup | pg_dump / pg_restore | One-click backup of database + encryption key, transactional restore |
| Frontend | Bootstrap 4 (Datta Able), jQuery, Font Awesome, Feather icons, SortableJS, Flatpickr | Dashboard UI, drag-and-drop ordering, time pickers |
| Packaging | PyInstaller 6, Inno Setup 6 | Windows executable and installer |

## Requirements

- Windows 10 / 11 (x64)
- Python 3.13
- PostgreSQL 16, with a database named `smc_db` (`createdb -U postgres smc_db`)
- Network access to the NVRs
- For packaging only: PyInstaller 6.x, Inno Setup 6 (optional)

## Run from source

```
pip install -r requirements.txt
python run.py
```

The first start opens a setup window (admin account + PostgreSQL password) and creates
`config.ini` and the encryption key `system_data/.sys_config.key`.
Click **Nyalakan Server** in the control panel, then open http://localhost:5001.

## Build

```
pyinstaller run.spec        # -> dist/run/run.exe
iscc smc_installer.iss      # optional installer -> Output/
```

## Configuration (`config.ini`)

| Section | Key | Meaning |
|---|---|---|
| `[DATABASE]` | `db_host`, `db_user`, `db_pass`, `db_name` | Written by the setup window (`db_pass` is stored encoded) |
| `[ACCESS]` | `enabled = true` | Turn on per-menu permissions (default: off, every logged-in account has full access) |
| `[BACKUP]` | `folder` | Backup folder (default: `C:\SMC_Backup`) |
| `[BACKUP]` | `pg_bin_dir` | Folder with `pg_dump.exe` / `pg_restore.exe`, if not found automatically |

## Upgrading an installation

1. Control panel → **Backup Data**.
2. Install the new version over the old one.
3. Keep `config.ini`, `system_data/` (without the key, stored NVR passwords cannot be
   decrypted), `data_storage/` (snapshots) and `app_logs/`.
4. Log in, open every menu and run one scan.
