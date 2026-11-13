import os
import re
import sys
import requests
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
from apps import db
from apps.home.device_api import device_url, UNV_STORAGE, HIK_STORAGE
from apps.home.models import Camera, HDDStatus, Site, DeviceStatus, WeeklyStatusSnapshot, HDDStatusHistory, TicketNotification
from datetime import datetime, date
import logging
from cryptography.fernet import Fernet
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import func

# Timeout config
DATA_TIMEOUT = 30

# =========================================================
# HASIL SCAN STORAGE (LEVEL NVR, BUKAN PER SLOT)
# =========================================================
SCAN_OK = 'ok'                    # Response 200 + ada disk
SCAN_EMPTY = 'empty'              # Response 200 tapi 0 disk -> KOSONG (bukan tiket)
SCAN_AUTH = 'auth'                # Response ada tapi ditolak (401/403)
SCAN_COMM = 'comm'                # Tidak ada response sama sekali (timeout / refused)
SCAN_UNSUPPORTED = 'unsupported'  # Response ada tapi bukan format storage yang dikenal
SCAN_BRAND = 'brand'              # Merk NVR bukan UNV / HIKVISION -> tidak dicek

# Alasan GAGAL CEK yang ditampilkan di halaman Hasil Scan
SCAN_FAIL_REASONS = {
    SCAN_COMM: "Tidak ada respons (timeout / koneksi ditolak)",
    SCAN_AUTH: "Login ditolak (HTTP 401/403) — cek username/password",
    SCAN_UNSUPPORTED: "Format respons tidak dikenal / endpoint tidak didukung",
    SCAN_BRAND: "Merk NVR tidak didukung (harus UNV / HIKVISION)",
}

# Kegagalan level NVR disimpan sebagai 1 baris HDDStatus dengan disk_no=0,
# supaya report menggambar 1 cell penuh (bukan memalsukan "Slot 1").
NVR_LEVEL_DISK_NO = 0
STATUS_TEST_CONN = "TEST CON/AUTH"      # Gagal cek -> ditampilkan sebagai "Error", tanpa tiket
STATUS_NO_DISK_ALL = "TIDAK ADA DISK"   # API membalas 0 disk -> kosong, tanpa tiket
STATUS_HDD_ERROR = "HDD ERROR"          # Label alternatif untuk 0 disk (tetap dikenali)
# 'Check Auth/Conn' = label alternatif gagal cek (tetap dikenali)
NVR_LEVEL_STATUSES = [STATUS_TEST_CONN, STATUS_NO_DISK_ALL, STATUS_HDD_ERROR, 'Check Auth/Conn']
CONN_FAIL_STATUSES = [STATUS_TEST_CONN, 'Check Auth/Conn']

NVR_ONLINE_STATUSES = ['Online', 'Available', 'Normal']

# ---------------------------------------------------------
# KLASIFIKASI STATUS HDD PER SLOT
#   Normal          -> sehat
#   No Disk         -> slot kosong (bukan masalah)
#   Error (x)       -> API JELAS bilang rusak -> boleh jadi tiket
#   Tidak Dikenal (x) -> status yang belum diketahui artinya -> TIDAK jadi tiket,
#                        ditampilkan apa adanya & dicatat di Hasil Scan untuk dipelajari
# Isi daftar ERROR dikonfirmasi dari respons asli perangkat.
# ---------------------------------------------------------
HIK_NORMAL = {'normal', 'ok', 'exist', 'online', 'mounted', 'formatted', 'idle'}
HIK_EMPTY = {'notexist'}
HIK_UNFORMATTED = {'unformatted'}      # Disk terpasang tapi belum diformat (lihat UNFORMATTED_AS)
UNFORMATTED_AS = "No Disk"             # Ganti ke "Error" bila disk belum diformat harus jadi tiket
HIK_ERROR = {'error', 'smartfailed', 'abnormal', 'offline', 'mismatch', 'fault', 'damaged', 'bad'}
UNV_NORMAL_CODES = {1, 3, 4}
UNV_ERROR_CODES = set()                # Belum ada kode error UNV yang terkonfirmasi -> isi dari hasil probe

