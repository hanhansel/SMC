# apps/home/routes_master_location.py
import os
import shutil
from datetime import datetime, date
from flask import render_template, request, url_for, redirect, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from apps import db
from apps.home import blueprint
from apps.home.models import Camera, MasterLocation, DeviceStatus, WeeklyStatusSnapshot, Site
from sqlalchemy import func

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@blueprint.route('/master_location')
@login_required
def master_location():
    # Ambil semua site untuk dropdown
    sites = Site.query.all()
    
    # Ambil semua kamera (channel > 0)
    cameras = Camera.query.filter(Camera.channel > 0).all()
    
    # Ambil data NVR (channel 0) untuk mapping NVR ke Site di frontend
    nvrs = Camera.query.filter(Camera.channel == 0).all()
    
    device_statuses = {stat.camera_id: stat for stat in DeviceStatus.query.all()}
    
    return render_template('home/master_location.html', 
                           segment='master_location', 
                           cameras=cameras, 
                           sites=sites, 
                           nvrs=nvrs,
                           device_statuses=device_statuses)

@blueprint.route('/master_location/sync', methods=['POST'])
@login_required
def sync_master_location():
    if not current_user.can_manage_master_location:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk mengupdate Master Location.", "danger")
        return redirect(url_for('home_blueprint.master_location'))

    success_count = 0
    skip_count = 0
    today = date.today()
    latest_scans = WeeklyStatusSnapshot.query.filter(
        func.date(WeeklyStatusSnapshot.snapshot_date) == today,
        WeeklyStatusSnapshot.screenshot_path != None,
        WeeklyStatusSnapshot.status != 'Available' # Abaikan channel kosong
    ).all()

    # Folder foto lokasi: data_storage/master_location
    base_storage = current_app.config['UPLOAD_FOLDER']
    master_folder = os.path.join(base_storage, 'master_location')
    os.makedirs(master_folder, exist_ok=True)

    # Catat kamera yang sudah diproses agar tidak diproses 2x
    processed_camera_ids = set()

    # =========================================================
    # Pre-fetch data Master Location & NVR (hindari query N+1)
    # =========================================================
    cam_ids = [s.camera_id for s in latest_scans]
    
    masters = MasterLocation.query.filter(MasterLocation.camera_id.in_(cam_ids)).all() if cam_ids else []
    master_dict = {m.camera_id: m for m in masters}
    
    cams = Camera.query.filter(Camera.id.in_(cam_ids)).all() if cam_ids else []
    cam_dict = {c.id: c for c in cams}
    
    ip_addrs = list(set(c.ip_address for c in cams))
    nvrs = Camera.query.filter(Camera.ip_address.in_(ip_addrs), Camera.channel == 0).all() if ip_addrs else []
    nvr_dict = {n.ip_address: n for n in nvrs}
    # =========================================================

    for scan in latest_scans:
        # Kalau kamera ini sudah diproses di putaran sebelumnya, lewati!
        if scan.camera_id in processed_camera_ids:
            continue
            
        processed_camera_ids.add(scan.camera_id) # Catat ID-nya

        master = master_dict.get(scan.camera_id)
        
        # Lokasi asli hasil screenshot harian
        source_path = os.path.join(base_storage, scan.screenshot_path.replace('/', os.sep))
        
        if os.path.exists(source_path):
            # Nama NVR
            cam = cam_dict.get(scan.camera_id)
            if not cam: continue
            parent_nvr = nvr_dict.get(cam.ip_address)
            nvr_name = parent_nvr.name.replace(" ", "_").upper() if parent_nvr and parent_nvr.name else "UNKNOWN_NVR"
            
            filename = f"{nvr_name}_CH_{cam.channel}_MASTER.jpg"
            target_path = os.path.join(master_folder, filename)
            db_path = f"master_location/{filename}" 
            
            try:
                shutil.copy2(source_path, target_path)
                if not master:
                    new_master = MasterLocation(camera_id=scan.camera_id, image_path=db_path, last_updated=datetime.now())
                    db.session.add(new_master)
                else:
                    master.image_path = db_path
                    master.last_updated = datetime.now()
                success_count += 1
            except Exception as e:
                print(f"Gagal copy master location: {e}")
                skip_count += 1
        else:
            skip_count += 1

    try:
        db.session.commit()
        if success_count > 0: flash(f"✅ Berhasil mengunci {success_count} gambar ke Master Location.", "success")
        else: flash("⚠️ Tidak ada gambar baru yang ditarik.", "warning")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Terjadi kesalahan saat sinkronisasi: {e}", "danger")

    return redirect(url_for('home_blueprint.master_location'))

@blueprint.route('/master_location/upload/<int:camera_id>', methods=['POST'])
@login_required
def upload_master_location(camera_id):
    if not current_user.can_manage_master_location:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.master_location'))

    if 'file' not in request.files:
        flash("❌ Tidak ada file yang diunggah.", "danger")
        return redirect(url_for('home_blueprint.master_location'))
        
    file = request.files['file']
    if file.filename == '':
        flash("❌ File kosong.", "warning")
        return redirect(url_for('home_blueprint.master_location'))

    if file and allowed_file(file.filename):
        base_storage = current_app.config['UPLOAD_FOLDER']
        master_folder = os.path.join(base_storage, 'master_location')
        os.makedirs(master_folder, exist_ok=True)
        
        # Nama NVR
        cam = Camera.query.get(camera_id)
        parent_nvr = Camera.query.filter_by(ip_address=cam.ip_address, channel=0).first() if cam else None
        nvr_name = parent_nvr.name.replace(" ", "_").upper() if parent_nvr and parent_nvr.name else "UNKNOWN_NVR"
        
        filename = secure_filename(f"{nvr_name}_CH_{cam.channel}_MASTER.jpg")       
        file_path = os.path.join(master_folder, filename)
        file.save(file_path)
        db_path = f"master_location/{filename}"
        
        master = MasterLocation.query.filter_by(camera_id=camera_id).first()
        if not master:
            master = MasterLocation(camera_id=camera_id, image_path=db_path)
            db.session.add(master)
        else:
            master.image_path = db_path
            master.last_updated = datetime.now()
            
        try:
            db.session.commit()
            flash(f"✅ Foto referensi manual berhasil diunggah!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Gagal menyimpan ke database: {e}", "danger")

    return redirect(url_for('home_blueprint.master_location'))