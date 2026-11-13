# apps/home/routes.py

import csv
import os
import openpyxl
from io import StringIO, BytesIO
from datetime import datetime, date

from flask import render_template, request, url_for, redirect, flash, jsonify, make_response, current_app, send_file, session
from jinja2 import TemplateNotFound

from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.utils import get_column_letter

from apps import db
from apps.home import blueprint, STATUS_MAP, dashboard_status_key
import json
from apps.home.models import Site, Camera, DeviceStatus, WeeklyStatusSnapshot, HDDStatus, HDDStatusHistory, ScanRun
from apps.home.ai_report import process_camera_status 
from apps.home.hdd_checker import run_hdd_scan
from apps.home.hdd_report_builder import build_hdd_blocks, write_hdd_sheet
from flask_login import login_required, current_user

@blueprint.route('/index')
@login_required
def index():
    try:
        sites = Site.query.all()
        # Urutkan berdasarkan sort_order
        cameras = Camera.query.order_by(Camera.site_id, Camera.sort_order).all()
        all_latest_statuses = DeviceStatus.query.all()
        latest_status_map = {}
        for status in all_latest_statuses:
            checked_at_str = status.checked_at.strftime('%d-%m-%Y %H:%M:%S') if status.checked_at else 'N/A'
            status_key = dashboard_status_key(status.status)
            latest_status_map[status.camera_id] = {'status': status_key, 'checked_at': checked_at_str, 'details': status.details}
        scan_run, scan_data, show_scan = _scan_popup_context()
        return render_template('home/index.html', segment='index', sites=sites, cameras=cameras, latest_status_map=latest_status_map, STATUS_MAP=STATUS_MAP,
                               scan_run=scan_run, scan_data=scan_data, show_scan_popup=show_scan)
    except Exception as e:
        flash(f'Error: {e}', 'danger')
        return render_template('home/index.html', segment='index', sites=[], cameras=[], latest_status_map={}, STATUS_MAP=STATUS_MAP)

@blueprint.route('/api/latest_status')
@login_required
def api_latest_status():
    from apps.home.models import WeeklyStatusSnapshot
    from datetime import timedelta

    # Urutkan berdasarkan sort_order
    all_devices = Camera.query.options(db.joinedload(Camera.site)).order_by(Camera.site_id, Camera.sort_order).all()
    all_statuses = DeviceStatus.query.all()
    status_map = {status.camera_id: status for status in all_statuses}
    response_map = {}
    
    for device in all_devices:
        if device.channel == 0:
            status_entry = status_map.get(device.id)
            current_status = dashboard_status_key(status_entry.status) if status_entry else "Unknown"
            last_online_str = status_entry.last_online.isoformat() if status_entry and status_entry.last_online else None
            response_map[device.id] = {
                'id': device.id,
                'status': current_status,
                'name': device.name,
                'ip_address': device.ip_address,
                'site_name': device.site.name if device.site else "Unknown Site",
                'last_online': last_online_str,
                'child_channels': {} if device.device_type == 'NVR' else None
            }

    # Tanggal report snapshot terbaru di database
    latest_snap = WeeklyStatusSnapshot.query.order_by(WeeklyStatusSnapshot.snapshot_date.desc()).first()
    if latest_snap and latest_snap.snapshot_date:
        wib_time = latest_snap.snapshot_date + timedelta(hours=7)
        bulanIndo = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
        global_time_str = f"{wib_time.day} {bulanIndo[wib_time.month-1]}, {wib_time.year}, {str(wib_time.hour).zfill(2)}:{str(wib_time.minute).zfill(2)} WIB"
    else:
        global_time_str = "Belum Ada Data"

    return jsonify({'status_map': response_map, 'latest_report_time': global_time_str})

