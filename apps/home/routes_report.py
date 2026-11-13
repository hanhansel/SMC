# apps/home/routes_report.py
import io
import os
import shutil
from datetime import datetime, date, timedelta
import PIL
import PIL.Image as PILImage
import openpyxl.utils
from flask import render_template, request, url_for, redirect, flash, jsonify, send_file, current_app, make_response
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import PatternFill, Alignment, Font, Border, Side
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from apps import db
from apps.home import blueprint, SCREENSHOT_BASE_PATH, STATUS_MAP
from apps.home.models import Site, Camera, WeeklyStatusSnapshot, HDDStatus, HDDStatusHistory, TicketNotification, DeviceStatus
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import smtplib
from email.message import EmailMessage
from apps.home.models import EmailSettings, TicketNotification
from apps.home.hdd_checker import decrypt_password
from apps.home.camera_naming import camera_display_name
from apps.branding import TICKET_FORMAT
from apps.home.report_status import (
    NOT_FOUND_LABEL, STATUS_UNCHECKABLE, UNCHECKABLE_LABEL, status_label,
    has_visual_evidence, snapshot_display_status, status_css_class,
    write_status_cell,
)
from apps.home.ticket_rules import evaluate_camera_tickets, evaluate_hdd_tickets

# NVR yang tidak bisa dicek / mati (termasuk label alternatif) -> placeholder baris NVR
NVR_UNCHECKABLE_STATUSES = [STATUS_UNCHECKABLE, "Offline", "Error", "No Video"]

# --------------------------------------------------------------------------
# UPDATE TO REPORT (KAMERA & HDD SEKALIGUS)
# --------------------------------------------------------------------------
@blueprint.route('/update_to_report', methods=['POST'])
@login_required
def update_to_report():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    hari_ini = datetime.now().date() 
    recent_statuses = DeviceStatus.query.filter(
        db.func.date(DeviceStatus.checked_at) == hari_ini
    ).all()

    if not recent_statuses:
        flash("⚠️ Tidak ada data hasil scan hari ini. Jalankan Cek AI dulu!", "warning")
        return redirect(url_for('home_blueprint.report_daily'))
    
    today = datetime.now().date() 
    upload_folder = current_app.config['UPLOAD_FOLDER'] 
    perm_screenshot_folder = os.path.join(upload_folder, 'screenshots')
    os.makedirs(perm_screenshot_folder, exist_ok=True)
    
    cameras = Camera.query.join(Site).filter(Camera.is_active == True, db.or_(Site.ai_report_enabled == True, Site.is_daily_report == True)).all()
    cam_count = 0
    today_snaps = []  # snapshot hari ini -> dievaluasi untuk ticket di akhir
    
    # Pre-fetch NVR induk sekaligus (hindari query N+1)
    all_parent_nvrs = Camera.query.filter_by(channel=0).all()
    nvr_dict = {n.ip_address: n for n in all_parent_nvrs}
    
    # 1. UPDATE DATA KAMERA & COPY FOTO
    for cam in cameras:
        if not cam.status_record or not cam.status_record.checked_at:
            continue
        if cam.status_record.checked_at.date() != today:
            continue 

        status_val = cam.status_record.status
        date_str = datetime.now().strftime('%Y%m%d')
        
        # Nama NVR & site untuk folder foto
        parent_nvr = nvr_dict.get(cam.ip_address)
        nvr_name = parent_nvr.name.replace(" ", "_").upper() if parent_nvr and parent_nvr.name else "UNKNOWN_NVR"
        site_name = cam.site.name.replace(" ", "_").upper() if cam.site and cam.site.name else "UNKNOWN_SITE"
        
        # Folder foto: screenshots/SITE/NVR/TANGGAL
        relative_dir = os.path.join('screenshots', site_name, nvr_name, date_str)
        perm_screenshot_folder = os.path.join(upload_folder, relative_dir)
        os.makedirs(perm_screenshot_folder, exist_ok=True)
        
        filename = f"{nvr_name}_CH_{cam.channel}_{date_str}.jpg"
        temp_path = os.path.join(SCREENSHOT_BASE_PATH, filename)
        perm_path = os.path.join(perm_screenshot_folder, filename)
        
        if os.path.exists(temp_path):
            try: shutil.copy2(temp_path, perm_path)
            except Exception as e: print(f"Gagal copy gambar: {e}")

        # Foto lama tidak dipakai ulang untuk kamera CTP / Offline.
        # Foto hanya untuk status Online yang snapshot-nya berhasil.
        details_text = cam.status_record.details or ""
        if "CTP" in details_text or status_val != "Online":
            db_path = None
        else:
            if os.path.exists(perm_path):
                db_path = os.path.join(relative_dir, filename).replace('\\', '/')
            else:
                db_path = cam.status_record.screenshot_path if cam.status_record else None
        is_ctp = status_val == "Online" and not db_path
        
        existing_snap = WeeklyStatusSnapshot.query.filter(
            WeeklyStatusSnapshot.camera_id == cam.id,
            func.date(WeeklyStatusSnapshot.snapshot_date) == today
        ).first()
        
        if existing_snap:
            # Snapshot hari ini selalu mengikuti hasil scan terakhir
            existing_snap.status = status_val
            existing_snap.screenshot_path = db_path # None jika sekarang CTP / Offline
            existing_snap.is_ctp = is_ctp
            existing_snap.snapshot_date = datetime.now()
            today_snaps.append(existing_snap)
        else:
            new_snap = WeeklyStatusSnapshot(
                camera_id=cam.id, status=status_val, is_ctp=is_ctp,
                snapshot_date=datetime.now(), screenshot_path=db_path
            )
            db.session.add(new_snap)
            today_snaps.append(new_snap)

        cam_count += 1

    # 2. HDD HISTORY: Snapshot hari ini = cerminan PENUH scan HDD terakhir per NVR.
    # Baris hari ini diganti total (bukan upsert per slot) supaya kalau jumlah slot berubah
    # saat scan ulang di hari yang sama, slot lama tidak tertinggal di report.
    # Hanya NVR dari Site yang ai_report_enabled (sama dengan cakupan scan HDD di hdd_checker)
    # Hanya HDD yang DI-SCAN HARI INI (sama seperti kamera) -> data lama tidak ikut tercatat sebagai hari ini
    realtime_hdd_map = {}
    hdd_rows = HDDStatus.query.join(Camera, HDDStatus.camera_id == Camera.id)\
        .join(Site, Camera.site_id == Site.id)\
        .filter(Site.ai_report_enabled == True, func.date(HDDStatus.last_checked) == today).all()
    for hdd in hdd_rows:
        realtime_hdd_map.setdefault(hdd.camera_id, []).append(hdd)

    hdd_count = 0
    if realtime_hdd_map:
        hdd_nvr_ids = list(realtime_hdd_map.keys())
        hdd_nvr_set = {n.id: n.current_hdd_set for n in Camera.query.filter(Camera.id.in_(hdd_nvr_ids)).all()}
        HDDStatusHistory.query.filter(
            HDDStatusHistory.camera_id.in_(hdd_nvr_ids),
            HDDStatusHistory.snapshot_date == today
        ).delete(synchronize_session=False)

        for nvr_id, hdd_list in realtime_hdd_map.items():
            for hdd in hdd_list:
                db.session.add(HDDStatusHistory(
                    camera_id=hdd.camera_id,
                    disk_no=hdd.disk_no,
                    status=hdd.status,
                    capacity_at_time=hdd.capacity,
                    free_space_at_time=hdd.free_space,
                    hdd_set_at_time=hdd_nvr_set.get(nvr_id),    # Label Rotasi Set (bukan property R/W)
                    serial_number_at_time=hdd.serial_number,
                    brand_at_time=hdd.brand,
                    snapshot_date=today
                ))
                hdd_count += 1

    # 3. AUTO-CLEANUP FOTO (> 150 hari)
    # Riwayat STATUS kamera & HDD disimpan SELAMANYA (ukurannya kecil, dibutuhkan untuk
    # laporan jangka panjang). Yang dihapus hanya FILE FOTO lama karena ukurannya besar;
    # baris snapshot tetap ada, hanya path fotonya dikosongkan.
    cleanup_date = today - timedelta(days=150)
    old_snaps = WeeklyStatusSnapshot.query.filter(
        func.date(WeeklyStatusSnapshot.snapshot_date) <= cleanup_date,
        WeeklyStatusSnapshot.screenshot_path.isnot(None)
    ).all()
    deleted_files = 0

    for snap in old_snaps:
        file_path = os.path.join(upload_folder, snap.screenshot_path.replace('/', os.sep))
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                deleted_files += 1
            except Exception:
                continue  # gagal hapus -> path dibiarkan, dicoba lagi di update berikutnya
        snap.screenshot_path = None

    try:
        # 4. TICKET: hanya dibuat / ditutup di sini (bukan saat scan)
        cam_new, cam_closed = evaluate_camera_tickets(today_snaps, today)
        hdd_new, hdd_closed = evaluate_hdd_tickets(list(realtime_hdd_map.keys()), today)
        db.session.commit()
        msg = f"✅ Terupdate! Cam: {cam_count} | HDD: {hdd_count} | Tiket baru: {cam_new + hdd_new} | Tiket ditutup: {cam_closed + hdd_closed}."
        if deleted_files > 0: msg += f" (Auto-Cleanup: {deleted_files} foto lama dihapus)"
        flash(msg, "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal: {e}", "danger")

    return redirect(request.referrer or url_for('home_blueprint.index'))

