# apps/backup_manager.py
# =========================================================
# BACKUP & RESTORE DATA SMC (dipanggil dari Panel Kontrol / run.py)
# ---------------------------------------------------------
# Isi 1 file backup (ZIP):
#   - db.dump            : database PostgreSQL lengkap (pg_dump format custom)
#   - .sys_config.key    : kunci enkripsi password NVR (WAJIB satu paket dengan DB,
#                          tanpa kunci ini password NVR di DB tidak bisa dibaca)
#   - manifest.json      : info backup (tanggal, jumlah site/kamera, dll)
# Screenshot TIDAK ikut di-backup (sengaja, agar file kecil).
#
# Lokasi folder backup: config.ini -> [BACKUP] folder = D:\SMC_Backup
# Jika tidak diisi, default: C:\SMC_Backup (di luar folder instalasi,
# supaya tidak ikut terhapus saat uninstall / install ulang).
# =========================================================
import os
import sys
import json
import glob
import shutil
import zipfile
import tempfile
import subprocess
import configparser
from datetime import datetime

BACKUP_PREFIX = "SMC_Backup_"
DUMP_NAME = "db.dump"
KEY_NAME = ".sys_config.key"
MANIFEST_NAME = "manifest.json"
BACKUP_FORMAT_VERSION = 1

# Jangan munculkan jendela CMD hitam saat memanggil pg_dump dari .exe
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BackupError(Exception):
    """Error yang pesannya aman & jelas untuk ditampilkan ke user."""


# ---------------------------------------------------------
# PATH & KONFIGURASI
# ---------------------------------------------------------
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def get_key_file():
    return os.path.join(get_base_path(), 'system_data', KEY_NAME)


def _read_config(config_path):
    cp = configparser.ConfigParser(interpolation=None)
    if os.path.exists(config_path):
        cp.read(config_path)
    return cp


def get_backup_dir(config_path):
    cp = _read_config(config_path)
    folder = cp.get('BACKUP', 'folder', fallback='').strip() if cp.has_section('BACKUP') else ''
    if not folder:
        folder = os.path.join(os.environ.get('SystemDrive', 'C:') + os.sep, 'SMC_Backup')
    return folder


def read_db_params(config_path, decode_key):
    """Ambil kredensial DB dari config.ini (pakai decode_key milik run.py)."""
    cp = _read_config(config_path)
    if 'DATABASE' not in cp:
        raise BackupError("Konfigurasi DATABASE tidak ditemukan di config.ini.")
    d = cp['DATABASE']
    return {
        'host': d.get('db_host', 'localhost'),
        'port': d.get('db_port', '5432'),
        'user': d.get('db_user', 'postgres'),
        'password': decode_key(d.get('db_pass', '')),
        'name': d.get('db_name', 'smc_db'),
    }


def find_pg_tool(tool, config_path=None):
    """Cari pg_dump.exe / pg_restore.exe.
    Urutan: config.ini [BACKUP] pg_bin_dir -> PATH -> C:\\Program Files\\PostgreSQL\\<versi terbaru>\\bin
    """
    exe = tool + ('.exe' if os.name == 'nt' else '')

    if config_path:
        cp = _read_config(config_path)
        custom = cp.get('BACKUP', 'pg_bin_dir', fallback='').strip() if cp.has_section('BACKUP') else ''
        if custom and os.path.isfile(os.path.join(custom, exe)):
            return os.path.join(custom, exe)

    found = shutil.which(tool)
    if found:
        return found

    candidates = []
    for root in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')):
        candidates += glob.glob(os.path.join(root, 'PostgreSQL', '*', 'bin', exe))

    def _ver(path):
        v = os.path.basename(os.path.dirname(os.path.dirname(path)))
        try:
            return tuple(int(x) for x in v.split('.'))
        except ValueError:
            return (0,)

    if candidates:
        return sorted(candidates, key=_ver)[-1]  # versi PostgreSQL tertinggi

    raise BackupError(
        f"{exe} tidak ditemukan.\n\nPastikan PostgreSQL terinstall, atau isi lokasi folder 'bin' PostgreSQL "
        f"di config.ini:\n\n[BACKUP]\npg_bin_dir = C:\\Program Files\\PostgreSQL\\16\\bin"
    )