def _scan_popup_context():
    """Data popup Hasil Scan di dashboard: scan terakhir (+ apakah popup dibuka otomatis)."""
    show_id = session.pop('show_scan_run', None)
    run = ScanRun.query.get(show_id) if show_id else None
    if run is None:
        run = ScanRun.query.order_by(ScanRun.id.desc()).first()
    if run is None:
        return None, {}, False
    try:
        data = json.loads(run.result_json or "{}")
    except ValueError:
        data = {}
    # Hasil scan tanpa pemisahan nvr_gagal / hdd_gagal: dipisah di sini
    if 'nvr_gagal' not in data:
        allg = data.get('gagal', [])
        data['nvr_gagal'] = [g for g in allg if not str(g.get('api', '')).startswith('HDD')]
        data['hdd_gagal'] = [g for g in allg if str(g.get('api', '')).startswith('HDD')]
    data.setdefault('nodata', [])
    sites = {x.get('site') for key in ('ctp', 'nodata', 'nvr_gagal', 'hdd_gagal', 'hdd_unknown') for x in data.get(key, [])}
    data['multi_site'] = len(set(data.get('sites') or []) | sites) > 1
    if not data.get('sites'):
        # Hasil scan tanpa daftar site: diambil dari isi hasilnya
        data['sites'] = sorted(s for s in sites if s) or ([run.scope] if run.scope and not run.scope.startswith('Semua') else [])
    return run, data, show_id is not None


# 1. UPDATE MANUAL (SCAN REALTIME)

@blueprint.route('/trigger_status_check', methods=['POST'])
@login_required
def trigger_status_check():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    site_id = request.form.get('site_id')
    started = datetime.now()

    try:
        # Scan TIDAK membuat tiket. Hasilnya khusus scan ini -> popup Hasil Scan di dashboard.
        updates, cam_result = process_camera_status(target_site_id=site_id)
        hdd_result = run_hdd_scan(target_site_id=site_id)

        site = Site.query.get(int(site_id)) if site_id else None
        payload = {
            'updates': updates,
            'counts': cam_result.get('counts', {}),
            'ctp': cam_result.get('ctp', []),
            'nodata': cam_result.get('nodata', []),
            'sites': cam_result.get('sites', []),
            'nvr_gagal': cam_result.get('gagal', []),
            'hdd_gagal': hdd_result.get('gagal', []),
            'gagal': cam_result.get('gagal', []) + hdd_result.get('gagal', []),
            'hdd_unknown': hdd_result.get('unknown', []),
            'hdd_ok': hdd_result.get('ok_count', 0),
        }
        run = ScanRun(started_at=started, finished_at=datetime.now(),
                      scope=site.name if site else "Semua site dengan AI Scan aktif",
                      result_json=json.dumps(payload, ensure_ascii=False))
        db.session.add(run)
        db.session.commit()
        session['show_scan_run'] = run.id   # dashboard membuka popup Hasil Scan sekali
        return redirect(url_for('home_blueprint.index'))

    except Exception as e:
        db.session.rollback()
        flash(f'⚠️ Terjadi kesalahan saat update: {e}', 'danger')

    return redirect(url_for('home_blueprint.index'))

# 1b. HASIL SCAN -> popup di dashboard (URL /scan_result mengarah ke sana)
@blueprint.route('/scan_result')
@blueprint.route('/scan_result/<int:run_id>')
@login_required
def scan_result(run_id=None):
    run = ScanRun.query.get(run_id) if run_id else ScanRun.query.order_by(ScanRun.id.desc()).first()
    if not run:
        flash("Belum ada hasil scan. Jalankan Update Manual dulu.", "warning")
        return redirect(url_for('home_blueprint.index'))
    session['show_scan_run'] = run.id
    return redirect(url_for('home_blueprint.index'))

# 1c. PERBAIKI KAMERA CTP LANGSUNG DARI POPUP HASIL SCAN
FIX_DETAILS = {'Online': "Online (Foto manual)", 'Offline': "Offline (Revisi manual)",
               'Available': "Channel Kosong (Revisi manual)"}