from flask_login import current_user

MANUAL_STATUSES = ['Online', 'Offline', 'Available']


def _snapshot_url(path):
    if not path:
        return None
    clean = path.replace('\\', '/')
    if clean.startswith('static/'):
        return url_for('static', filename=clean.replace('static/', ''))
    return f"/media/{clean}"


def revise_snapshot(camera_id, new_status, target_date, file=None):
    """Revisi status 1 kamera di kolom report tanggal target (dipakai halaman Weekly & popup Hasil Scan).
    Return (snapshot, tiket_dibuat, tiket_ditutup); snapshot None kalau kolom tanggal itu belum ada.
    Belum di-commit."""
    snap = WeeklyStatusSnapshot.query.filter(
        WeeklyStatusSnapshot.camera_id == camera_id,
        func.date(WeeklyStatusSnapshot.snapshot_date) == target_date
    ).first()
    if not snap:
        return None, 0, 0

    cam = Camera.query.get(camera_id)
    snap.status = new_status
    upload_folder = current_app.config['UPLOAD_FOLDER']

    parent_nvr = Camera.query.filter_by(ip_address=cam.ip_address, channel=0).first() if cam else None
    nvr_name = parent_nvr.name.replace(" ", "_").upper() if parent_nvr and parent_nvr.name else "UNKNOWN_NVR"
    site_name = cam.site.name.replace(" ", "_").upper() if cam and cam.site and cam.site.name else "UNKNOWN_SITE"
    clean_date = target_date.strftime('%Y%m%d')
    relative_dir = os.path.join('screenshots', site_name, nvr_name, clean_date)
    perm_screenshot_folder = os.path.join(upload_folder, relative_dir)

    if new_status == 'Online':
        if file and file.filename != '':
            os.makedirs(perm_screenshot_folder, exist_ok=True)
            filename = secure_filename(f"{nvr_name}_CH_{cam.channel}_{clean_date}_manual.jpg")
            file.save(os.path.join(perm_screenshot_folder, filename))
            snap.screenshot_path = os.path.join(relative_dir, filename).replace('\\', '/')
        elif not snap.screenshot_path and os.path.exists(perm_screenshot_folder):
            # Smart recovery: pakai foto hasil scan hari ini kalau ada
            expected_prefix = f"{nvr_name}_CH_{cam.channel}_{clean_date}"
            for f in os.listdir(perm_screenshot_folder):
                if f.startswith(expected_prefix) and f.endswith(('.jpg', '.png', '.jpeg')):
                    snap.screenshot_path = os.path.join(relative_dir, f).replace('\\', '/')
                    break
        snap.is_ctp = not snap.screenshot_path  # Online tanpa foto = CTP
    else:
        snap.screenshot_path = None
        snap.is_ctp = False

    # Tiket: aturan yang SAMA dengan Update to Report (lihat ticket_rules.py)
    created, closed = evaluate_camera_tickets([snap], target_date)

    # Sinkronkan ke status terakhir supaya scan berikutnya / dashboard konsisten
    dev_status = DeviceStatus.query.filter_by(camera_id=camera_id).first()
    if dev_status:
        dev_status.status = new_status
    return snap, created, closed


