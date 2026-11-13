import os
import shutil
import configparser
import urllib.parse
from apps import create_app, db
from apps.config import config_dict
from cryptography.fernet import Fernet

import base64

def decode_key(encoded_str):
    if not encoded_str or encoded_str == '*** TERHAPUS OLEH SISTEM ***': return ""
    try:
        return base64.b64decode(encoded_str.encode('utf-8')).decode('utf-8')[::-1]
    except:
        return encoded_str

def factory_reset():
    print("========================================")
    print("⚠️ MEMULAI FACTORY RESET DATABASE SMC ⚠️")
    print("========================================")
    
    base_path = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(base_path, "config.ini")
    
    cp = configparser.ConfigParser(interpolation=None)
    
    db_u = 'postgres'
    db_h = 'localhost'
    db_n = 'smc_db'
    db_p = ''

    if not os.path.exists(config_file):
        print("⚠️ File config.ini tidak ditemukan di folder ini.")
        print("   Kita tetap bisa melakukan reset Database jika Anda mengingat Password PostgreSQL Anda.")
        raw_db_pass = input("   Masukkan Password PostgreSQL Anda: ").strip()
        if not raw_db_pass:
            print("❌ Dibatalkan. Password tidak boleh kosong.")
            return
        db_p = urllib.parse.quote_plus(raw_db_pass)
    else:
        cp.read(config_file)
        if 'DATABASE' not in cp:
            print("❌ Konfigurasi DATABASE tidak ditemukan di config.ini.")
            return
            
        try:
            raw_db_pass = decode_key(cp['DATABASE']['db_pass'])
            db_p = urllib.parse.quote_plus(raw_db_pass)         
            db_u = cp['DATABASE']['db_user']
            db_h = cp['DATABASE']['db_host']
            db_n = cp['DATABASE']['db_name']
        except Exception as e:
            print(f"❌ Gagal membaca kredensial Database: {e}")
            return
            
    try:
        uri = f"postgresql://{db_u}:{db_p}@{db_h}:5432/{db_n}"
        config_dict['Production'].SQLALCHEMY_DATABASE_URI = uri
        print("[1/5] Berhasil mengaitkan ke PostgreSQL.")
    except Exception as e:
        print(f"❌ Koneksi URI gagal: {e}")
        return

    # 2. INISIALISASI FLASK APP
    os.environ['DEBUG'] = 'False'
    app = create_app(config_dict['Production'])
    
    with app.app_context():
        try:
            print("[2/4] Menghapus seluruh tabel di database PostgreSQL...")
            # Ini aman dilakukan di PostgreSQL asalkan tidak ada koneksi lain yang mengunci tabel
            db.drop_all()
            
            print("[3/4] Membuat ulang struktur tabel yang bersih...")
            db.create_all()
            print("      ✅ Database berhasil di-reset menjadi kosong.")
            
        except Exception as e:
            print(f"      ❌ Gagal mereset database PostgreSQL: {e}")
            print("      Pastikan tidak ada aplikasi/terminal lain yang sedang mengakses database.")
            return

    # 4. HAPUS CONFIG DAN KUNCI
    print("[4/5] Menghapus file konfigurasi dan kunci keamanan...")
    try:
        os.remove(config_file)
        print("      ✅ config.ini dihapus.")
    except Exception as e: print(f"      - Gagal menghapus config.ini: {e}")
        
    key_path = os.path.join(base_path, 'system_data', '.sys_config.key')
    if os.path.exists(key_path):
        try:
            os.remove(key_path)
            print("      ✅ .sys_config.key dihapus.")
        except Exception as e: print(f"      - Gagal menghapus .sys_config.key: {e}")

    # 5. HAPUS FOLDER PENYIMPANAN LOG & FOTO
    print("[5/5] Membersihkan sisa file foto (Screenshot) dan Log aplikasi...")
    data_storage_path = os.path.join(base_path, 'data_storage')
    app_logs_path = os.path.join(base_path, 'app_logs')
    
    if os.path.exists(data_storage_path):
        try:
            shutil.rmtree(data_storage_path)
            print("      ✅ Folder 'data_storage' beserta foto-foto berhasil dihapus.")
        except Exception as e: print(f"      - Gagal menghapus data_storage: {e}")
        
    if os.path.exists(app_logs_path):
        try:
            shutil.rmtree(app_logs_path)
            print("      ✅ Folder 'app_logs' beserta file log historis berhasil dihapus.")
        except Exception as e: print(f"      - Gagal menghapus app_logs: {e}")

    print("\n==================================================")
    print("🎉 FACTORY RESET SELESAI!")
    print("Silakan jalankan 'python run.py' kembali.")
    print("Aplikasi akan memunculkan jendela Setup Awal (seperti hari pertama instalasi).")
    print("==================================================")

if __name__ == "__main__":
    print("PERINGATAN KRITIS: Skrip ini akan melakukan hal berikut:")
    print(" 1. Menghapus (DROP) semua tabel di PostgreSQL.")
    print(" 2. Menghapus SEMUA DATA (Kamera, Site, Laporan HDD, Log AI).")
    print(" 3. Menghapus SEMUA AKUN termasuk akun Super Admin Anda.")
    print(" 4. Menghapus file config.ini dan kunci enkripsi aplikasi.")
    print(" 5. Menghapus SEMUA FOTO (Screenshots) dan LOG aplikasi.")
    confirm = input("\nApakah Anda YAKIN ingin menghancurkan dan mereset ulang seluruh sistem? (ketik 'y' untuk lanjut): ")
    
    if confirm.strip().lower() == 'y':
        factory_reset()
    else:
        print("Dibatalkan.")