@blueprint.route('/scan_result/fix_camera', methods=['POST'])
@login_required
def scan_fix_camera():
    """Revisi 1 kamera dari popup Hasil Scan (balas JSON).
    - Online wajib upload foto -> disimpan seperti foto hasil scan, jadi Update To Report
      memperlakukannya sebagai Online dengan foto (bukan CTP).
    - Kalau Update To Report hari ini SUDAH dijalankan, kolom Weekly hari ini ikut direvisi
      (aturan tiket sama dengan revisi di halaman Weekly)."""
    from apps.home.routes_report import revise_snapshot, MANUAL_STATUSES
    from apps.home.ai_report import save_snapshot_and_get_path
    from apps.home.report_status import status_label

    if not current_user.can_edit_daily_report:
        return jsonify({'ok': False, 'message': "Akses ditolak."}), 403

    cam = Camera.query.get(request.form.get('camera_id', type=int) or 0)
    new_status = request.form.get('new_status')
    if not cam or new_status not in MANUAL_STATUSES:
        return jsonify({'ok': False, 'message': "Data tidak valid."}), 400
    file = request.files.get('screenshot_file')
    has_file = bool(file and file.filename)
    if new_status == 'Online' and not has_file:
        return jsonify({'ok': False, 'message': "Untuk Online, upload foto kameranya dulu (tanpa foto tetap dihitung CTP)."}), 400

    try:
        ds = DeviceStatus.query.filter_by(camera_id=cam.id).first()
        if not ds:
            ds = DeviceStatus(camera_id=cam.id)
            db.session.add(ds)
        ds.status = new_status
        ds.details = FIX_DETAILS[new_status]
        ds.screenshot_path = None
        if new_status == 'Online':
            nvr = Camera.query.filter_by(ip_address=cam.ip_address, channel=0).first()
            path = save_snapshot_and_get_path(cam, file.read(), {cam.ip_address: nvr} if nvr else {})
            if not path:
                db.session.rollback()
                return jsonify({'ok': False, 'message': "Foto gagal disimpan."}), 500
            ds.screenshot_path = path
            ds.last_online = datetime.now()

        # Sudah Update To Report hari ini? -> kolom Weekly hari ini ikut direvisi (foto dipakai otomatis)
        snap, created, closed = revise_snapshot(cam.id, new_status, date.today())

        label = "Online (foto manual)" if new_status == 'Online' else status_label(new_status)
        run = ScanRun.query.get(request.form.get('run_id', type=int) or 0)
        if run:
            data = json.loads(run.result_json or "{}")
            for key in ('ctp', 'nodata'):
                for x in data.get(key, []):
                    if x.get('camera_id') == cam.id:
                        x['fixed'] = label
            run.result_json = json.dumps(data, ensure_ascii=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'message': f"Gagal menyimpan: {e}"}), 500

    msg = f"{cam.name}: {label}."
    msg += " Kolom Weekly hari ini ikut direvisi." if snap else " Akan masuk Weekly saat Update To Report."
    if created: msg += " Ticket dibuat."
    if closed: msg += " Ticket ditutup."
    return jsonify({'ok': True, 'message': msg, 'label': label, 'camera_id': cam.id})


# 2. HDD ROUTES
@blueprint.route('/hdd_report/switch_set', methods=['POST'])
@login_required
def switch_hdd_set():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    nvr_id = request.form.get('nvr_id')
    new_set_name = request.form.get('set_name')
    today = date.today()
    nvr = Camera.query.get(nvr_id)
    if nvr:
        nvr.current_hdd_set = new_set_name if new_set_name else None
        db.session.commit()
        histories = HDDStatusHistory.query.filter_by(camera_id=nvr.id, snapshot_date=today).all()
        if histories:
            for h in histories: h.hdd_set_at_time = nvr.current_hdd_set
            db.session.commit()
            flash(f"✅ Set diupdate untuk Data Minggu Ini (Latest).", "success")
        else:
            flash(f"✅ Set NVR diupdate. (Tekan Update to Report nanti untuk simpan history).", "info")
    return redirect(url_for('home_blueprint.hdd_report'))

@blueprint.route('/hdd_report/delete_set', methods=['POST'])
@login_required
def delete_hdd_set():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    nvr_id = request.form.get('nvr_id')
    today = date.today()
    nvr = Camera.query.get(nvr_id)
    if nvr:
        nvr.current_hdd_set = None 
        db.session.commit()
        histories = HDDStatusHistory.query.filter_by(camera_id=nvr.id, snapshot_date=today).all()
        for h in histories: h.hdd_set_at_time = None
        db.session.commit()
        flash("✅ Set dihapus dari NVR dan Report Hari Ini.", "success")
    return redirect(url_for('home_blueprint.hdd_report'))

@blueprint.route('/hdd_report/delete_snapshot', methods=['POST'])
@login_required
def delete_hdd_snapshot():
    if not current_user.can_manage_users:
        flash("❌ Akses Ditolak: Fitur ini hanya dapat diakses oleh Super Admin.", "danger")
        return redirect(url_for('home_blueprint.index'))
    date_str = request.form.get('snapshot_date')
    if not date_str: return redirect(url_for('home_blueprint.hdd_report'))
    try:
        snapshot_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        deleted_count = HDDStatusHistory.query.filter_by(snapshot_date=snapshot_date).delete()
        db.session.commit()
        flash(f"✅ Data snapshot HDD tanggal {date_str} berhasil dihapus.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal menghapus: {e}", "danger")
    return redirect(url_for('home_blueprint.hdd_report'))