HDD_OK_STATUSES = ['Normal', 'No Disk', 'Idle']
UNKNOWN_PREFIX = "Tidak Dikenal"


def is_hdd_error(status):
    """True HANYA jika API jelas melaporkan disk rusak."""
    return str(status or "").startswith("Error")


def is_hdd_unknown(status):
    s = str(status or "")
    return s.startswith(UNKNOWN_PREFIX) or s.startswith("Check (")  # 'Check (x)' = label alternatif status UNV

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

def decrypt_password(cipher_text):
    if not cipher_text: return ""
    try:
        with open(KEY_FILE, 'rb') as f:
            key = f.read()
        return Fernet(key).decrypt(cipher_text.encode()).decode()
    except:
        return cipher_text

def parse_size(size_raw):
    try:
        val = float(size_raw)
        if val <= 0: return "0.00 GB"
        if val > 1000 * 1024 * 1024 * 1024:
            gb = val / (1024**3)
        else:
            gb = val / 1024

        if gb > 1000: return f"{gb/1024:.2f} TB"
        else: return f"{gb:.2f} GB"
    except:
        return "0.00 GB"

def normalize_status(status_raw):
    """Status HDD Hikvision -> Normal / No Disk / Error (x) / Tidak Dikenal (x)."""
    raw = str(status_raw or "").strip()
    s = raw.lower()
    if s in HIK_NORMAL: return "Normal"
    if s in HIK_EMPTY: return "No Disk"
    if s in HIK_UNFORMATTED: return UNFORMATTED_AS if UNFORMATTED_AS != "Error" else f"Error ({raw})"
    if s in HIK_ERROR: return f"Error ({raw})"
    return f"{UNKNOWN_PREFIX} ({raw or 'kosong'})"

# =========================================================
# DETEKSI BRAND HDD (UNTUK KOLOM DISK INFO)
# =========================================================
# Nama merk yang tertulis langsung di field manufacturer/model
BRAND_KEYWORDS = [
    ("SEAGATE", "Seagate"), ("WESTERN DIGITAL", "WD"), ("WDC", "WD"),
    ("TOSHIBA", "Toshiba"), ("HGST", "HGST"), ("HITACHI", "HGST"),
    ("SAMSUNG", "Samsung"), ("HIKVISION", "Hikvision"), ("MAXTOR", "Maxtor"),
]
# Prefix nomor model. Urutan penting: prefix yang lebih spesifik harus di atas ("HDW" sebelum "HD")
MODEL_PREFIXES = [
    ("ST", "Seagate"), ("WD", "WD"),
    ("HUS", "HGST"), ("HUH", "HGST"), ("HUA", "HGST"), ("HDN", "HGST"), ("HDS", "HGST"), ("HTS", "HGST"),
    ("HDW", "Toshiba"), ("DT01", "Toshiba"), ("MG0", "Toshiba"), ("MD0", "Toshiba"), ("MN0", "Toshiba"), ("MQ0", "Toshiba"),
    ("HS-", "Hikvision"), ("HD", "Samsung"),
]

def detect_brand(manufacturer=None, model=None, serial=None):
    """Tebak merk HDD dari field API. Return None kalau tidak ada info sama sekali."""
    for text in (manufacturer, model):
        upper = str(text or "").strip().upper()
        if not upper: continue
        for keyword, brand in BRAND_KEYWORDS:
            if keyword in upper: return brand
        for prefix, brand in MODEL_PREFIXES:
            if upper.startswith(prefix): return brand

    # Serial WD biasanya diawali "WD-"
    if str(serial or "").strip().upper().startswith("WD-"): return "WD"

    # Manufacturer tidak dikenal tapi ada isinya -> tampilkan apa adanya
    if manufacturer and str(manufacturer).strip(): return str(manufacturer).strip()
    return None

