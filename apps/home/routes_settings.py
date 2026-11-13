import os
from flask import render_template, request, url_for, redirect, flash, send_file, jsonify
from cryptography.fernet import Fernet
from apps import db
import openpyxl
from io import BytesIO
from apps.home import blueprint
from apps.home.models import Site, Camera, EmailSettings
import sys
from flask_login import login_required, current_user

# Lokasi kunci enkripsi password NVR: <folder aplikasi>/system_data/.sys_config.key
def get_base_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    
    # Otomatis membuat folder system_data jika belum ada
    sys_data_dir = os.path.join(base_dir, 'system_data')
    os.makedirs(sys_data_dir, exist_ok=True)
    
    return sys_data_dir

KEY_FILE = os.path.join(get_base_path(), '.sys_config.key')

def get_cipher():
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, 'wb') as f:
            f.write(key)
    else:
        with open(KEY_FILE, 'rb') as f:
            key = f.read()
    return Fernet(key)
    
def encrypt_password(plain_text):
    if not plain_text: return ""
    return get_cipher().encrypt(plain_text.encode()).decode()

@blueprint.route('/settings')
@login_required
def settings_page():
    if not current_user.can_manage_system_settings:
        flash("❌ Akses Ditolak: Hanya Super Admin yang bisa mengakses pengaturan NVR.", "danger")
        return redirect(url_for('home_blueprint.index'))
        
    sites = Site.query.order_by(Site.id.asc()).all()
    cameras = Camera.query.filter_by(channel=0).order_by(Camera.sort_order.asc()).all()
    return render_template('home/settings_camera.html', segment='settings', sites=sites, cameras=cameras)

@blueprint.route('/update_nvr_order', methods=['POST'])
@login_required
def update_nvr_order():
    if not current_user.can_manage_users:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    try:
        data = request.get_json()
        order_list = data.get('order', [])
        for index, nvr_id in enumerate(order_list):
            cam = Camera.query.get(nvr_id)
            if cam:
                cam.sort_order = index
        db.session.commit()
        return jsonify({'success': True, 'message': 'Urutan berhasil disimpan!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@blueprint.route('/add_site', methods=['POST'])
@login_required
def add_site():
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))
    name = request.form.get('site_name')
    address = request.form.get('location_name')
    report_days = ','.join(request.form.getlist('report_days')) if request.form.getlist('report_days') else None
    daily_report_time = request.form.get('daily_report_time')
    ai_report_enabled = True if request.form.get('ai_report_enabled') == 'on' else False
    is_daily_report = True if request.form.get('is_daily_report') == 'on' else False

    try:
        new_site = Site(
            name=name, address=address, report_days=report_days,
            ai_report_enabled=ai_report_enabled, is_daily_report=is_daily_report,
            daily_report_time=daily_report_time
        )
        db.session.add(new_site)
        db.session.commit()
        flash('✅ Site berhasil ditambahkan', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal menambahkan Site: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/add_camera', methods=['POST'])
@login_required
def add_camera():
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    camera_name = request.form.get('camera_name')
    camera_type = request.form.get('camera_type', '')
    ip_address = request.form.get('ip_address')
    site_id = request.form.get('site_id')
    username = request.form.get('username')
    password = request.form.get('password')
    channel_count = int(request.form.get('channel_count') or 1)
    
    # Password NVR disimpan terenkripsi
    encrypted_pass = encrypt_password(password)

    try:
        if channel_count >= 1:
            parent_camera = Camera(
            name=camera_name, model=camera_type, ip_address=ip_address,
            site_id=int(site_id), device_type='NVR', channel_count=channel_count,
            username=username, password=encrypted_pass, channel=0
        )
        db.session.add(parent_camera)
        # Channel kamera ditambahkan otomatis oleh auto-discovery saat scan
        flash(f'✅ NVR ditambahkan!', 'success')
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/delete_site/<int:site_id>', methods=['POST'])
@login_required
def delete_site(site_id):
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))
    site = Site.query.get_or_404(site_id)
    try:
        db.session.delete(site)
        db.session.commit()
        flash('✅ Site dihapus!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/delete_camera/<int:camera_id>', methods=['POST'])
@login_required
def delete_camera(camera_id):
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    camera = Camera.query.get_or_404(camera_id)
    try:
        cams = Camera.query.filter_by(ip_address=camera.ip_address).all()
        for c in cams:
            db.session.delete(c)
        db.session.commit()
        flash('✅ NVR dihapus!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/edit_site/<int:site_id>', methods=['POST'])