@blueprint.route('/hdd_report')
@login_required
def hdd_report():
    if not current_user.can_view_weekly_hdd:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    # Urutan tanggal ascending: lama di kiri, baru di kanan
    from datetime import datetime, timedelta
    ninety_days_ago = datetime.now().date() - timedelta(days=90)
    from sqlalchemy import func
    
    raw_dates = db.session.query(HDDStatusHistory.snapshot_date)\
        .filter(func.date(HDDStatusHistory.snapshot_date) >= ninety_days_ago)\
        .distinct().all()
    date_headers = sorted([d[0] for d in raw_dates])

    nvrs = Camera.query.filter_by(device_type='NVR', channel=0).join(Site).order_by(Site.id, Camera.sort_order).all()
    sites = Site.query.all()
    grouped_data = build_hdd_blocks(nvrs, date_headers)

    return render_template('home/hdd_report.html', grouped_data=grouped_data, date_headers=date_headers, sites=sites, segment='hdd_report')

# EXPORT HDD EXCEL
def generate_hdd_excel(site_id=None):
    query = Camera.query.filter_by(device_type='NVR', channel=0).join(Site).order_by(Site.id, Camera.sort_order)
    if site_id: query = query.filter(Camera.site_id == site_id)
    nvrs = query.all()

    from datetime import datetime, timedelta
    ninety_days_ago = datetime.now().date() - timedelta(days=90)
    from sqlalchemy import func
    
    # Tanggal ascending (lama -> kiri), maksimal 3 bulan
    raw_dates = db.session.query(HDDStatusHistory.snapshot_date)\
        .filter(func.date(HDDStatusHistory.snapshot_date) >= ninety_days_ago)\
        .distinct().all()
    date_headers = sorted([d[0] for d in raw_dates])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "HDD Report"
    write_hdd_sheet(ws, build_hdd_blocks(nvrs, date_headers), date_headers, date_fmt="%d-%b-%Y", header_color="BDD7EE")
    return wb

@blueprint.route('/hdd_report/export_all')
@login_required
def export_hdd_all():
    if not current_user.can_view_weekly_hdd:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    wb = generate_hdd_excel(site_id=None)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    date_str = datetime.now().strftime('%d %m %Y')
    return send_file(out, download_name=f"LaporanHDD_All_{date_str}.xlsx", as_attachment=True)

@blueprint.route('/hdd_report/export_site', methods=['POST'])
@login_required
def export_hdd_site():
    if not current_user.can_view_weekly_hdd:
        flash("❌ Akses Ditolak: Anda tidak memiliki izin untuk melihat halaman Laporan.", "danger")
        return redirect(url_for('home_blueprint.index'))
    site_id = request.form.get('site_id')
    wb = generate_hdd_excel(site_id=site_id)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    site = Site.query.get(site_id)
    name = site.name.replace(" ", "_") if site else "Site"
    date_str = datetime.now().strftime('%d %m %Y')
    return send_file(out, download_name=f"LaporanHDD_{name}_{date_str}.xlsx", as_attachment=True)

@blueprint.route('/<template>')
@login_required
def route_template(template):
    try:
        if not template.endswith('.html'): template += '.html'
        return render_template("home/" + template, segment=template.replace('.html', ''))
    except TemplateNotFound: return render_template('home/page-404.html'), 404
    except Exception as e: return render_template('home/page-500.html'), 500

from . import routes_live_view
from . import routes_settings
from . import routes_report 
from . import routes_ai_settings
from . import routes_account
from . import routes_master_location
from apps.home.models import TicketNotification
from sqlalchemy import or_, and_
    
@blueprint.app_context_processor
def inject_ticket_count():
    try:
        # Cek dulu apakah ada user yang sedang login
        if current_user.is_authenticated:
            # Hitung tiket yang belum di-log (masih Open, atau Resolved tapi belum di-log)
            open_ticket_count = TicketNotification.query.filter_by(is_logged=False).count()
            return dict(open_ticket_count=open_ticket_count)
        return dict(open_ticket_count=0)
    except Exception:
        return dict(open_ticket_count=0)