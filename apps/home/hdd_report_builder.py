# apps/home/hdd_report_builder.py
# Penyusun data HDD Report yang dipakai BERSAMA oleh halaman web (hdd_report.html),
# dan Export Excel (All / per Site).
# Satu sumber logika = kedua format selalu identik.
#
# Format per NVR (1 blok):
#   Site | NVR | IP | Disk Info (brand unik hari ini) | [tanggal 1] [tanggal 2] ...
# Tiap kolom tanggal dibagi rata sesuai jumlah slot yang dibalas API di tanggal itu.
# Tinggi blok = jumlah slot terbanyak di rentang tanggal yang tampil.
import math
from datetime import datetime

from openpyxl.styles import PatternFill, Alignment, Font, Border, Side

from apps.home.models import HDDStatus, HDDStatusHistory
from apps.home.hdd_checker import NVR_LEVEL_STATUSES, STATUS_HDD_ERROR, STATUS_TEST_CONN, STATUS_NO_DISK_ALL, is_hdd_unknown

# Nilai property HDD (R/W dll) di hdd_set_at_time bukan label Set -> tidak ditampilkan
LEGACY_SET_VALUES = ['R/W', 'R/O', 'RW', 'RO', '-']

EXCEL_SLOT_HEIGHT = 60          # Tinggi (point) 1 slot di Excel
MAX_EXCEL_ROWS_PER_BLOCK = 24   # Batas baris Excel per blok NVR (lihat _excel_rows_for_block)

# unknown = status yang belum dikenal (tanpa tiket), empty = API membalas 0 disk, conn = gagal cek (oranye)
SLOT_FILLS = {'ok': "92D050", 'nodisk': "D9D9D9", 'unknown': "D9D9D9", 'error': "FF0000"}
FAIL_FILLS = {'empty': "D9D9D9", 'conn': "FFC000"}
THIN_BORDER = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)

def _calc_used_space(cap_str, free_str):
    if not cap_str or not free_str or cap_str == "-" or free_str == "-":
        return "N/A Used"
    def to_gb(s):
        s = str(s).strip().upper()
        if "TB" in s:
            try: return float(s.replace("TB", "").strip()) * 1024
            except: return 0.0
        elif "GB" in s:
            try: return float(s.replace("GB", "").strip())
            except: return 0.0
        else:
            try: return float(s)
            except: return 0.0

    cap_gb = to_gb(cap_str)
    free_gb = to_gb(free_str)
    used_gb = cap_gb - free_gb
    if used_gb < 0: used_gb = 0.0

    if "TB" in str(cap_str).upper():
        return f"{used_gb/1024:.2f} TB"
    else:
        return f"{used_gb:.2f} GB"

def _as_date(value):
    return value.date() if isinstance(value, datetime) else value

def _slot_view(hist, slot_no):
    s = (hist.status or "").lower()
    if 'normal' in s: label, level = "OK", 'ok'
    elif 'no disk' in s: label, level = "No Disk", 'nodisk'
    elif is_hdd_unknown(hist.status): label, level = hist.status, 'unknown'
    else: label, level = hist.status, 'error'

    used = _calc_used_space(hist.capacity_at_time, hist.free_space_at_time)
    return {
        'slot_no': slot_no, 'label': label, 'level': level,
        'total': hist.capacity_at_time or "-",
        'used': used if used != "N/A Used" else "-",
    }

def _date_cell(rows):
    """rows = semua HDDStatusHistory milik 1 NVR pada 1 tanggal."""
    if not rows: return {'kind': 'none'}

    hdd_set = next((r.hdd_set_at_time for r in rows if r.hdd_set_at_time and r.hdd_set_at_time not in LEGACY_SET_VALUES), None)

    fail = next((r for r in rows if r.status in NVR_LEVEL_STATUSES), None)
    if fail:
        is_empty = fail.status in (STATUS_NO_DISK_ALL, STATUS_HDD_ERROR)  # HDD ERROR = label alternatif 0 disk
        return {
            'kind': 'fail', 'set': hdd_set,
            'label': STATUS_NO_DISK_ALL if is_empty else STATUS_TEST_CONN,
            'level': 'empty' if is_empty else 'conn',
        }

    # Nomor slot = nomor urut 1, 2, 3, ... (bukan ID mentah dari API)
    slots = [_slot_view(r, i) for i, r in enumerate(sorted(rows, key=lambda r: r.disk_no), 1)]
    return {'kind': 'slots', 'slots': slots, 'set': hdd_set}

def _disk_info(current_disks):
    """Gabungan brand unik dari scan TERAKHIR. Scan terakhir gagal / tidak ada brand -> '-'."""
    if not current_disks or any(d.status in NVR_LEVEL_STATUSES for d in current_disks):
        return "-"
    brands = []
    for d in sorted(current_disks, key=lambda d: d.disk_no):
        if d.brand and d.brand not in brands: brands.append(d.brand)
    return ", ".join(brands) if brands else "-"