@login_required
def edit_site_post(site_id):
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    site = Site.query.get_or_404(site_id)
    site.name = request.form.get('edited_site_name')
    site.address = request.form.get('edited_location_name')
    site.report_days = ','.join(request.form.getlist('edited_report_days')) if request.form.getlist('edited_report_days') else None
    site.daily_report_time = request.form.get('edited_daily_report_time')
    site.ai_report_enabled = True if request.form.get('edited_ai_report_enabled') == 'on' else False
    site.is_daily_report = True if request.form.get('edited_is_daily_report') == 'on' else False

    try:
        db.session.commit()
        flash('✅ Site diperbarui!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/edit_camera/<int:camera_id>', methods=['POST'])
@login_required
def edit_camera_post(camera_id):
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak: Anda tidak memiliki izin Super Admin.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    cam = Camera.query.get_or_404(camera_id)
    edited_name = request.form.get('edited_camera_name')
    edited_model = request.form.get('edited_camera_type')
    edited_ip = request.form.get('edited_ip_address')
    edited_site_id = int(request.form.get('edited_site_id'))
    edited_username = request.form.get('edited_username')
    edited_password = request.form.get('edited_password') # Jika kosong, ignore
    edited_channel_count = int(request.form.get('edited_channel_count') or 1)

    try:
        all_related = Camera.query.filter_by(ip_address=cam.ip_address).all()
        enc_pass = encrypt_password(edited_password) if edited_password else None

        for c in all_related:
            c.ip_address, c.site_id, c.model, c.username = edited_ip, edited_site_id, edited_model, edited_username
            if enc_pass: c.password = enc_pass # Timpa hanya jika diisi password baru
            
            if c.channel == 0:
                c.name, c.channel_count = edited_name, edited_channel_count
            else:
                c.name = f"{edited_name} - Ch {c.channel}"

        # Update password/username anak-anak channel yang sudah ditemukan (Auto-Discovered)
        for child in all_related:
            if child.channel > 0:
                if edited_username: child.username = edited_username
                if enc_pass: child.password = enc_pass

        # Daftar channel diatur oleh auto-discovery saat scan
        db.session.commit()
        flash('✅ NVR diperbarui!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal: {e}', 'danger')
    return redirect(url_for('home_blueprint.settings_page'))

# =========================================================
# AUTO EMAIL SETTINGS
# =========================================================