# =========================================================
# FETCH STORAGE PER VENDOR -> (outcome, disks)
# =========================================================
def get_unv_disks(ip, user, password):
    url = device_url(ip, UNV_STORAGE)
    try:
        response = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=DATA_TIMEOUT)
    except requests.RequestException as e:
        logging.error(f"[UNV] Tidak ada response {ip}: {e}")
        return SCAN_COMM, []

    if response.status_code in (401, 403): return SCAN_AUTH, []
    if response.status_code != 200: return SCAN_UNSUPPORTED, []

    try:
        data = response.json()
    except ValueError:
        return SCAN_UNSUPPORTED, []
    resp_body = data.get("Response") if isinstance(data, dict) else None
    if not isinstance(resp_body, dict):
        return SCAN_UNSUPPORTED, []

    items = []
    inner_data = resp_body.get("Data")
    if not isinstance(inner_data, dict): inner_data = {}
    if "LocalHDDList" in inner_data: items = inner_data["LocalHDDList"]
    elif "ContainerDetailInfos" in inner_data: items = inner_data["ContainerDetailInfos"]
    if not isinstance(items, list): items = []

    disk_map = {}
    for item in items:
        if not isinstance(item, dict): continue
        try:
            disk_id = int(item.get("ID", item.get("id", 0)))
            cap_raw = float(item.get("TotalCapacity", item.get("Capacity", 0)))
            free_raw = float(item.get("RemainCapacity", item.get("FreeSpace", 0)))
            stat_code = int(item.get("Status", 0) or 0)
        except (TypeError, ValueError):
            continue

        if cap_raw > 0 and stat_code in UNV_NORMAL_CODES:
            status_str = "Normal"
        elif cap_raw <= 0:
            status_str = "No Disk"
        elif stat_code in UNV_ERROR_CODES:
            status_str = f"Error ({stat_code})"
        else:
            status_str = f"{UNKNOWN_PREFIX} ({stat_code})"

        prop_code = item.get("Property", 0)
        prop_str = "R/W" if str(prop_code) != "1" else "R/O"
        serial = item.get("SerialNo") or item.get("SerialNumber") or "-"
        brand = detect_brand(item.get("Manufacturer"), item.get("Model"), serial)

        disk_map[disk_id] = {'status': status_str, 'capacity': parse_size(cap_raw), 'free': parse_size(free_raw), 'property': prop_str, 'sn': serial, 'brand': brand}

    if not disk_map: return SCAN_EMPTY, []
    return SCAN_OK, [{'disk_no': i, **disk_map[i]} for i in sorted(disk_map.keys())]

def get_hik_disks(ip, user, password):
    url = device_url(ip, HIK_STORAGE)
    try:
        response = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=DATA_TIMEOUT)
    except requests.RequestException as e:
        logging.error(f"[HIK] Tidak ada response {ip}: {e}")
        return SCAN_COMM, []

    if response.status_code in (401, 403): return SCAN_AUTH, []
    if response.status_code != 200: return SCAN_UNSUPPORTED, []

    try:
        # Hapus namespace (ver10/ver20) agar find() tidak bergantung versi firmware
        root = ET.fromstring(re.sub(r'\sxmlns="[^"]+"', '', response.text))
    except ET.ParseError:
        return SCAN_UNSUPPORTED, []
    if root.tag != 'hddList' and root.find('.//hdd') is None:
        return SCAN_UNSUPPORTED, [] # Contoh: <ResponseStatus> notSupport

    def text_of(node, tag):
        child = node.find(tag)
        return child.text.strip() if child is not None and child.text else None

    disk_map = {}
    for hdd in root.findall('.//hdd'):
        try:
            hdd_id = int(text_of(hdd, 'id'))
            cap = float(text_of(hdd, 'capacity') or 0)
            free = float(text_of(hdd, 'freeSpace') or 0)
        except (TypeError, ValueError):
            continue

        final_status = normalize_status(text_of(hdd, 'status') or "Unknown")
        serial = text_of(hdd, 'serialNumber') or "-"
        brand = detect_brand(text_of(hdd, 'manufacturer'), text_of(hdd, 'model'), serial)

        disk_map[hdd_id] = {'status': final_status, 'capacity': parse_size(cap), 'free': parse_size(free), 'property': text_of(hdd, 'property') or "R/W", 'sn': serial, 'brand': brand}

    if not disk_map: return SCAN_EMPTY, []
    return SCAN_OK, [{'disk_no': i, **disk_map[i]} for i in sorted(disk_map.keys())]

