# apps/home/report_status.py
# =========================================================
# STANDAR STATUS LAPORAN WEEKLY (Web + semua Export Excel)
# ---------------------------------------------------------
# Satu-satunya tempat untuk menentukan:
#   - label status yang ditampilkan
#   - warna status (web & Excel)
# Ubah di sini saja, jangan hardcode lagi di route / template.
#
# Aturan:
#   1. Semua status "tidak ada data" digabung jadi SATU label: "NO DATA",
#      warna abu-abu muda. Status ini TIDAK PERNAH diubah menjadi "Offline".
#      Mencakup 2 skenario:
#        - "Tidak Ditemukan" (disimpan di DB): channel minggu lalu ada, minggu ini
#          tidak ada di daftar NVR (mis. NVR 32ch -> 16ch, ch 17-32).
#        - Tidak ada snapshot sama sekali: channel belum ada / tidak di-scan pada
#          tanggal itu (mis. NVR 16ch -> 32ch, ch 17-32 di minggu-minggu sebelumnya).
#      Plus variasi lain: N/A, -, Unknown, Tidak Terdeteksi, Hilang, dll.
#   2. "Error" & "No Video" = kamera benar-benar mati -> ditampilkan "Offline".
# =========================================================
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

NOT_FOUND_LABEL = "NO DATA"

# Semua variasi status yang artinya "tidak ada data / channel tidak ada".
# Dibandingkan dalam huruf kecil.
_NOT_FOUND_RAW = {
    "", "-", "n/a", "na", "none", "null",
    "no data", "nodata",
    "unknown",
    "tidak ditemukan", "tidak ditemukan di nvr (channel mati)",
    "tidak terdeteksi", "tidak diketahui", "tidak diketemukan", "tidak diketemui",
    "hilang",
}

_OFFLINE_RAW = {"offline", "error", "no video"}

# NVR tidak bisa dicek (timeout / login ditolak / merk tidak didukung) -> NVR & semua kameranya
STATUS_UNCHECKABLE = "Tidak Bisa Dicek"          # nilai di database (maks 20 karakter)
UNCHECKABLE_LABEL = "NVR Tidak Bisa Dicek"       # label tampilan
CTP_LABEL = "Online (CTP)"                       # Online tapi snapshot gagal

# Warna (hex tanpa '#') — dipakai bersama oleh web & Excel
COLOR_ONLINE = "92D050"
COLOR_OFFLINE = "FF0000"
COLOR_AVAILABLE = "5B9BD5"
COLOR_NOT_FOUND = "D9D9D9"  # abu-abu muda
COLOR_UNCHECKABLE = "FFC000"  # oranye

_THIN = Side(style='thin')
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def is_not_found(status):
    """True jika status termasuk kelompok 'tidak ada data / tidak ditemukan'."""
    if status is None:
        return True
    return str(status).strip().lower() in _NOT_FOUND_RAW


def snapshot_display_status(snap):
    """Status snapshot untuk ditampilkan: 'Online' + CTP -> 'Online (CTP)'."""
    if snap is None:
        return None
    if snap.status == "Online" and getattr(snap, 'is_ctp', False):
        return CTP_LABEL
    return snap.status


def status_label(status):
    """Label standar untuk ditampilkan di web & Excel."""
    if is_not_found(status):
        return NOT_FOUND_LABEL
    s = str(status).strip()
    low = s.lower()
    if low in _OFFLINE_RAW:
        return "Offline"
    if low == "normal":
        return "Online"
    if low == CTP_LABEL.lower():
        return CTP_LABEL
    if low in (STATUS_UNCHECKABLE.lower(), UNCHECKABLE_LABEL.lower()):
        return UNCHECKABLE_LABEL
    return s


def status_css_class(status):
    """Nama class CSS untuk sel status di halaman web."""
    label = status_label(status)
    if label in ("Online", CTP_LABEL):
        return "status-online"
    if label == "Offline":
        return "status-offline"
    if "Available" in label:
        return "status-available"
    if label == UNCHECKABLE_LABEL:
        return "status-uncheckable"
    if label == NOT_FOUND_LABEL:
        return "status-nodata"
    return ""


def has_visual_evidence(status):
    """Screenshot hanya ditampilkan untuk status Online (bukan CTP)."""
    return status_label(status) == "Online"


def style_status_cell(cell):
    """Warnai sel status Excel berdasarkan nilai label yang sudah ditulis."""
    label = str(cell.value) if cell.value is not None else NOT_FOUND_LABEL
    if label in ("Online", CTP_LABEL):
        cell.fill = PatternFill(start_color=COLOR_ONLINE, end_color=COLOR_ONLINE, fill_type="solid")
        cell.font = Font(color="000000")
    elif label == "Offline":
        cell.fill = PatternFill(start_color=COLOR_OFFLINE, end_color=COLOR_OFFLINE, fill_type="solid")
        cell.font = Font(color="000000", bold=True)
    elif "Available" in label:
        cell.fill = PatternFill(start_color=COLOR_AVAILABLE, end_color=COLOR_AVAILABLE, fill_type="solid")
        cell.font = Font(color="000000")
    elif label == UNCHECKABLE_LABEL:
        cell.fill = PatternFill(start_color=COLOR_UNCHECKABLE, end_color=COLOR_UNCHECKABLE, fill_type="solid")
        cell.font = Font(color="000000", bold=True)
    elif label == NOT_FOUND_LABEL:
        cell.fill = PatternFill(start_color=COLOR_NOT_FOUND, end_color=COLOR_NOT_FOUND, fill_type="solid")
        cell.font = Font(color="000000")
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = _BORDER


def write_status_cell(ws, row, column, raw_status):
    """Tulis status mentah ke Excel dengan label & warna standar."""
    cell = ws.cell(row=row, column=column, value=status_label(raw_status))
    style_status_cell(cell)
    return cell