@blueprint.route('/auto_email_settings')
@login_required
def auto_email_settings():
    if not current_user.can_manage_email_settings:
        flash("❌ Akses Ditolak: Hanya Super Admin yang bisa mengakses pengaturan Email.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    # Setting email global: hanya 1 baris
    settings = EmailSettings.query.first()
    if not settings:
        settings = EmailSettings()
        db.session.add(settings)
        db.session.commit()
        
    return render_template('home/auto_email_settings.html', segment='auto_email_settings', settings=settings)

@blueprint.route('/save_email_settings', methods=['POST'])
@login_required
def save_email_settings():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.index'))
        
    settings = EmailSettings.query.first()
    if not settings:
        settings = EmailSettings()
        db.session.add(settings)
        
    # Tangkap data dari form web
    settings.smtp_server = request.form.get('smtp_server', 'smtp.office365.com')
    settings.smtp_port = int(request.form.get('smtp_port', 587))
    settings.sender_email = request.form.get('sender_email')
    settings.receiver_emails = request.form.get('receiver_emails')
    settings.cc_emails = request.form.get('cc_emails')
    settings.schedule_time = request.form.get('schedule_time')
    settings.is_active = True if request.form.get('is_active') == 'on' else False
    settings.email_template = request.form.get('email_template')
    
    # Password hanya diganti jika field diisi (kosong = password tersimpan tetap dipakai)
    new_password = request.form.get('sender_password')
    if new_password:
        settings.sender_password = encrypt_password(new_password)
        
    try:
        db.session.commit()
        flash('✅ Pengaturan Auto Email berhasil disimpan!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Gagal menyimpan pengaturan: {e}', 'danger')
        
    return redirect(url_for('home_blueprint.auto_email_settings'))

# =========================================================
# IMPORT EXCEL NVR
# =========================================================

@blueprint.route('/download_nvr_template')
@login_required
def download_nvr_template():
    if not current_user.can_manage_system_settings:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.settings_page'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template Import NVR"
    
    # Header kolom
    headers = [
        "Nama Site", "Lokasi Site", "Nama NVR", "Jumlah Channel", 
        "Model (UNV/HIKVISION/Lainnya)", "IP Address", "Username", "Password"
    ]
    ws.append(headers)
    
    # Contoh data di baris kedua
    ws.append(["Site Pusat", "Gedung A", "NVR Lobby", 8, "UNV", "192.168.1.100", "admin", "password123"])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    
    return send_file(out, download_name="Template_Data_NVR.xlsx", as_attachment=True)

@blueprint.route('/import_nvr_excel', methods=['POST'])
@login_required
def import_nvr_excel():
    if not current_user.can_manage_system_settings:
        flash('❌ Akses Ditolak.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    if 'excel_file' not in request.files:
        flash('❌ File tidak ditemukan.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    file = request.files['excel_file']
    if file.filename == '':
        flash('❌ File tidak valid.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))

    try:
        # Baca file Excel yang diupload
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        
        sites_created = 0
        nvrs_created = 0
        skipped_nvrs = 0

        # Mulai dari baris ke-2 (baris 1 = header)
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[0] or not row[5]: # Wajib ada Nama Site dan IP Address
                continue
                
            site_name = str(row[0]).strip()
            site_location = str(row[1]).strip() if row[1] else "-"
            nvr_name = str(row[2]).strip() if row[2] else "NVR Baru"
            
            # Jumlah channel kosong / bukan angka -> 4
            try: channel_count = int(row[3])
            except: channel_count = 4 
            
            # Normalisasi merk: scan hanya mengenal UNV / HIKVISION (merk lain masuk daftar Gagal Cek)
            model = str(row[4]).strip() if row[4] else "Lainnya"
            model_up = model.upper().replace(" ", "")
            if "UNV" in model_up or "UNIVIEW" in model_up: model = "UNV"
            elif "HIK" in model_up: model = "HIKVISION"
            ip_address = str(row[5]).strip()
            username = str(row[6]).strip() if row[6] is not None else ""
            raw_password = str(row[7]).strip() if row[7] is not None else ""

            # 1. CEK ATAU BUAT SITE
            site = Site.query.filter_by(name=site_name).first()
            if not site:
                site = Site(name=site_name, address=site_location)
                db.session.add(site)
                db.session.flush() # Flush supaya site.id tersedia sebelum commit
                sites_created += 1


            # 2. CEK NVR (Berdasarkan IP)
            existing_nvr = Camera.query.filter_by(ip_address=ip_address, channel=0).first()
            
            enc_pass = encrypt_password(raw_password) if raw_password else ""

            if existing_nvr:
                # Sudah ada: perbarui datanya
                existing_nvr.name = nvr_name
                existing_nvr.model = model
                existing_nvr.channel_count = channel_count # Sync channel_count
                
                # Update username & password HANYA jika di Excel ada isinya
                if username: 
                    existing_nvr.username = username
                if raw_password: 
                    existing_nvr.password = enc_pass
                
                child_channels = Camera.query.filter_by(ip_address=ip_address).filter(Camera.channel > 0).all()
                # Update password/username anak-anak channel yang sudah ditemukan
                for child in child_channels:
                    if username: child.username = username
                    if raw_password: child.password = enc_pass
                
                skipped_nvrs += 1 # Dihitung sebagai NVR yang diperbarui
                continue # Lanjut ke baris Excel berikutnya

            # 3. JIKA BELUM ADA: BUAT NVR BARU
            parent_camera = Camera(
                name=nvr_name, model=model, ip_address=ip_address,
                site_id=site.id, device_type='NVR', channel_count=channel_count,
                username=username, password=enc_pass, channel=0
            )
            db.session.add(parent_camera)
            # Channel kamera dibuat oleh auto-discovery saat scan
            
            nvrs_created += 1

        db.session.commit()
        
        # Pesan Sukses yang dinamis
        msg = f"Import selesai dengan {sites_created} Site baru dan {nvrs_created} NVR."
        if skipped_nvrs > 0:
            msg += f" ({skipped_nvrs} NVR dilewati karena IP sudah terdaftar)."
        flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Gagal membaca atau menyimpan data Excel: {str(e)}', 'danger')

    return redirect(url_for('home_blueprint.settings_page'))

# =========================================================
# BACKUP & RESTORE KUNCI ENKRIPSI
# =========================================================

@blueprint.route('/download_sys_key')
@login_required
def download_sys_key():
    if not current_user.can_manage_system_settings:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.settings_page'))
    
    if os.path.exists(KEY_FILE):
        return send_file(KEY_FILE, as_attachment=True, download_name=".sys_config.key")
    else:
        flash("❌ File kunci enkripsi belum digenerate oleh sistem.", "danger")
        return redirect(url_for('home_blueprint.settings_page'))

@blueprint.route('/upload_sys_key', methods=['POST'])
@login_required
def upload_sys_key():
    if not current_user.can_manage_system_settings:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.settings_page'))
    
    if 'key_file' not in request.files:
        flash('❌ File tidak ditemukan.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))
        
    file = request.files['key_file']
    if file.filename == '':
        flash('❌ File tidak valid.', 'danger')
        return redirect(url_for('home_blueprint.settings_page'))
        
    try:
        # Timpa file kunci saat ini dengan yang diunggah
        file.save(KEY_FILE)
        flash('✅ Kunci enkripsi berhasil dipulihkan (Restore). Password NVR kini dapat dibaca.', 'success')
    except Exception as e:
        flash(f'❌ Gagal menyimpan kunci: {e}', 'danger')
        
    return redirect(url_for('home_blueprint.settings_page'))