@blueprint.route('/override_weekly_status', methods=['POST'])
@login_required
def override_weekly_status():
    """Revisi status manual (hanya kolom HARI INI).
    Dipanggil lewat AJAX dari halaman Weekly (balas JSON, halaman tidak reload / tidak lompat ke atas).
    Tetap mendukung form biasa (redirect) sebagai cadangan."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def reply(ok, message, category, **extra):
        if is_ajax:
            return jsonify({'ok': ok, 'message': message, **extra}), (200 if ok else 400)
        flash(message, category)
        return redirect(url_for('home_blueprint.weekly_report'))

    if not current_user.can_edit_daily_report:
        return reply(False, "❌ Akses Ditolak.", "danger")

    camera_id = request.form.get('camera_id')
    new_status = request.form.get('new_status')
    target_date_str = request.form.get('target_date')
    if new_status not in MANUAL_STATUSES:
        return reply(False, "❌ Status tidak valid.", "danger")

    try:
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        if target_date != date.today():
            return reply(False, "❌ Anda hanya diizinkan mengedit laporan untuk HARI INI.", "warning")

        snap, created, closed = revise_snapshot(camera_id, new_status, target_date, request.files.get('screenshot_file'))
        if not snap:
            return reply(False, "❌ Data tidak ditemukan.", "danger")

        db.session.commit()

        display = snapshot_display_status(snap)
        msg = "✅ Status berhasil direvisi."
        if created: msg += " Ticket dibuat."
        if closed: msg += " Ticket ditutup."
        return reply(True, msg, "success",
                     camera_id=int(camera_id), status=snap.status,
                     label=status_label(display), css_class=status_css_class(display),
                     screenshot_url=_snapshot_url(snap.screenshot_path) if has_visual_evidence(display) else None)
    except Exception as e:
        db.session.rollback()
        return reply(False, f"❌ Gagal merevisi data: {e}", "danger")

# --------------------------------------------------------------------------
# TAMPILAN WEB REPORT (KAMERA)
# --------------------------------------------------------------------------
@blueprint.route('/weekly_report')
@login_required
def weekly_report():
    if not current_user.can_view_report:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    # Rentang 90 hari (3 bulan), sama dengan Export Excel
    ninety_days_ago = date.today() - timedelta(days=90)
    unique_dates_query = db.session.query(func.date(WeeklyStatusSnapshot.snapshot_date))\
        .filter(func.date(WeeklyStatusSnapshot.snapshot_date) >= ninety_days_ago)\
        .distinct().order_by(func.date(WeeklyStatusSnapshot.snapshot_date)).all()
    
    # func.date() bisa berupa str (SQLite) atau date (PostgreSQL)
    target_dates = []
    for r in unique_dates_query:
        if r[0] is None: continue
        if isinstance(r[0], str): 
            target_dates.append(datetime.strptime(r[0], '%Y-%m-%d').date())
        else: 
            target_dates.append(r[0]) # Jika PostgreSQL, langsung masukkan karena sudah berupa Date

    date_headers = [d.strftime("%d %b %Y") for d in target_dates]
    data_rows = []
    all_snapshots = WeeklyStatusSnapshot.query.filter(
        WeeklyStatusSnapshot.snapshot_date.isnot(None),
        func.date(WeeklyStatusSnapshot.snapshot_date) >= ninety_days_ago
    ).all()

    status_pivot = {}
    for s in all_snapshots: 
        status_pivot[(s.camera_id, s.snapshot_date.date())] = snapshot_display_status(s)
    
    # Foto terbaru per kamera (path di folder static atau data_storage)
    latest_screenshot_map = {}
    sorted_snaps = sorted(all_snapshots, key=lambda x: x.snapshot_date)
    for s in sorted_snaps:
        if not has_visual_evidence(s.status):
            latest_screenshot_map[s.camera_id] = None
        elif s.screenshot_path: 
            clean_path = s.screenshot_path.replace('\\', '/')
            if clean_path.startswith('static/'):
                 latest_screenshot_map[s.camera_id] = url_for('static', filename=clean_path.replace('static/', ''))
            else:
                 latest_screenshot_map[s.camera_id] = f"/media/{clean_path}"
        else:
            # Online tanpa foto (CTP) -> tidak ada gambar
            latest_screenshot_map[s.camera_id] = None

    no = 1
    # Pre-fetch semua channel sekaligus (hindari query N+1)
    all_channels = Camera.query.filter(Camera.channel > 0).all()
    channel_dict = {}
    for ch in all_channels:
        channel_dict.setdefault(ch.ip_address, []).append(ch)

    # Pre-fetch DeviceStatus NVR untuk bedakan Offline vs Online-tapi-kosong
    all_nvr_objs = Camera.query.filter_by(channel=0).all()
    nvr_dev_status_map = {}
    nvr_ids_all = [n.id for n in all_nvr_objs]
    if nvr_ids_all:
        for ds in DeviceStatus.query.filter(DeviceStatus.camera_id.in_(nvr_ids_all)).all():
            nvr_dev_status_map[ds.camera_id] = ds.status

    all_sites = Site.query.order_by(Site.id).all()
    for site in all_sites:
        nvrs = sorted([c for c in site.cameras if c.channel == 0], key=lambda x: x.sort_order)
        for nvr in nvrs:
            channels = channel_dict.get(nvr.ip_address, [])
            channels.sort(key=lambda x: x.channel)
            
            if not channels and nvr.device_type != 'NVR': channels = [nvr]
            elif not channels:
                # Bedakan: NVR offline/timeout vs NVR online tapi belum ada kamera
                nvr_live_status = nvr_dev_status_map.get(nvr.id, None)
                if nvr_live_status in NVR_UNCHECKABLE_STATUSES:
                    # NVR tidak bisa di-reach → tampil sebagai Offline
                    placeholder_statuses = [STATUS_UNCHECKABLE] * len(target_dates)
                    placeholder_cam_name = "(NVR Tidak Bisa Dicek)"
                else:
                    # NVR online tapi belum ada channel yang di-discover
                    placeholder_statuses = [NOT_FOUND_LABEL] * len(target_dates)
                    placeholder_cam_name = "(Belum Ada Data Kamera)"
                data_rows.append({
                    "camera_id": nvr.id,
                    "no": no,
                    "site_name": site.name,
                    "nvr_name": nvr.name,
                    "ip_address": nvr.ip_address,
                    "channel_number": "-",
                    "camera_name": placeholder_cam_name,
                    "statuses": placeholder_statuses,
                    "rowspan": 1,
                    "screenshot_path": None
                })
                no += 1
                continue

            first_channel = True
            for ch in channels:
                historic_statuses = []
                for target_date in target_dates:
                    historic_statuses.append(status_pivot.get((ch.id, target_date), NOT_FOUND_LABEL))

                # Status hari ini = status terakhir di list
                latest_status = historic_statuses[-1] if historic_statuses else NOT_FOUND_LABEL
                
                # Jika Available, kosongkan IP. Jika tidak, pakai IP Internal / IP NVR.
                if latest_status == "Available":
                    ip_to_show = "-"
                else:
                    ip_to_show = ch.internal_ip if ch.internal_ip else ch.ip_address

                data_rows.append({
                    "camera_id": ch.id,
                    "no": no if first_channel and nvr.device_type == 'NVR' else "", 
                    "site_name": site.name if first_channel and nvr.device_type == 'NVR' else "", 
                    "nvr_name": nvr.name,
                    "ip_address": ip_to_show,
                    "channel_number": ch.channel,
                    "camera_name": ch.name,
                    "statuses": historic_statuses, 
                    "rowspan": len(channels) if first_channel and nvr.device_type == 'NVR' else 0,
                    "screenshot_path": latest_screenshot_map.get(ch.id, None)
                })
                first_channel = False
            no += 1 

    return render_template("home/weekly_report.html", data=data_rows, date_headers=date_headers, today=datetime.now().strftime("%d %B %Y"), today_ymd=datetime.now().strftime("%Y-%m-%d"), sites=Site.query.all(), snapshot_dates=[d.strftime("%Y-%m-%d") for d in target_dates])

def generate_camera_excel_full_history(site_id=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Weekly Report"
    
    # 1. Siapkan Header Tanggal
    today_date = datetime.now().date()
    from datetime import timedelta
    ninety_days_ago = today_date - timedelta(days=90)
    
    unique_dates_query = db.session.query(func.date(WeeklyStatusSnapshot.snapshot_date))\
        .filter(func.date(WeeklyStatusSnapshot.snapshot_date) >= ninety_days_ago)\
        .distinct().order_by(func.date(WeeklyStatusSnapshot.snapshot_date)).all()

    target_dates = []
    for r in unique_dates_query:
        if r[0] is None: continue
        if isinstance(r[0], str): 
            target_dates.append(datetime.strptime(r[0], '%Y-%m-%d').date())
        else: 
            target_dates.append(r[0]) # Jika PostgreSQL, langsung masukkan karena sudah berupa Date
    
    static_headers = ["No", "Nama Site", "Nama NVR", "IP Address", "Channel", "Nama Kamera", "Screenshot"]
    dynamic_headers = [d.strftime("%d-%b-%Y") for d in target_dates]
    headers = static_headers + dynamic_headers
    ws.append(headers)
    
    # Styling Header
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = Font(bold=True, color="000000")
        cell.fill = PatternFill("solid", fgColor="BDD7EE")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # 2. Build lookup dicts — loop Site→NVR(by id)→Channel(by ch number)
    #    Sama persis dengan struktur live view agar urutan selalu konsisten
    all_nvrs = Camera.query.filter_by(channel=0, device_type='NVR').all()
    all_channels_objs = Camera.query.filter(Camera.channel > 0).all()
    if site_id:
        all_nvrs_filtered = [n for n in all_nvrs if n.site_id == int(site_id)]
        all_channels_filtered = [c for c in all_channels_objs if c.site_id == int(site_id)]
    else:
        all_nvrs_filtered = all_nvrs
        all_channels_filtered = all_channels_objs

    # Bangun map IP → channels
    channels_by_ip = {}
    for ch in all_channels_filtered:
        channels_by_ip.setdefault(ch.ip_address, []).append(ch)

    # Bangun map site_id → NVR list
    nvrs_by_site = {}
    for nvr in all_nvrs_filtered:
        nvrs_by_site.setdefault(nvr.site_id, []).append(nvr)

    # Pre-fetch semua snapshot untuk semua channel yang ada
    all_ch_ids = [c.id for c in all_channels_filtered]
    all_snapshots = WeeklyStatusSnapshot.query.filter(
        WeeklyStatusSnapshot.camera_id.in_(all_ch_ids)
    ).all() if all_ch_ids else []

    status_pivot = {}
    latest_screenshot_map = {}
    for s in sorted(all_snapshots, key=lambda x: x.snapshot_date):
        status_pivot[(s.camera_id, s.snapshot_date.date())] = snapshot_display_status(s)
        if not has_visual_evidence(s.status):
            latest_screenshot_map[s.camera_id] = None
        elif s.screenshot_path:
            latest_screenshot_map[s.camera_id] = s.screenshot_path
        else:
            latest_screenshot_map[s.camera_id] = None

    # Pre-fetch DeviceStatus NVR untuk bedakan Offline vs kosong
    all_nvr_ids = [n.id for n in all_nvrs_filtered]
    nvr_dev_status_map = {}
    if all_nvr_ids:
        for ds in DeviceStatus.query.filter(DeviceStatus.camera_id.in_(all_nvr_ids)).all():
            nvr_dev_status_map[ds.camera_id] = ds.status

    # ── LOOP UTAMA: Site → NVR (by id) → Channel (by ch number) ──
    all_sites_q = Site.query.order_by(Site.id).all()
    if site_id:
        all_sites_q = [s for s in all_sites_q if s.id == int(site_id)]

    row_idx = 2
    actual_idx = 1

    def _write_status_cells(ws, row_idx, cam_id, target_dates, status_pivot, col_offset=8):
        # Label & warna standar (report_status.py). NO DATA TIDAK diubah jadi Offline.
        for i, t_date in enumerate(target_dates):
            write_status_cell(ws, row_idx, col_offset + i, status_pivot.get((cam_id, t_date)))

    def _write_border_align(ws, row_idx, col_count=7):
        for c in range(1, col_count + 1):
            ws.cell(row=row_idx, column=c).alignment = Alignment(vertical='center', horizontal='center')
            ws.cell(row=row_idx, column=c).border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    for site in all_sites_q:
        site_nvrs = sorted(nvrs_by_site.get(site.id, []), key=lambda x: x.sort_order)
        for nvr in site_nvrs:
            channels = sorted(channels_by_ip.get(nvr.ip_address, []), key=lambda x: x.channel)

            if not channels:
                # NVR tanpa channel: bedakan Offline vs online-tapi-kosong
                nvr_live_status = nvr_dev_status_map.get(nvr.id)
                is_offline = nvr_live_status in NVR_UNCHECKABLE_STATUSES
                ws.cell(row=row_idx, column=1, value=actual_idx)
                ws.cell(row=row_idx, column=2, value=site.name)
                ws.cell(row=row_idx, column=3, value=nvr.name)
                ws.cell(row=row_idx, column=4, value=nvr.ip_address)
                ws.cell(row=row_idx, column=5, value="-")
                ws.cell(row=row_idx, column=6, value="(NVR Tidak Bisa Dicek)" if is_offline else "(Belum Ada Data Kamera)")
                ws.cell(row=row_idx, column=7, value="N/A")
                ws.row_dimensions[row_idx].height = 20
                for i in range(len(target_dates)):
                    write_status_cell(ws, row_idx, 8 + i, STATUS_UNCHECKABLE if is_offline else NOT_FOUND_LABEL)
                _write_border_align(ws, row_idx)
                row_idx += 1
                actual_idx += 1
                continue

            for ch in channels:
                # Export per Site / Semua = REKAP LENGKAP: kamera tetap tampil walaupun
                # berkali-kali NO DATA. Hanya dilewati jika sama sekali tidak punya
                # catatan snapshot dalam 90 hari (belum pernah masuk laporan).
                has_any_record = any((ch.id, t_date) in status_pivot for t_date in target_dates)
                if not has_any_record:
                    continue

                latest_status = status_pivot.get((ch.id, target_dates[-1]), "-") if target_dates else "-"
                ip_to_show = "-" if "Available" in latest_status else (ch.internal_ip if ch.internal_ip else ch.ip_address)

                ws.cell(row=row_idx, column=1, value=actual_idx)
                ws.cell(row=row_idx, column=2, value=site.name)
                ws.cell(row=row_idx, column=3, value=nvr.name)
                ws.cell(row=row_idx, column=4, value=ip_to_show)
                ws.cell(row=row_idx, column=5, value=ch.channel)
                ws.cell(row=row_idx, column=6, value=ch.name)
                ws.row_dimensions[row_idx].height = 60
                ws.column_dimensions['G'].width = 18

                # Gambar screenshot
                snap_path = latest_screenshot_map.get(ch.id)
                has_img = False
                if snap_path and has_visual_evidence(latest_status):
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    path_in_storage = os.path.join(upload_folder, snap_path) if upload_folder else None
                    path_in_static = os.path.join(current_app.root_path, 'static', snap_path)
                    final_full_path = None
                    if path_in_storage and os.path.exists(path_in_storage): final_full_path = path_in_storage
                    elif os.path.exists(path_in_static): final_full_path = path_in_static
                    if final_full_path:
                        try:
                            with PILImage.open(final_full_path) as pil_img:
                                img = ExcelImage(final_full_path)
                                img.width = 120
                                img.height = 75
                                ws.add_image(img, f"{openpyxl.utils.get_column_letter(7)}{row_idx}")
                                has_img = True
                        except: pass
                if not has_img:
                    ws.cell(row=row_idx, column=7, value="N/A")

                _write_status_cells(ws, row_idx, ch.id, target_dates, status_pivot)
                _write_border_align(ws, row_idx)
                row_idx += 1
                actual_idx += 1

    return wb

@blueprint.route('/export_weekly_report')
@login_required
def export_weekly_report():
    if not current_user.can_view_report:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    try:
        wb = generate_camera_excel_full_history(site_id=None)
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        date_str = datetime.now().strftime('%d %m %Y')
        return send_file(out, download_name=f"Laporan_Kamera_All_{date_str}.xlsx", as_attachment=True)
    except Exception as e: return f"Error: {e}", 500

@blueprint.route("/export_weekly_report_by_site", methods=['POST'])
@login_required
def export_weekly_report_by_site():
    if not current_user.can_view_report:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    site_id = request.form.get('site_id')
    try:
        wb = generate_camera_excel_full_history(site_id=site_id)
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        site = Site.query.get(site_id)
        name = site.name.replace(" ", "_") if site else "Site"
        date_str = datetime.now().strftime('%d %m %Y')
        return send_file(out, download_name=f"Laporan_Kamera_{name}_{date_str}.xlsx", as_attachment=True)
    except Exception as e:
        flash(f"Gagal: {e}", "danger")
        return redirect(url_for('home_blueprint.weekly_report'))

@blueprint.route("/delete_snapshots_by_date", methods=['POST'])
@login_required
def delete_snapshots_by_date():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    date_str = request.form.get('target_date')
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        start_of_day = datetime.combine(target_date, datetime.min.time())
        end_of_day = datetime.combine(target_date, datetime.max.time())
        
        snapshots = WeeklyStatusSnapshot.query.filter(
            WeeklyStatusSnapshot.snapshot_date >= start_of_day,
            WeeklyStatusSnapshot.snapshot_date <= end_of_day
        ).all()
        
        row_count = len(snapshots) 
        upload_folder = current_app.config.get('UPLOAD_FOLDER', '')
        deleted_files_count = 0
        
        for snap in snapshots:
            if snap.screenshot_path:
                file_path = os.path.join(upload_folder, snap.screenshot_path.replace('/', os.sep).replace('\\', os.sep))
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        deleted_files_count += 1
                    except Exception as e:
                        print(f"Gagal hapus file {file_path}: {e}")
        
        WeeklyStatusSnapshot.query.filter(
            WeeklyStatusSnapshot.snapshot_date >= start_of_day,
            WeeklyStatusSnapshot.snapshot_date <= end_of_day
        ).delete(synchronize_session='fetch')
        
        HDDStatusHistory.query.filter(HDDStatusHistory.snapshot_date == target_date).delete(synchronize_session='fetch')
        
        kamera_zombie = DeviceStatus.query.filter(
            db.func.date(DeviceStatus.checked_at) == target_date
        ).all()
        
        for cam in kamera_zombie:
            cam.checked_at = cam.checked_at - timedelta(days=1)
            
        db.session.commit()
        
        flash(f"✅ Sukses! {row_count} Data Laporan dan {deleted_files_count} Foto tanggal {date_str} telah dihapus bersih.", 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal Hapus: {e}", 'danger')
        
    return redirect(url_for('home_blueprint.weekly_report'))

@blueprint.route('/ticket_checker')
@login_required
def ticket_checker():
    if not current_user.can_view_ticket:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk membuka Ticket Checker.", "danger")
        return redirect(url_for('home_blueprint.index'))
    selected_site_id = request.args.get('site_id')

    query = TicketNotification.query.filter_by(status='Open').join(Camera)
    if selected_site_id:
        query = query.filter(Camera.site_id == selected_site_id)
    
    open_tickets = query.all()

    cam_ids = [t.camera_id for t in open_tickets]
    ip_addrs = list(set(t.camera.ip_address for t in open_tickets if t.camera))
    
    parent_nvrs = Camera.query.filter(Camera.ip_address.in_(ip_addrs), Camera.channel == 0).options(db.joinedload(Camera.status_record)).all() if ip_addrs else []
    nvr_dict = {nvr.ip_address: nvr for nvr in parent_nvrs}
    
    all_snaps = WeeklyStatusSnapshot.query.filter(WeeklyStatusSnapshot.camera_id.in_(cam_ids)).order_by(WeeklyStatusSnapshot.snapshot_date.desc()).all() if cam_ids else []
    snaps_dict = {}
    for s in all_snaps:
        snaps_dict.setdefault(s.camera_id, []).append(s)
        
    active_children = Camera.query.filter(Camera.ip_address.in_(ip_addrs), Camera.is_active == True, Camera.channel > 0).all() if ip_addrs else []
    active_child_count = {}
    for child in active_children:
        active_child_count[child.ip_address] = active_child_count.get(child.ip_address, 0) + 1

    ticket_list = []
    grouped_data = {}

    for ticket in open_tickets:
        cam = ticket.camera
        parent_nvr = nvr_dict.get(cam.ip_address)
        nvr_name = parent_nvr.name if parent_nvr else "Unknown NVR"

        is_nvr_dead = False
        if parent_nvr and parent_nvr.status_record and parent_nvr.status_record.status in NVR_UNCHECKABLE_STATUSES + ['Tidak Ditemukan']:
            is_nvr_dead = True

        ticket_date = ticket.created_at.date() if isinstance(ticket.created_at, datetime) else ticket.created_at
        cam_snaps = snaps_dict.get(cam.id, [])
        past_snap = next((s for s in cam_snaps if (s.snapshot_date.date() if isinstance(s.snapshot_date, datetime) else s.snapshot_date) < ticket_date), None)
        
        prev_status = past_snap.status if past_snap else "Unknown"
        prev_date = past_snap.snapshot_date.strftime('%d %b %Y') if past_snap and past_snap.snapshot_date else "-"

        display_img = None
        
        if cam.master_location and cam.master_location.image_path:
            clean_path = cam.master_location.image_path.replace('\\', '/')
            display_img = f"/media/{clean_path}"
            
        elif past_snap and past_snap.screenshot_path:
            clean_path = past_snap.screenshot_path.replace('\\', '/')
            if clean_path.startswith('static/'):
                display_img = url_for('static', filename=clean_path.replace('static/', ''))
            else:
                display_img = f"/media/{clean_path}"

        ticket_list.append({
            'site_name': cam.site.name if cam.site else "Unknown",
            'nvr_name': nvr_name, 
            'camera_name': cam.name, 
            'ip_address': cam.ip_address,
            'internal_ip': cam.internal_ip or "-", 
            'channel': cam.channel,
            'issue_type': ticket.issue_type,
            'model': cam.model,
            'since': ticket.created_at.strftime('%d %b %Y, %H:%M'),
            'image': display_img, 
            'prev_status': prev_status,
            'prev_date': prev_date
        })

        nvr_ip = cam.ip_address
        if nvr_ip not in grouped_data:
            grouped_data[nvr_ip] = {
                "site_name": cam.site.name if cam.site else "-",
                "nvr_name": nvr_name, "ip_address": nvr_ip,
                "cameras": [], "internal_ips": [],
                "is_nvr_dead": is_nvr_dead 
            }
        
        grouped_data[nvr_ip]["cameras"].append(f"Ch {cam.channel}")
        if cam.internal_ip: grouped_data[nvr_ip]["internal_ips"].append(cam.internal_ip)

    grouped_ticket = []
    for ip, data in grouped_data.items():
        
        total_active_children = active_child_count.get(ip, 0)
        child_ticket_count = len([c for c in data["cameras"] if not str(c).startswith("Ch 0")])
        
        if total_active_children > 0 and child_ticket_count == total_active_children:
            data["is_nvr_dead"] = True

        if data["is_nvr_dead"]:
            data["cameras_str"] = "⚠️ NVR MATI TOTAL"
            data["internal_ips_str"] = "N/A (NVR Offline)"
        else:
            data["cameras_str"] = ", ".join(data["cameras"])
            data["internal_ips_str"] = ", ".join(data["internal_ips"]) if data["internal_ips"] else "-"
            
        grouped_ticket.append(data)

    return render_template(
        "home/ticket_checker.html", 
        ticket_list=ticket_list, 
        grouped_ticket=grouped_ticket, 
        sites=Site.query.all(), 
        selected_site_id=selected_site_id
    )

def process_and_send_ticket_email(is_manual=False):
    settings = EmailSettings.query.first()
    
    if not settings or not settings.sender_email or not settings.sender_password or not settings.receiver_emails:
        return False, "Pengaturan Auto Email belum lengkap. Silakan isi di menu Settings."

    open_ticket_list = TicketNotification.query.filter_by(status='Open').all()
    if not open_ticket_list:
        return True, "Aman! Tidak ada ticket yang Open saat ini."

    nvr_groups = {}
    
    all_parent_nvrs = Camera.query.filter_by(channel=0).all()
    nvr_dict = {nvr.ip_address: nvr for nvr in all_parent_nvrs}
    
    counts = db.session.query(Camera.ip_address, func.count(Camera.id)).filter(Camera.is_active == True, Camera.channel > 0).group_by(Camera.ip_address).all()
    child_count_dict = {ip: count for ip, count in counts}

    for ticket in open_ticket_list:
        cam = ticket.camera
        ip = cam.ip_address

        if ip not in nvr_groups:
            parent_nvr = nvr_dict.get(ip)
            nvr_groups[ip] = {
                'site_name': cam.site.name if cam.site else "Unknown Site",
                'nvr_name': parent_nvr.name if parent_nvr else cam.name,
                'total_child_cameras': child_count_dict.get(ip, 0),
                'child_tickets': [],
                'has_hdd_ticket': False,
                'has_nvr_down_ticket': False
            }

        # Tiket channel 0 dibedakan dari issue_type-nya: HDD Error vs NVR Offline / NVR Issue
        if cam.channel == 0 and ticket.issue_type == 'HDD Error':
            nvr_groups[ip]['has_hdd_ticket'] = True
        elif cam.channel == 0:
            nvr_groups[ip]['has_nvr_down_ticket'] = True
        else:
            nvr_groups[ip]['child_tickets'].append(cam)

    ticket_dict = {}
    for ip, data in nvr_groups.items():
        site = data['site_name']
        nvr = data['nvr_name']

        if site not in ticket_dict:
            ticket_dict[site] = {}
        if nvr not in ticket_dict[site]:
            ticket_dict[site][nvr] = []

        all_children_down = data['total_child_cameras'] > 0 and len(data['child_tickets']) == data['total_child_cameras']
        if data['has_nvr_down_ticket'] or all_children_down:
            ticket_dict[site][nvr].append(f"[NVR ERROR] NVR Offline / Mati Total | IP: {ip}")
            continue 

        if data['has_hdd_ticket']:
            ticket_dict[site][nvr].append(f"[HDD ERROR] IP: {ip}")

        for child_cam in data['child_tickets']:
            ticket_dict[site][nvr].append(f"[CAMERA ISSUE] Cam: {camera_display_name(nvr, child_cam.name, child_cam.channel, include_nvr=False, model=child_cam.model)} | IP: {ip}")

    ticket_text_lines = []
    for site, nvrs in ticket_dict.items():
        ticket_text_lines.append(f"SITE: {site}")
        for nvr, issues in nvrs.items():
            ticket_text_lines.append(f"   NVR: {nvr}")
            for issue in issues:
                ticket_text_lines.append(f"      ➔ {issue}")
        ticket_text_lines.append("") 

    ticket_data_str = "\n".join(ticket_text_lines)

    today_str = datetime.now().strftime('%d-%b-%Y %H:%M')
    raw_template = settings.email_template or "Title: SMC AUTO TICKET REPORT - {date}\n\n{ticket_data}"
    
    lines = raw_template.split('\n')
    subject = lines[0].replace('Title:', '').replace('{date}', today_str).strip()
    
    base_body = "\n".join(lines[1:]).replace('{date}', today_str).replace('{ticket_data}', ticket_data_str).strip()

    auto_footer = "\n\n" + "="*50 + "\nThis is an auto-generated email from the SMC System.\nPlease do not reply to this email."
    body = base_body + auto_footer

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = settings.sender_email
        msg['To'] = settings.receiver_emails
        if settings.cc_emails:
            msg['Cc'] = settings.cc_emails
            
        msg.set_content(body)

        real_password = decrypt_password(settings.sender_password)

        with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.sender_email, real_password)
            server.send_message(msg)

        return True, f"Email berhasil dikirim ke {settings.receiver_emails}!"
    except Exception as e:
        return False, f"Gagal mengirim email SMTP: {str(e)}"

@blueprint.route('/send_manual_ticket_email', methods=['POST'])
@login_required
def send_manual_ticket_email():
    if not current_user.can_view_ticket:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.ticket_checker'))
        
    success, message = process_and_send_ticket_email(is_manual=True)
    
    if success:
        if "Aman!" in message:
            flash(f'👍 {message}', 'info') 
        else:
            flash(f'✅ {message}', 'success') 
    else:
        flash(f'❌ {message}', 'danger') 
        
    return redirect(url_for('home_blueprint.ticket_checker'))


# =========================================================
# TICKET LOG: insiden aktif & tiket yang belum di-log
# =========================================================
@blueprint.route('/ticket_log', methods=['GET'])
@login_required
def ticket_log():
    if not current_user.can_manage_ticket_log:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.index'))
        
    # Hanya tiket yang belum di-log
    open_ticket = TicketNotification.query.filter_by(is_logged=False).order_by(TicketNotification.created_at.desc()).all()
    
    # Ambil data NVR untuk mapping nama parent NVR
    nvrs = Camera.query.filter_by(channel=0).all()
    nvr_dict = {nvr.ip_address: nvr for nvr in nvrs}

    return render_template("home/ticket_log.html", open_ticket=open_ticket, nvr_dict=nvr_dict)

# =========================================================
# TICKET ARCHIVE: histori tiket + filter
# =========================================================
@blueprint.route('/ticket_archive', methods=['GET'])
@login_required
def ticket_archive():
    if not current_user.can_view_ticket_archive:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.index'))
        
    sites = Site.query.all()
    selected_site = request.args.get('site_id')
    selected_month = request.args.get('month')
    search_ticket = request.args.get('ticket_number', '')
    
    # Nomor halaman dari URL (default 1)
    page = request.args.get('page', 1, type=int)

    query_arsip = TicketNotification.query.filter_by(is_logged=True)

    if selected_site:
        query_arsip = query_arsip.join(Camera).filter(Camera.site_id == int(selected_site))
    if selected_month:
        try:
            year, month = map(int, selected_month.split('-'))
            query_arsip = query_arsip.filter(
                func.extract('year', TicketNotification.created_at) == year,
                func.extract('month', TicketNotification.created_at) == month
            )
        except ValueError:
            pass
    if search_ticket:
        query_arsip = query_arsip.filter(TicketNotification.ticket_number.ilike(f"%{search_ticket}%"))

    # Paginasi di database: 50 data per halaman
    pagination = query_arsip.order_by(TicketNotification.resolved_at.desc()).paginate(page=page, per_page=50, error_out=False)
    resolved_ticket = pagination.items

    # Ambil data NVR untuk mapping nama parent NVR
    nvrs = Camera.query.filter_by(channel=0).all()
    nvr_dict = {nvr.ip_address: nvr for nvr in nvrs}

    return render_template(
        "home/ticket_archive.html",
        sites=sites,
        resolved_ticket=resolved_ticket,
        pagination=pagination,
        selected_site=selected_site,
        selected_month=selected_month,
        search_ticket=search_ticket,
        nvr_dict=nvr_dict
    )

@blueprint.route('/submit_ticket_log', methods=['POST'])
@login_required
def submit_ticket_log():
    if not current_user.can_manage_ticket_log:
        return jsonify({'status': 'error', 'message': 'Akses ditolak.'}), 403
        

    ticket_id = request.form.get('ticket_id')
    ticket_number = request.form.get('ticket_number')
    masalah = request.form.get('masalah')
    perbaikan = request.form.get('perbaikan')

    try:
        ticket = TicketNotification.query.get(ticket_id)
        if not ticket:
            flash("❌ Data ticket tidak ditemukan.", "danger")
            return redirect(url_for('home_blueprint.ticket_log'))

        # Simpan ke database
        ticket.ticket_number = ticket_number
        ticket.masalah = masalah
        ticket.perbaikan = perbaikan
        ticket.is_logged = True # Tiket keluar dari hitungan notifikasi lonceng

        db.session.commit()
        flash(f"✅ Log untuk {ticket.camera.name} berhasil disimpan!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal menyimpan log: {e}", "danger")

    return redirect(url_for('home_blueprint.ticket_log'))

@blueprint.route('/delete_ticket_log/<int:ticket_id>', methods=['POST'])
@login_required
def delete_ticket_log(ticket_id):
    # Pastikan yang bisa hapus hanya yang punya akses Ticket Log
    if not current_user.can_manage_ticket_log:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.ticket_log'))

    try:
        ticket = TicketNotification.query.get(ticket_id)
        if ticket:
            db.session.delete(ticket)
            db.session.commit()
            flash("✅ Tiket False Alarm berhasil dihapus!", "success")
        else:
            flash("❌ Data ticket tidak ditemukan.", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal menghapus log: {e}", "danger")

    return redirect(url_for('home_blueprint.ticket_log'))


# --------------------------------------------------------------------------
# BULK DELETE TICKET LOG (Hapus Banyak Sekaligus via Checkbox)
# --------------------------------------------------------------------------
@blueprint.route('/bulk_delete_ticket_log', methods=['POST'])
@login_required
def bulk_delete_ticket_log():
    ticket_ids = request.form.getlist('ticket_ids') 
    
    if not ticket_ids:
        flash("⚠️ Tidak ada log yang dipilih untuk dihapus.", "warning")
        return redirect(url_for('home_blueprint.ticket_log'))

    try:
        TicketNotification.query.filter(TicketNotification.id.in_(ticket_ids)).delete(synchronize_session=False)
        db.session.commit()
        flash(f"✅ {len(ticket_ids)} Log Insiden berhasil dihapus!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal menghapus log massal: {e}", "danger")

    return redirect(url_for('home_blueprint.ticket_log'))

# --------------------------------------------------------------------------
# BULK UPDATE TICKET LOG (Isi Log Banyak NVR Sekaligus)
# --------------------------------------------------------------------------
@blueprint.route('/bulk_update_ticket_log', methods=['POST'])
@login_required
def bulk_update_ticket_log():
    # Menangkap string ID yang dipisahkan koma (contoh: "15,18,21")
    ticket_ids_str = request.form.get('ticket_ids') 
    
    if not ticket_ids_str:
        flash("⚠️ Tidak ada log yang dipilih.", "warning")
        return redirect(url_for('home_blueprint.ticket_log'))

    # Ambil inputan dari form modal
    raw_ticket_number = request.form.get('ticket_number')
    masalah = request.form.get('masalah')
    perbaikan = request.form.get('perbaikan')

    ticket_number_formatted = ""
    if raw_ticket_number:
        raw_ticket_number = raw_ticket_number.strip()
        # Cek apakah user hanya memasukkan angka murni
        if raw_ticket_number.isdigit():
            now = datetime.now()
            mm = now.strftime('%m') # Bulan (01-12)
            yy = now.strftime('%y') # Tahun 2 digit (26)
            ticket_number_formatted = TICKET_FORMAT.format(no=raw_ticket_number, mm=mm, yy=yy)
        else:
            # Bukan angka murni (format lengkap diketik manual) -> disimpan apa adanya
            ticket_number_formatted = raw_ticket_number

    # Ubah string "15,18,21" menjadi list angka [15, 18, 21]
    id_list = [int(i.strip()) for i in ticket_ids_str.split(',') if i.strip().isdigit()]

    try:
        # Panggil semua data ticket yang dicentang
        logs = TicketNotification.query.filter(TicketNotification.id.in_(id_list)).all()
        
        for log in logs:
            log.ticket_number = ticket_number_formatted or None   # boleh kosong -> tiket tetap diarsipkan
            # Kolom Akar Masalah / Perbaikan sudah tidak ada di form massal:
            # jangan timpa deskripsi masalah otomatis dengan nilai kosong.
            if 'masalah' in request.form:
                log.masalah = masalah
            if 'perbaikan' in request.form:
                log.perbaikan = perbaikan
            log.status = 'Resolved' # Status diubah supaya tiket pindah ke Archive
            log.is_logged = True
            # Jam selesai hanya diisi kalau tiket belum tertutup. Tiket yang sudah ditutup otomatis
            # (kamera Online lagi) tetap memakai jam aslinya -> rentang insiden di laporan tetap benar.
            if not log.resolved_at:
                log.resolved_at = datetime.now()
            
        db.session.commit()
        flash(f"✅ {len(logs)} Log Insiden berhasil diperbarui & dipindahkan ke Arsip!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal update log massal: {e}", "danger")

    return redirect(url_for('home_blueprint.ticket_log'))
    
# FILTER JINJA: Untuk menggeser UTC ke WIB (Waktu Indonesia Barat) di HTML
@blueprint.app_template_filter('to_wib')
def to_wib(dt_obj):
    if not dt_obj:
        return "-"
    # Geser UTC (+0) menjadi WIB (+7)
    return (dt_obj + timedelta(hours=7)).strftime('%d %b %Y %H:%M')