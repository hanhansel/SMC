import os
import threading
import logging
import sys
import configparser
import webbrowser
import urllib.parse
import base64
import secrets
from sys import exit
import tkinter as tk
from tkinter import messagebox
from sqlalchemy import text
import ctypes
from datetime import datetime

# =================================================================
# ENCODER & DECODER NILAI DI CONFIG.INI
# =================================================================
def encode_key(text_str):
    if not text_str: return ""
    return base64.b64encode(text_str[::-1].encode('utf-8')).decode('utf-8')

def decode_key(encoded_str):
    if not encoded_str or encoded_str == '*** TERHAPUS OLEH SISTEM ***': return ""
    try:
        return base64.b64decode(encoded_str.encode('utf-8')).decode('utf-8')[::-1]
    except:
        return encoded_str

# =================================================================
# INISIALISASI DASAR & LOGGING
# =================================================================
def get_base_path():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))

BASE_PATH = get_base_path()
INITIAL_CONFIG_FILE = os.path.join(BASE_PATH, "config.ini")
ICON_FILE = os.path.join(BASE_PATH, "smc.ico")

try:
    myappid = 'smc.controller.1.0'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception: pass

current_file_handler = None
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

formatter = logging.Formatter("[%(levelname)s] %(message)s")
if sys.stdout is not None:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    if not root_logger.handlers:
        root_logger.addHandler(stream_handler)

# =================================================================
# TEMA WARNA CONTROLLER (biru - putih)
# =================================================================
UI_BG = "#FFFFFF"          # latar
UI_BLUE = "#1565C0"        # tombol utama / judul
UI_BLUE_DARK = "#0D47A1"   # tombol utama saat ditekan
UI_BLUE_LIGHT = "#E3F2FD"  # tombol sekunder
UI_TEXT = "#0D2A4A"        # teks
UI_MUTED = "#5F6B7A"       # teks redup
UI_FONT = "Segoe UI"

def apply_theme(win):
    win.configure(bg=UI_BG)
    win.option_add("*Label.Background", UI_BG)
    win.option_add("*Label.Foreground", UI_TEXT)
    win.option_add("*Frame.Background", UI_BG)
    win.option_add("*Font", (UI_FONT, 9))

def themed_button(parent, text, command, primary=True, width=16):
    """Tombol datar: utama = biru solid, sekunder = biru muda dengan teks biru."""
    if primary:
        colors = dict(bg=UI_BLUE, fg="white", activebackground=UI_BLUE_DARK, activeforeground="white", disabledforeground="#8FB3E8")
    else:
        colors = dict(bg=UI_BLUE_LIGHT, fg=UI_BLUE, activebackground="#BBDEFB", activeforeground=UI_BLUE_DARK, disabledforeground="#9DB7D5")
    return tk.Button(parent, text=text, command=command, width=width, relief="flat", bd=0, cursor="hand2",
                     font=(UI_FONT, 9, "bold"), padx=6, pady=5, **colors)

# =================================================================
# SETUP AWAL (JENDELA PERTAMA KALI)
# =================================================================
def setup_first_admin():
    if os.path.exists(INITIAL_CONFIG_FILE): return

    win = tk.Tk()
    win.title("Setup Awal Sistem SMC (Secure)")
    win.geometry("400x400")
    win.resizable(False, False)
    apply_theme(win)
    try:
        if os.path.exists(ICON_FILE): win.iconbitmap(default=ICON_FILE)
    except: pass 
    
    tk.Label(win, text="1. Akun Super Admin Web:", font=(UI_FONT, 10, "bold"), fg=UI_BLUE).pack(pady=(10, 2))
    tk.Label(win, text="Username:").pack(); ent_u = tk.Entry(win, width=30); ent_u.pack(pady=2)
    tk.Label(win, text="Email:").pack(); ent_e = tk.Entry(win, width=30); ent_e.pack(pady=2)
    tk.Label(win, text="Password Web:").pack(); ent_p = tk.Entry(win, width=30, show="*"); ent_p.pack(pady=2)

    tk.Label(win, text="2. Koneksi PostgreSQL:", font=(UI_FONT, 10, "bold"), fg=UI_BLUE).pack(pady=(15, 2))
    tk.Label(win, text="Password PostgreSQL:").pack(); ent_db_p = tk.Entry(win, width=30, show="*"); ent_db_p.pack(pady=2)

    def save_admin():
        u = str(ent_u.get().strip()); e = str(ent_e.get().strip())
        p = str(ent_p.get().strip()); db_p = str(ent_db_p.get().strip())
        
        if not u or not p or not e or not db_p:
            messagebox.showerror("Error", "Semua kolom wajib diisi!")
            return
            
        config = configparser.ConfigParser()
        config['DATABASE'] = {'db_host': 'localhost', 'db_user': 'postgres', 'db_pass': encode_key(db_p), 'db_name': 'smc_db'}
        config['CREDENTIALS'] = {'USERNAME': u, 'EMAIL': e, 'PASSWORD': encode_key(p)}
        
        with open(INITIAL_CONFIG_FILE, 'w') as configfile: config.write(configfile)
        messagebox.showinfo("Sukses", "Data diamankan dan disimpan! Memulai Engine...")
        win.destroy() 

    themed_button(win, "Simpan Konfigurasi", save_admin, width=20).pack(pady=15)
    win.eval('tk::PlaceWindow . center')
    win.mainloop()