def scan_nvr_storage(nvr_obj):
    """Endpoint storage SESUAI MERK NVR (ketat). Merk selain UNV/HIKVISION tidak dicek."""
    model = (nvr_obj.model or "").upper()
    if "HIKVISION" in model: fetch = get_hik_disks
    elif "UNV" in model: fetch = get_unv_disks
    else: return SCAN_BRAND, []
    return fetch(nvr_obj.ip_address, nvr_obj.username, decrypt_password(nvr_obj.password))

def run_hdd_scan(target_site_id=None, target_nvr_id=None):
    """Scan HDD realtime. TIDAK membuat / menutup ticket (tiket hanya saat Update to Report).

    Return dict untuk halaman Hasil Scan:
      'gagal'   : [{site, nvr, ip, api, reason}]  -> API storage tidak bisa dibaca
      'unknown' : [{site, nvr, slot, status}]      -> status slot yang belum dikenal
      'ok_count': jumlah NVR yang HDD-nya terbaca
    """
    logging.info("🚀 Memulai Scan HDD (Realtime)...")

    query = db.session.query(Camera).join(Site).filter(
        Camera.device_type == 'NVR',
        Camera.channel == 0,
        Camera.is_active == True,
        Site.ai_report_enabled == True
    )
    if target_nvr_id:
        query = query.filter(Camera.id == target_nvr_id)
    elif target_site_id:
        query = query.filter(Camera.site_id == target_site_id)
    nvrs = query.all()

    result = {'gagal': [], 'unknown': [], 'ok_count': 0}

    scan_results = {}
    def fetch_hdd(nvr_obj):
        # Error tak terduga di 1 NVR jangan sampai membatalkan scan NVR lainnya
        try:
            return nvr_obj.id, scan_nvr_storage(nvr_obj)
        except Exception as e:
            logging.exception(f"[HDD] Gagal memproses response storage {nvr_obj.name} ({nvr_obj.ip_address}): {e}")
            return nvr_obj.id, (SCAN_UNSUPPORTED, [])

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(fetch_hdd, nvr) for nvr in nvrs]
        for future in as_completed(futures):
            nvr_id, res = future.result()
            scan_results[nvr_id] = res

    for nvr in nvrs:
        outcome, disk_list = scan_results.get(nvr.id, (SCAN_COMM, []))
        site_name = nvr.site.name if nvr.site else "-"

        # Data realtime NVR ini diganti total dengan hasil scan barusan
        HDDStatus.query.filter_by(camera_id=nvr.id).delete(synchronize_session=False)

        if outcome == SCAN_OK:
            result['ok_count'] += 1
            for disk in disk_list:
                db.session.add(HDDStatus(
                    camera_id=nvr.id, disk_no=disk['disk_no'], status=disk['status'],
                    capacity=disk.get('capacity', '-'), free_space=disk.get('free', '-'),
                    property=disk.get('property', '-'), serial_number=disk.get('sn', '-'),
                    brand=disk.get('brand')
                ))
                if is_hdd_unknown(disk['status']):
                    result['unknown'].append({'site': site_name, 'nvr': nvr.name, 'slot': disk['disk_no'], 'status': disk['status']})
        elif outcome == SCAN_EMPTY:
            result['ok_count'] += 1
            db.session.add(HDDStatus(camera_id=nvr.id, disk_no=NVR_LEVEL_DISK_NO, status=STATUS_NO_DISK_ALL))
        else:
            db.session.add(HDDStatus(camera_id=nvr.id, disk_no=NVR_LEVEL_DISK_NO, status=STATUS_TEST_CONN))
            reason = SCAN_FAIL_REASONS.get(outcome, outcome)
            if outcome == SCAN_BRAND:
                reason += f" — tercatat: '{nvr.model or '-'}'"
            result['gagal'].append({'site': site_name, 'nvr': nvr.name, 'ip': nvr.ip_address, 'api': 'HDD (storage)', 'reason': reason})
            logging.warning(f"   ⚠️ {nvr.name}: storage scan '{outcome}' -> {STATUS_TEST_CONN}")

        db.session.commit()
        logging.info(f"   ✅ Data Realtime HDD Tersimpan untuk {nvr.name} ({outcome}, {len(disk_list)} disk)")

    return result