def _pg_env(db):
    env = os.environ.copy()
    env['PGPASSWORD'] = db['password']
    return env


def _run_pg(cmd, db, timeout=1800):
    try:
        res = subprocess.run(cmd, env=_pg_env(db), capture_output=True, text=True,
                             timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise BackupError(f"Proses {os.path.basename(cmd[0])} terlalu lama (timeout).")
    if res.returncode != 0:
        err = (res.stderr or res.stdout or '').strip()
        raise BackupError(f"{os.path.basename(cmd[0])} gagal:\n{err[-1500:]}")
    return res


# ---------------------------------------------------------
# RINGKASAN DATA (untuk manifest & konfirmasi restore)
# ---------------------------------------------------------
def collect_summary(app):
    """Hitung isi DB saat ini. Gagal hitung tidak menggagalkan backup."""
    summary = {}
    try:
        from sqlalchemy import func
        from apps import db
        from apps.home.models import Site, Camera, WeeklyStatusSnapshot
        with app.app_context():
            summary['site'] = Site.query.count()
            summary['nvr'] = Camera.query.filter(Camera.channel == 0).count()
            summary['kamera'] = Camera.query.filter(Camera.channel > 0).count()
            summary['tanggal_weekly'] = db.session.query(
                func.count(func.distinct(func.date(WeeklyStatusSnapshot.snapshot_date)))).scalar() or 0
            last = db.session.query(func.max(WeeklyStatusSnapshot.snapshot_date)).scalar()
            summary['weekly_terakhir'] = last.strftime('%Y-%m-%d') if last else None
            from apps.home.models import HDDStatusHistory, TicketNotification, EmailSettings
            from apps.authentication.models import Users
            summary['akun'] = Users.query.count()
            summary['ticket'] = TicketNotification.query.count()
            summary['tanggal_hdd'] = db.session.query(func.count(func.distinct(HDDStatusHistory.snapshot_date))).scalar() or 0
            summary['email_setting'] = EmailSettings.query.count() > 0
    except Exception as e:
        summary['catatan'] = f"Ringkasan tidak lengkap: {e}"
    return summary


def describe_summary(manifest):
    s = manifest.get('summary', {}) or {}
    lines = [f"Tanggal backup : {manifest.get('created_at', '-')}"]
    if 'site' in s:
        lines.append(f"Site / NVR / Kamera : {s.get('site')} / {s.get('nvr')} / {s.get('kamera')}")
    if 'tanggal_weekly' in s:
        lines.append(f"Riwayat Weekly : {s.get('tanggal_weekly')} tanggal (terakhir: {s.get('weekly_terakhir') or '-'})")
    if 'akun' in s:
        lines.append(f"Riwayat HDD : {s.get('tanggal_hdd')} tanggal")
        lines.append(f"Ticket : {s.get('ticket')}")
        lines.append(f"Akun login : {s.get('akun')} | Setting email : {'Ada' if s.get('email_setting') else 'Belum'}")
    lines.append(f"Kunci enkripsi : {'Ada' if manifest.get('has_key') else 'TIDAK ADA'}")
    return "\n".join(lines)


# ---------------------------------------------------------
# BACKUP
# ---------------------------------------------------------
def create_backup(db, config_path, summary=None, suffix=""):
    """Buat 1 file ZIP backup. Return path file-nya."""
    backup_dir = get_backup_dir(config_path)
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError as e:
        raise BackupError(f"Folder backup tidak bisa dibuat: {backup_dir}\n{e}")

    pg_dump = find_pg_tool('pg_dump', config_path)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    final_path = os.path.join(backup_dir, f"{BACKUP_PREFIX}{stamp}{suffix}.zip")
    part_path = final_path + ".part"

    with tempfile.TemporaryDirectory(prefix="smc_backup_") as tmp:
        dump_path = os.path.join(tmp, DUMP_NAME)
        _run_pg([pg_dump, '-h', db['host'], '-p', str(db['port']), '-U', db['user'],
                 '-d', db['name'], '-Fc', '--no-owner', '--no-privileges', '-f', dump_path], db)

        key_file = get_key_file()
        manifest = {
            'format_version': BACKUP_FORMAT_VERSION,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'db_name': db['name'],
            'has_key': os.path.isfile(key_file),
            'summary': summary or {},
        }
        try:
            with zipfile.ZipFile(part_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(dump_path, DUMP_NAME)
                if manifest['has_key']:
                    zf.write(key_file, KEY_NAME)
                zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
            os.replace(part_path, final_path)  # file baru "jadi" hanya jika ZIP lengkap
        except Exception:
            if os.path.exists(part_path):
                os.remove(part_path)
            raise
    return final_path


def get_last_backup(config_path):
    """Return (path, datetime) backup terbaru, atau (None, None)."""
    backup_dir = get_backup_dir(config_path)
    files = glob.glob(os.path.join(backup_dir, f"{BACKUP_PREFIX}*.zip"))
    if not files:
        return None, None
    latest = max(files, key=os.path.getmtime)
    return latest, datetime.fromtimestamp(os.path.getmtime(latest))


def last_backup_text(config_path):
    """Teks + warna untuk label 'Backup terakhir' di Panel Kontrol."""
    path, when = get_last_backup(config_path)
    if not when:
        return "Backup terakhir: BELUM PERNAH ⚠️", "#C62828"
    days = (datetime.now().date() - when.date()).days
    ago = "hari ini" if days == 0 else ("kemarin" if days == 1 else f"{days} hari lalu")
    color = "#1565C0" if days <= 7 else ("#5F6B7A" if days <= 30 else "#C62828")
    return f"Backup terakhir: {when.strftime('%d %b %Y %H:%M')} ({ago})", color


# ---------------------------------------------------------
# RESTORE / IMPORT
# ---------------------------------------------------------
def read_backup_manifest(zip_path):
    """Validasi file backup & kembalikan manifest-nya."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if DUMP_NAME not in names or MANIFEST_NAME not in names:
                raise BackupError("File ini bukan file backup SMC yang valid.")
            manifest = json.loads(zf.read(MANIFEST_NAME).decode('utf-8'))
            manifest['has_key'] = KEY_NAME in names
            return manifest
    except zipfile.BadZipFile:
        raise BackupError("File rusak atau bukan file ZIP.")


def restore_backup(zip_path, db, config_path, app=None):
    """Kembalikan DB + kunci enkripsi dari file backup.

    - Server WAJIB dimatikan dulu (dicek di Panel Kontrol).
    - Sebelum restore, kondisi saat ini otomatis di-backup (akhiran '_sebelum-restore')
      supaya kalau salah pilih file masih bisa dikembalikan.
    - pg_restore dijalankan dalam 1 transaksi: jika gagal, DB kembali seperti semula.
    Return path backup pengaman.
    """
    read_backup_manifest(zip_path)  # validasi dulu sebelum menyentuh apa pun
    pg_restore = find_pg_tool('pg_restore', config_path)

    summary = collect_summary(app) if app else None
    safety_path = create_backup(db, config_path, summary=summary, suffix="_sebelum-restore")

    # Lepas semua koneksi milik aplikasi agar tabel tidak terkunci
    if app is not None:
        try:
            from apps import db as sa_db
            with app.app_context():
                sa_db.session.remove()
                sa_db.engine.dispose()
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="smc_restore_") as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract(DUMP_NAME, tmp)
            has_key = KEY_NAME in zf.namelist()
            if has_key:
                zf.extract(KEY_NAME, tmp)

        _run_pg([pg_restore, '-h', db['host'], '-p', str(db['port']), '-U', db['user'],
                 '-d', db['name'], '--clean', '--if-exists', '--no-owner', '--no-privileges',
                 '--single-transaction', '--exit-on-error', os.path.join(tmp, DUMP_NAME)], db)

        if has_key:
            key_file = get_key_file()
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            shutil.copy2(os.path.join(tmp, KEY_NAME), key_file)

    return safety_path