# =================================================================
# PANEL KONTROL SERVER
# =================================================================

from werkzeug.serving import make_server


def rotate_session_key(app):
    """Kunci cookie login baru setiap server dinyalakan: semua sesi lama tidak berlaku -> wajib login lagi.
    (Matikan/Nyalakan Server di controller hanya mengganti thread web; proses & app-nya tetap sama.)"""
    if os.getenv('SECRET_KEY'):
        return                      # kunci tetap dari environment -> sengaja tidak diganti
    app.config['SECRET_KEY'] = secrets.token_hex(32)
    logging.info("[SECURE] Kunci sesi baru dibuat: semua user perlu login ulang.")


class FlaskServerThread(threading.Thread):
    def __init__(self, app):
        threading.Thread.__init__(self)
        # Menggunakan make_server agar bisa dimatikan via perintah
        self.server = make_server('0.0.0.0', 5001, app)
        self.daemon = True

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()

class TkinterLogger(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg + '\n')
            self.text_widget.see(tk.END)
            self.text_widget.configure(state='disabled')
        self.text_widget.after(0, append)

def start_controller_gui(app):
    ctrl = tk.Tk()
    ctrl.title("SMC Server Controller")
    ctrl.geometry("550x580")
    ctrl.resizable(False, False)
    apply_theme(ctrl)
    try:
        if os.path.exists(ICON_FILE): ctrl.iconbitmap(default=ICON_FILE)
    except: pass
    
    tk.Label(ctrl, text="Sistem Monitor CCTV - Panel Kontrol", font=(UI_FONT, 14, "bold"), fg=UI_BLUE).pack(pady=(12,4))
    status_label = tk.Label(ctrl, text="● Server Mati", fg=UI_MUTED, font=(UI_FONT, 10, "bold"))
    status_label.pack(pady=2)

    server_thread = None

    def start_server():
        nonlocal server_thread
        global current_file_handler
        if server_thread is None or not server_thread.is_alive():
            now = datetime.now()
            bulan_indo = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", 
                          "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
            nama_bulan = bulan_indo[now.month - 1]
            time_format = now.strftime('%d-%m-%y_%H-%M-%S')
            log_dir = os.path.join(BASE_PATH, "app_logs", nama_bulan)
            os.makedirs(log_dir, exist_ok=True)
            new_log_path = os.path.join(log_dir, f"{time_format}.log")

            # Hapus ikatan file log sebelumnya (server bisa dimatikan & dinyalakan berulang kali)
            if current_file_handler:
                root_logger.removeHandler(current_file_handler)
                current_file_handler.close()
            
            current_file_handler = logging.FileHandler(new_log_path, encoding='utf-8')
            # Format khusus file log (lebih detail dari GUI)
            file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            current_file_handler.setFormatter(file_formatter)
            root_logger.addHandler(current_file_handler)
            logging.info(f"=========== SESSION STARTED: {time_format} WIB ===========")

            rotate_session_key(app)
            server_thread = FlaskServerThread(app)
            server_thread.start()
            status_label.config(text="● Server Berjalan", fg=UI_BLUE)
            btn_start.config(state="disabled")
            btn_stop.config(state="normal")
            logging.info("[SYSTEM] Server Flask berhasil dihidupkan pada Port 5001.")
        
    def stop_server():
        nonlocal server_thread
        if server_thread is not None and server_thread.is_alive():
            logging.info("[SYSTEM] Mematikan Server Flask secara aman...")
            server_thread.shutdown()
            server_thread.join() # Tunggu sampai thread benar-benar mati
            status_label.config(text="● Server Mati", fg=UI_MUTED)
            btn_start.config(state="normal")
            btn_stop.config(state="disabled")
            logging.info("[SYSTEM] Server Flask telah dimatikan. GUI tetap menyala.")
            server_thread = None
            
    def open_browser(): webbrowser.open("http://localhost:5001")
    
    def on_closing_app():
        if server_thread is not None and server_thread.is_alive():
            server_thread.shutdown()

        global current_file_handler
        if current_file_handler:
            root_logger.removeHandler(current_file_handler)
            current_file_handler.close()
            
        ctrl.destroy()
        os._exit(0)

    btn_frame = tk.Frame(ctrl)
    btn_frame.pack(pady=10)
    btn_start = themed_button(btn_frame, "Nyalakan Server", start_server)
    btn_start.grid(row=0, column=0, padx=5)
    btn_web = themed_button(btn_frame, "Buka Web SMC", open_browser)
    btn_web.grid(row=0, column=1, padx=5)
    btn_stop = themed_button(btn_frame, "Matikan Server", stop_server, primary=False)
    btn_stop.grid(row=0, column=2, padx=5)
    btn_stop.config(state="disabled") # Disabled saat awal buka

    # =============================================================
    # BACKUP & RESTORE DATA (lihat apps/backup_manager.py)
    # =============================================================
    from apps import backup_manager as bm

    backup_frame = tk.Frame(ctrl)
    backup_frame.pack(pady=(0, 2))
    lbl_last_backup = tk.Label(ctrl, text="", font=(UI_FONT, 9, "bold"))
    lbl_last_backup.pack(pady=(0, 8))

    def refresh_last_backup():
        try:
            txt, color = bm.last_backup_text(INITIAL_CONFIG_FILE)
        except Exception as e:
            txt, color = f"Backup terakhir: tidak bisa dibaca ({e})", "#C62828"
        lbl_last_backup.config(text=txt, fg=color)

    def set_busy(busy):
        state = "disabled" if busy else "normal"
        btn_backup.config(state=state)
        btn_restore.config(state=state)
        if busy:
            btn_start.config(state="disabled")
        elif server_thread is None or not server_thread.is_alive():
            btn_start.config(state="normal")

    def run_in_background(job, on_done):
        """Jalankan job di thread terpisah supaya jendela tidak 'Not Responding'."""
        result = {}
        def worker():
            try: result['ok'] = job()
            except Exception as e: result['err'] = e
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        def poll():
            if t.is_alive():
                ctrl.after(300, poll)
            else:
                on_done(result.get('ok'), result.get('err'))
        ctrl.after(300, poll)

    def do_backup():
        try:
            db_params = bm.read_db_params(INITIAL_CONFIG_FILE, decode_key)
        except Exception as e:
            messagebox.showerror("Backup Gagal", str(e)); return
        set_busy(True)
        logging.info("[BACKUP] Membuat backup database...")
        def job():
            return bm.create_backup(db_params, INITIAL_CONFIG_FILE, summary=bm.collect_summary(app))
        def done(path, err):
            set_busy(False); refresh_last_backup()
            if err:
                logging.error(f"[BACKUP] Gagal: {err}")
                messagebox.showerror("Backup Gagal", str(err))
            else:
                logging.info(f"[BACKUP] Berhasil: {path}")
                messagebox.showinfo("Backup Berhasil", f"Backup tersimpan di:\n\n{path}")
        run_in_background(job, done)

    def do_restore():
        if server_thread is not None and server_thread.is_alive():
            messagebox.showwarning("Matikan Server Dulu",
                                   "Restore hanya bisa dilakukan saat server MATI.\n\nKlik 'Matikan Server' terlebih dahulu.")
            return
        from tkinter import filedialog
        backup_dir = bm.get_backup_dir(INITIAL_CONFIG_FILE)
        zip_path = filedialog.askopenfilename(
            title="Pilih File Backup SMC",
            initialdir=backup_dir if os.path.isdir(backup_dir) else BASE_PATH,
            filetypes=[("Backup SMC", "SMC_Backup_*.zip"), ("File ZIP", "*.zip")])
        if not zip_path: return
        try:
            manifest = bm.read_backup_manifest(zip_path)
            db_params = bm.read_db_params(INITIAL_CONFIG_FILE, decode_key)
        except Exception as e:
            messagebox.showerror("Restore Gagal", str(e)); return

        msg = (f"File: {os.path.basename(zip_path)}\n\n{bm.describe_summary(manifest)}\n\n"
               "⚠️ SEMUA DATA SAAT INI akan DIGANTI dengan isi backup ini.\n"
               "(Data saat ini otomatis disimpan dulu sebagai backup '_sebelum-restore'.)\n\n"
               "Lanjutkan restore?")
        if not messagebox.askyesno("Konfirmasi Restore", msg, icon="warning"): return

        set_busy(True)
        logging.info(f"[RESTORE] Memulihkan data dari {zip_path} ...")
        def job():
            return bm.restore_backup(zip_path, db_params, INITIAL_CONFIG_FILE, app=app)
        def done(safety_path, err):
            set_busy(False); refresh_last_backup()
            if err:
                logging.error(f"[RESTORE] Gagal: {err}")
                messagebox.showerror("Restore Gagal",
                                     f"{err}\n\nData TIDAK berubah (restore dibatalkan otomatis).")
            else:
                logging.info(f"[RESTORE] Berhasil. Backup pengaman: {safety_path}")
                messagebox.showinfo("Restore Berhasil",
                                    "Data berhasil dipulihkan.\n\nKlik 'Nyalakan Server' untuk mulai memakai SMC lagi.\n\n"
                                    f"Backup data sebelum restore:\n{safety_path}")
        run_in_background(job, done)

    def open_backup_folder():
        folder = bm.get_backup_dir(INITIAL_CONFIG_FILE)
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except Exception as e:
            messagebox.showerror("Error", f"Folder backup tidak bisa dibuka:\n{folder}\n\n{e}")

    btn_backup = themed_button(backup_frame, "Backup Data", do_backup)
    btn_backup.grid(row=0, column=0, padx=5)
    btn_restore = themed_button(backup_frame, "Restore Backup", do_restore, primary=False)
    btn_restore.grid(row=0, column=1, padx=5)
    btn_folder = themed_button(backup_frame, "Folder Backup", open_backup_folder, primary=False)
    btn_folder.grid(row=0, column=2, padx=5)
    refresh_last_backup()

    tk.Label(ctrl, text="Console Log", font=(UI_FONT, 9, "bold"), fg=UI_BLUE).pack(anchor="w", padx=20)
    log_frame = tk.Frame(ctrl)
    log_frame.pack(padx=20, pady=5, fill="both", expand=True)
    log_text = tk.Text(log_frame, state='disabled', bg="#F5F9FF", fg=UI_TEXT, font=("Consolas", 9), height=15,
                       relief="solid", bd=1, highlightthickness=0)
    log_scroll = tk.Scrollbar(log_frame, command=log_text.yview)
    log_text.configure(yscrollcommand=log_scroll.set)
    log_text.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")

    root_logger = logging.getLogger()
    gui_handler = TkinterLogger(log_text)
    gui_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root_logger.addHandler(gui_handler)

    try:
        with open(new_log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            log_text.configure(state='normal')
            log_text.insert(tk.END, "".join(lines[-20:]))
            log_text.see(tk.END)
            log_text.configure(state='disabled')
    except: pass

    # Intercept tombol X merah di pojok kanan atas jendela
    ctrl.protocol("WM_DELETE_WINDOW", on_closing_app) 
    ctrl.eval('tk::PlaceWindow . center')
    ctrl.mainloop()

# =================================================================
# MAIN
# =================================================================
if __name__ == "__main__":
    setup_first_admin()

    import apps.authentication.routes
    import apps.home.routes
    from flask import send_from_directory
    from flask_login import login_required
    from apps import create_app, db
    from apps.config import config_dict
    from apps.authentication.models import Users

    cp = configparser.ConfigParser(interpolation=None)
    cp.read(INITIAL_CONFIG_FILE)
    
    if 'DATABASE' in cp:
        raw_db_pass = decode_key(cp['DATABASE']['db_pass']) 
        db_p = urllib.parse.quote_plus(raw_db_pass)         
        db_u = cp['DATABASE']['db_user']
        db_h = cp['DATABASE']['db_host']
        db_n = cp['DATABASE']['db_name']
        
        uri = f"postgresql://{db_u}:{db_p}@{db_h}:5432/{db_n}"
        config_dict['Production'].SQLALCHEMY_DATABASE_URI = uri
        logging.info("[SECURE] Target DB Locked & Parsed securely.")

    # SECRET_KEY acak, TIDAK disimpan ke file: cookie login tidak bisa dipalsukan
    # dan cookie dari sesi sebelumnya tidak berlaku lagi.
    # Kunci juga diganti setiap kali tombol "Nyalakan Server" ditekan (lihat rotate_session_key) ->
    # server dinyalakan ulang = semua user harus login lagi.
    if not os.getenv('SECRET_KEY'):
        config_dict['Production'].SECRET_KEY = secrets.token_hex(32)

    app = create_app(config_dict['Production'])
    
    # Tampilkan Info DB secara disensor
    masked_uri = f"postgresql://{db_u}:***_ENCRYPTED_***@{db_h}:5432/{db_n}"
    logging.info(f"[SECURE] Database Configuration Loaded: {masked_uri}")
    
    with app.app_context():
        try:
            db.create_all()
            logging.info("[DB] Sinkronisasi tabel PostgreSQL berhasil.")
        except Exception as e:
            logging.error(f"[DB] ERROR PEMBUATAN TABEL: {e}")

        # Migrasi kolom baru. Tiap ALTER berdiri sendiri: 1 gagal tidak membatalkan yang lain.
        for ddl in (
            'ALTER TABLE camera ADD COLUMN IF NOT EXISTS internal_ip VARCHAR(50)',
            'ALTER TABLE site ADD COLUMN IF NOT EXISTS daily_report_time VARCHAR(10)',
            'ALTER TABLE weekly_status_snapshot ADD COLUMN IF NOT EXISTS is_ctp BOOLEAN DEFAULT FALSE',
        ):
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logging.warning(f"[DB] Migrasi dilewati ({ddl}): {e}")

        try:
            if 'CREDENTIALS' in cp:
                setup_user = cp['CREDENTIALS']['USERNAME']
                raw_web_pass = decode_key(cp['CREDENTIALS']['PASSWORD']) 
                
                if raw_web_pass != "" and not Users.query.filter_by(username=setup_user).first():
                    
                    # Password dikirim mentah: model Users meng-hash password sendiri.
                    new_admin = Users(
                        username=setup_user, 
                        email=cp['CREDENTIALS']['EMAIL'], 
                        password=raw_web_pass, 
                        role='Superadmin'
                    )
                    db.session.add(new_admin)
                    db.session.commit()
                    
                    db.session.execute(text(f"""
                        UPDATE users SET 
                        can_manage_users=TRUE, can_view_dashboard=TRUE, can_view_report=TRUE,
                        can_edit_daily_report=TRUE, can_manage_master_location=TRUE, can_view_ticket=TRUE,
                        can_manage_email_settings=TRUE, can_manage_system_settings=TRUE, can_view_weekly_report=TRUE,
                        can_view_weekly_hdd=TRUE, can_view_daily_report=TRUE, can_view_daily_camera=TRUE,
                        can_view_daily_hdd=TRUE, can_view_ticket_management=TRUE, can_manage_ticket_log=TRUE,
                        can_view_ticket_archive=TRUE
                        WHERE username='{setup_user}'
                    """))
                    db.session.commit()
                    logging.info(f"[DB] SUKSES: Admin '{setup_user}' berhasil disuntik masuk!")
                    
                    cp.set('CREDENTIALS', 'PASSWORD', '*** TERHAPUS OLEH SISTEM ***')
                    with open(INITIAL_CONFIG_FILE, 'w') as f:
                        cp.write(f)
                    logging.info("[SECURE] Jejak Password Web telah dibumihanguskan dari config.ini!")
                    
                else:
                    logging.info(f"[DB] Pengecekan Admin: Sudah Aman.")
        except Exception as e:
            logging.error(f"[DB] FATAL ERROR SAAT INJEKSI: {e}")
            db.session.rollback()

    # Foto CCTV hanya bisa dibuka oleh user yang sudah login
    @app.route('/media/<path:filename>')
    @login_required
    def custom_media(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    
    start_controller_gui(app)