def build_hdd_blocks(nvrs, date_headers):
    nvr_ids = [n.id for n in nvrs]
    if not nvr_ids: return []

    histories = HDDStatusHistory.query.filter(
        HDDStatusHistory.camera_id.in_(nvr_ids),
        HDDStatusHistory.snapshot_date.in_(date_headers)
    ).all() if date_headers else []
    rows_by_key = {}
    for h in histories:
        rows_by_key.setdefault((h.camera_id, _as_date(h.snapshot_date)), []).append(h)

    current_map = {}
    for d in HDDStatus.query.filter(HDDStatus.camera_id.in_(nvr_ids)).all():
        current_map.setdefault(d.camera_id, []).append(d)

    blocks = []
    for nvr in nvrs:
        cells = [_date_cell(rows_by_key.get((nvr.id, _as_date(d)), [])) for d in date_headers]
        slot_counts = [len(c['slots']) for c in cells if c['kind'] == 'slots']
        blocks.append({
            'site': nvr.site.name if nvr.site else "-",
            'nvr': nvr.name, 'nvr_id': nvr.id, 'ip': nvr.ip_address,
            'disk_info': _disk_info(current_map.get(nvr.id, [])),
            'cells': cells,
            'slot_counts': slot_counts,
            'height': max(slot_counts, default=1),
        })
    return blocks

# =========================================================
# EXCEL WRITER
# =========================================================
def _excel_rows_for_block(block):
    """Excel hanya bisa merge baris bulat. Pakai KPK jumlah slot agar semua tanggal terbagi rata
    (contoh 4 & 2 slot -> 4 baris, 4 & 3 slot -> 12 baris). Kalau KPK terlalu besar,
    pakai slot terbanyak dan pembagiannya sedikit tidak rata."""
    rows = math.lcm(*set(block['slot_counts'])) if block['slot_counts'] else 1
    return rows if rows <= MAX_EXCEL_ROWS_PER_BLOCK else block['height']

def _split_rows(total, parts):
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]

def _write_merged(ws, start_row, end_row, col, value, fill_color=None, bold=False):
    if end_row > start_row:
        ws.merge_cells(start_row=start_row, start_column=col, end_row=end_row, end_column=col)
    for r in range(start_row, end_row + 1):
        ws.cell(row=r, column=col).border = THIN_BORDER
    cell = ws.cell(row=start_row, column=col, value=value)
    cell.alignment = CENTER_WRAP
    if fill_color:
        cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
    if bold:
        cell.font = Font(bold=True)

def write_hdd_sheet(ws, blocks, date_headers, date_fmt, header_color):
    headers = ["Site", "NVR", "IP", "Disk Info"] + [d.strftime(date_fmt) for d in date_headers]
    header_fill = PatternFill(start_color=header_color, end_color=header_color, fill_type="solid")
    for col, val in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=val)
        cell.font = Font(bold=True, color="000000")
        cell.fill = header_fill
        cell.alignment = CENTER_WRAP
        cell.border = THIN_BORDER

    row = 2
    for block in blocks:
        n_rows = _excel_rows_for_block(block)
        end_row = row + n_rows - 1
        for r in range(row, end_row + 1):
            ws.row_dimensions[r].height = EXCEL_SLOT_HEIGHT * block['height'] / n_rows

        for col, val in enumerate([block['site'], block['nvr'], block['ip'], block['disk_info']], 1):
            _write_merged(ws, row, end_row, col, val, bold=(col == 1))

        for i, cell_data in enumerate(block['cells']):
            col = 5 + i
            if cell_data['kind'] == 'slots':
                r = row
                spans = _split_rows(n_rows, len(cell_data['slots']))
                for idx, (slot, span) in enumerate(zip(cell_data['slots'], spans)):
                    text = f"Slot: {slot['slot_no']}\n{slot['label']}\nTotal: {slot['total']}\nUsed: {slot['used']}"
                    if idx == 0 and cell_data['set']:
                        text += f"\nSet: {cell_data['set']}"
                    _write_merged(ws, r, r + span - 1, col, text, fill_color=SLOT_FILLS[slot['level']], bold=(slot['level'] == 'error'))
                    r += span
            elif cell_data['kind'] == 'fail':
                text = cell_data['label'] + (f"\nSet: {cell_data['set']}" if cell_data.get('set') else "")
                _write_merged(ws, row, end_row, col, text, fill_color=FAIL_FILLS[cell_data['level']], bold=True)
            else:
                _write_merged(ws, row, end_row, col, "-")

        row = end_row + 1

    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['B'].width = 22
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 25
    for i in range(len(date_headers)):
        ws.column_dimensions[ws.cell(row=1, column=5 + i).column_letter].width = 18
    ws.freeze_panes = "E2" # Bekukan 4 kolom kiri + header, sama seperti versi web
