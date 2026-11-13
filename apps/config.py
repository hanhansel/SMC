import os
import sys
import secrets
import configparser

class Config(object):
    basedir = os.path.abspath(os.path.dirname(__file__))

    # 1. Tentukan Root Folder
    if getattr(sys, 'frozen', False):
        PROJECT_ROOT = os.path.dirname(sys.executable)
    else:
        PROJECT_ROOT = os.path.abspath(os.path.join(basedir, '..'))

    # 2. BACA CONFIG.INI
    config_ini_path = os.path.join(PROJECT_ROOT, "config.ini")
    config_file = configparser.ConfigParser()
    
    db_uri = 'postgresql://postgres@localhost:5432/smc_db' # Fallback jika config.ini tidak ada
    
    if os.path.exists(config_ini_path):
        config_file.read(config_ini_path)
        if 'DATABASE' in config_file:
            db_u = config_file.get('DATABASE', 'db_user', fallback='postgres')
            db_p = config_file.get('DATABASE', 'db_pass', fallback='')
            db_h = config_file.get('DATABASE', 'db_host', fallback='localhost')
            db_n = config_file.get('DATABASE', 'db_name', fallback='smc_db')
            db_uri = f"postgresql://{db_u}:{db_p}@{db_h}/{db_n}"

    # SAKLAR HAK AKSES PER MENU (RBAC) — default MATI:
    # semua akun yang berhasil login otomatis punya akses penuh, dan pengaturan Role / centang
    # hak akses di Account Management disembunyikan. Data izin di database TIDAK dihapus.
    # Untuk menyalakan lagi, tambahkan di config.ini lalu restart SMC:
    #   [ACCESS]
    #   enabled = true
    try:
        ACCESS_CONTROL_ENABLED = config_file.getboolean('ACCESS', 'enabled', fallback=False)
    except ValueError:
        ACCESS_CONTROL_ENABLED = False

    # 3. SET DATABASE URI SECARA DINAMIS
    SQLALCHEMY_DATABASE_URI = db_uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, 'data_storage')
    if not os.path.exists(UPLOAD_FOLDER):
        try:
            os.makedirs(UPLOAD_FOLDER)
            os.makedirs(os.path.join(UPLOAD_FOLDER, 'screenshots'))
        except: pass

    ASSETS_ROOT = os.getenv('ASSETS_ROOT', '/static/assets')
    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)   # acak jika tidak di-set

class ProductionConfig(Config):
    DEBUG = False

class DebugConfig(Config):
    DEBUG = True

config_dict = {
    'Production': ProductionConfig,
    'Debug': DebugConfig
}