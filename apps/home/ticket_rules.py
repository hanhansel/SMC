# apps/home/ticket_rules.py
# =========================================================
# ATURAN TICKET (satu-satunya tempat tiket dibuat / ditutup otomatis)
# ---------------------------------------------------------
# Dipanggil saat UPDATE TO REPORT dan saat REVISI MANUAL di halaman Weekly.
# Scan (Cek Status) TIDAK PERNAH membuat / menutup tiket.
#
# KAMERA
#   Buat tiket  : status hari ini Offline DAN kolom report sebelumnya punya status NYATA
#                 yang bukan Offline (Online / Online CTP / Available).
#                 TIDAK ada tiket kalau sebelumnya Unknown / NO DATA / Tidak Ditemukan / belum
#                 pernah ada data (kamera baru, pertama kali diambil datanya).
#                 "Kolom sebelumnya" = tanggal report terakhir sebelum hari ini yang BISA DICEK
#                 (kolom "Tidak Bisa Dicek" dilewati).
#   Tutup tiket : status hari ini Online DENGAN foto (bukan CTP).
#                 CTP, Available, NO DATA, Tidak Bisa Dicek -> tidak menutup apa pun.
# NVR
#   Tidak pernah dibuatkan tiket otomatis (NVR Tidak Bisa Dicek -> tiket manual).
#   Tiket NVR (bukan HDD) ditutup otomatis saat NVR kembali Online.
# HDD (per NVR)
#   Buat tiket  : ada slot berstatus Error (API jelas bilang rusak) DAN kolom HDD sebelumnya
#                 (yang bisa dicek) ADA dan tidak ada Error. Belum ada data HDD sebelumnya -> tanpa tiket.
#   Tutup tiket : HDD terbaca dan semua slot Normal / No Disk.
#   TEST CON/AUTH, 0 disk, status Tidak Dikenal -> tidak membuat / menutup tiket.
# =========================================================
from datetime import datetime, date

from sqlalchemy import func

from apps import db
from apps.home.models import Camera, WeeklyStatusSnapshot, HDDStatusHistory, TicketNotification
from apps.home.hdd_checker import is_hdd_error, HDD_OK_STATUSES, CONN_FAIL_STATUSES
from apps.home.report_status import is_not_found

STATUS_UNCHECKABLE = "Tidak Bisa Dicek"
CAM_ERROR = {"Offline", "No Video", "Error"}  # 'No Video' / 'Error' = label alternatif untuk Offline
HDD_TICKET = 'HDD Error'


def _resolve(ticket):
    ticket.status = 'Resolved'
    ticket.resolved_at = datetime.now()


def _open_tickets(camera_ids):
    rows = TicketNotification.query.filter(
        TicketNotification.camera_id.in_(camera_ids), TicketNotification.status == 'Open'
    ).all() if camera_ids else []
    out = {}
    for t in rows:
        out.setdefault(t.camera_id, []).append(t)
    return out


def evaluate_camera_tickets(today_snaps, today=None):
    """today_snaps: WeeklyStatusSnapshot hari ini (sudah di-add ke session). Return (dibuat, ditutup)."""
    today = today or date.today()
    snaps = [s for s in today_snaps if s is not None and s.camera_id]
    cam_ids = list({s.camera_id for s in snaps})
    if not cam_ids:
        return 0, 0
    db.session.flush()

    cams = {c.id: c for c in Camera.query.filter(Camera.id.in_(cam_ids)).all()}

    prev_map = {}
    for r in WeeklyStatusSnapshot.query.filter(
        WeeklyStatusSnapshot.camera_id.in_(cam_ids),
        func.date(WeeklyStatusSnapshot.snapshot_date) < today,
        WeeklyStatusSnapshot.status != STATUS_UNCHECKABLE,
    ).order_by(WeeklyStatusSnapshot.snapshot_date.desc()).all():
        prev_map.setdefault(r.camera_id, r)

    open_map = _open_tickets(cam_ids)
    created = resolved = 0

    for s in snaps:
        cam = cams.get(s.camera_id)
        if not cam:
            continue
        open_non_hdd = [t for t in open_map.get(cam.id, []) if t.issue_type != HDD_TICKET]

        if cam.channel == 0 and cam.device_type == 'NVR':
            if s.status == 'Online':
                for t in open_non_hdd:
                    _resolve(t); resolved += 1
            continue

        if s.status in CAM_ERROR:
            prev = prev_map.get(cam.id)
            # Sebelumnya harus status nyata & bukan Offline. Unknown / NO DATA / belum ada data -> tanpa tiket.
            prev_was_ok = prev is not None and not is_not_found(prev.status) and prev.status not in CAM_ERROR
            if prev_was_ok and not open_non_hdd:
                detail = cam.status_record.details if cam.status_record and cam.status_record.details else "Kamera Offline"
                t = TicketNotification(camera_id=cam.id, status='Open', issue_type='Camera Issue', masalah=detail)
                db.session.add(t)
                open_map.setdefault(cam.id, []).append(t)
                created += 1
        elif s.status == 'Online' and not s.is_ctp:
            for t in open_non_hdd:
                _resolve(t); resolved += 1

    return created, resolved


def _hdd_rows_by_date(nvr_ids, today):
    """{nvr_id: {date: [status,...]}} untuk hari ini dan sebelumnya."""
    out = {}
    for h in HDDStatusHistory.query.filter(
        HDDStatusHistory.camera_id.in_(nvr_ids), HDDStatusHistory.snapshot_date <= today
    ).all():
        d = h.snapshot_date.date() if isinstance(h.snapshot_date, datetime) else h.snapshot_date
        out.setdefault(h.camera_id, {}).setdefault(d, []).append(h)
    return out


def _is_conn_fail(rows):
    return any(r.status in CONN_FAIL_STATUSES for r in rows)


def evaluate_hdd_tickets(nvr_ids, today=None):
    """Evaluasi tiket HDD untuk NVR yang punya data HDD hari ini. Return (dibuat, ditutup)."""
    today = today or date.today()
    nvr_ids = list(nvr_ids or [])
    if not nvr_ids:
        return 0, 0
    db.session.flush()

    by_nvr = _hdd_rows_by_date(nvr_ids, today)
    open_map = _open_tickets(nvr_ids)
    created = resolved = 0

    for nvr_id in nvr_ids:
        dates = by_nvr.get(nvr_id, {})
        today_rows = dates.get(today)
        if not today_rows or _is_conn_fail(today_rows):
            continue  # Tidak ada data / gagal cek -> tidak menyentuh tiket

        statuses = [r.status for r in today_rows]
        now_error = any(is_hdd_error(s) for s in statuses)
        open_hdd = [t for t in open_map.get(nvr_id, []) if t.issue_type == HDD_TICKET]

        if now_error:
            prev_date = next((d for d in sorted(dates, reverse=True)
                              if d < today and not _is_conn_fail(dates[d])), None)
            # Harus ada data HDD sebelumnya yang terbaca & tanpa Error (data pertama -> tanpa tiket)
            prev_ok = prev_date is not None and not any(is_hdd_error(r.status) for r in dates[prev_date])
            if prev_ok and not open_hdd:
                bad = ", ".join(f"Slot {r.disk_no}: {r.status}" for r in sorted(today_rows, key=lambda r: r.disk_no) if is_hdd_error(r.status))
                db.session.add(TicketNotification(camera_id=nvr_id, status='Open', issue_type=HDD_TICKET, masalah=f"HDD Error — {bad}"))
                created += 1
        elif all(s in HDD_OK_STATUSES for s in statuses):
            for t in open_hdd:
                _resolve(t); resolved += 1

    return created, resolved
