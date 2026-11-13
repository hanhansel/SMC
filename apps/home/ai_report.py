# -*- encoding: utf-8 -*-
# apps/home/ai_report.py
from apps import db
from apps.home.models import Camera, DeviceStatus, Site, TicketNotification
from datetime import datetime
from flask import current_app
import os
import sys
import logging
import requests
import re
import xml.etree.ElementTree as ET
from requests.auth import HTTPDigestAuth
from apps.home.device_api import (device_url, UNV_CHANNELS, UNV_SNAPSHOT_PREVIEW, UNV_SNAPSHOT,
                                  HIK_CHANNELS, HIK_SNAPSHOT)
from cryptography.fernet import Fernet
from typing import Tuple, Optional
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Path file log (folder aplikasi)
def get_base_path_log():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

LOG_PATH = os.path.join(get_base_path_log(), "ai_report.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Lokasi kunci enkripsi password NVR (folder system_data)
def get_base_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
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

# ---------------------------------------------------------
# 1. DAFTAR CHANNEL PER MERK -> (hasil, alasan_gagal)
#   hasil  : {ch_id: {'status': Online/Offline/Available, 'ip': .., 'name': ..}}
#   alasan : None jika berhasil, teks alasan GAGAL CEK jika tidak
# ---------------------------------------------------------
REASON_NO_RESPONSE = "Tidak ada respons (timeout / koneksi ditolak)"
REASON_AUTH = "Login ditolak (HTTP 401/403) — cek username/password"
REASON_FORMAT = "Format respons tidak dikenal"
REASON_EMPTY = "NVR menjawab, tapi daftar channel kosong"
REASON_BRAND = "Merk NVR tidak didukung (harus UNV / HIKVISION)"


def _http_reason(status_code):
    if status_code in (401, 403):
        return REASON_AUTH
    return f"HTTP {status_code} — endpoint tidak didukung?"


def fetch_unv_channels(ip_address, username, password):
    if not ip_address: return {}, "IP kosong"
    url = device_url(ip_address, UNV_CHANNELS)
    real_pass = decrypt_password(password)
    try:
        response = requests.get(url, timeout=30, auth=HTTPDigestAuth(username, real_pass))
    except Exception as e:
        logger.warning(f"UNV API Error [{ip_address}]: {e}")
        return {}, REASON_NO_RESPONSE
    if response.status_code != 200:
        return {}, _http_reason(response.status_code)
    try:
        detail_infos = response.json().get('Response', {}).get('Data', {}).get('DetailInfos', [])
    except Exception:
        return {}, REASON_FORMAT
    result = {}
    for ch_info in detail_infos or []:
        raw_status = ch_info.get('Status')
        if raw_status == 1: mapped_status = "Online"
        elif raw_status == 2: mapped_status = "Available"
        else: mapped_status = "Offline"
        result[ch_info.get('ID')] = {
            'status': mapped_status,
            'ip': (ch_info.get('AddressInfo') or {}).get('Address'),
            'name': ch_info.get('Name'),
        }
    return (result, None) if result else ({}, REASON_EMPTY)


def fetch_hik_channels(ip_address, username, password):
    if not ip_address: return {}, "IP kosong"
    url = device_url(ip_address, HIK_CHANNELS)
    real_pass = decrypt_password(password)
    reason = None
    try:
        response = requests.get(url, timeout=30, auth=HTTPDigestAuth(username, real_pass))
    except Exception as e:
        logger.warning(f"Hikvision API Error [{ip_address}]: {e}")
        reason = REASON_NO_RESPONSE
    else:
        if response.status_code != 200:
            reason = _http_reason(response.status_code)
    if reason:
        logger.error(f"Hikvision API Error [{ip_address}]: {reason}")
        return {}, reason
    try:
        # Hapus SEMUA XML Namespace agar find() tidak bergantung versi firmware
        root = ET.fromstring(re.sub(r'\sxmlns="[^"]+"', '', response.text))
    except ET.ParseError:
        return {}, REASON_FORMAT
    result = {}
    for ch in root.findall('.//VideoInputChannel'):
        ch_id_elem, res_elem = ch.find('id'), ch.find('resDesc')
        if ch_id_elem is None or res_elem is None:
            continue
        ch_id = int(ch_id_elem.text)
        res_desc = res_elem.text.upper() if res_elem.text else "UNKNOWN"
        name_elem = ch.find('name')
        ch_name = name_elem.text if (name_elem is not None and name_elem.text) else f"Channel {ch_id}"
        # Hikvision 'NO VIDEO' = kamera tidak mengirim video = Offline
        status = 'Offline' if res_desc in ("NO VIDEO", "UNSUPPORTED") else 'Online'
        result[ch_id] = {'status': status, 'ip': None, 'name': ch_name}
    return (result, None) if result else ({}, REASON_EMPTY)


def fetch_channels_by_brand(nvr_obj):
    """Pilih API SESUAI MERK NVR (ketat). Merk lain -> tidak dicek."""
    model = (nvr_obj.model or "").upper()
    if "UNV" in model:
        return fetch_unv_channels(nvr_obj.ip_address, nvr_obj.username, nvr_obj.password)
    if "HIKVISION" in model:
        return fetch_hik_channels(nvr_obj.ip_address, nvr_obj.username, nvr_obj.password)
    return {}, f"{REASON_BRAND} — tercatat: '{nvr_obj.model or '-'}'"


# Versi singkat: hanya mengembalikan daftar channel (tanpa alasan gagal)
def get_unv_batch_channel_info(ip_address, username, password):
    return fetch_unv_channels(ip_address, username, password)[0]

def get_hikvision_batch_channel_info(ip_address, username, password):
    return fetch_hik_channels(ip_address, username, password)[0]

# ---------------------------------------------------------
# AMBIL SNAPSHOT (MURNI BYTES, TANPA OPENCV)
# ---------------------------------------------------------
def capture_snapshot(camera: Camera) -> Tuple[bool, Optional[bytes], str]:
    upper_model = (camera.model or "").upper()
    channel = getattr(camera, 'channel', 1) or 1
    urls = []
    
    if "UNV" in upper_model: 
        # Urutan penting: URL pertama dicoba dulu,
        urls.append(device_url(camera.ip_address, UNV_SNAPSHOT_PREVIEW, channel=channel))
        # jika gagal lanjut ke URL kedua
        urls.append(device_url(camera.ip_address, UNV_SNAPSHOT, channel=channel))
    elif "HIKVISION" in upper_model:
        c_str = f"{channel}01" if channel > 0 else "0"
        urls.append(device_url(camera.ip_address, HIK_SNAPSHOT, stream=c_str))
    else:
        urls.append(device_url(camera.ip_address, UNV_SNAPSHOT, channel=channel))
    
    real_pass = decrypt_password(camera.password)
    auth = HTTPDigestAuth(camera.username, real_pass)

    for url in urls:
        try:
            resp = requests.get(url, timeout=10, stream=True, auth=auth)
            # Gambar minimal 1KB agar terhindar dari file kosong/corrupt
            if resp.status_code == 200 and resp.content and len(resp.content) > 1024:
                return True, resp.content, "Snapshot OK."
        except Exception:
            continue
    return False, None, "Snapshot Gagal."

# ---------------------------------------------------------
# SIMPAN SNAPSHOT (Struktur Folder: SITE/NVR/DATE)
# ---------------------------------------------------------
def save_snapshot_and_get_path(camera: Camera, frame_bytes: bytes, nvr_dict: dict) -> Optional[str]:
    if not frame_bytes: return None
    try:
        upload_folder = current_app.config['UPLOAD_FOLDER'] 
        date_only = datetime.now().strftime('%Y%m%d')
        
        # Nama NVR & site untuk folder foto
        parent_nvr = nvr_dict.get(camera.ip_address)
        nvr_name = parent_nvr.name.replace(" ", "_").upper() if parent_nvr and parent_nvr.name else "UNKNOWN_NVR"
        site_name = camera.site.name.replace(" ", "_").upper() if camera.site and camera.site.name else "UNKNOWN_SITE"
        
        # Folder foto: screenshots/SITE/NVR/TANGGAL
        relative_dir = os.path.join('screenshots', site_name, nvr_name, date_only)
        save_dir = os.path.join(upload_folder, relative_dir)
        os.makedirs(save_dir, exist_ok=True) 
        
        filename = f"{nvr_name}_CH_{camera.channel}_{date_only}.jpg"        
        full_path = os.path.join(save_dir, filename)
        
        with open(full_path, 'wb') as f:
            f.write(frame_bytes)
        
        # Simpan ke DB pakai format standard web (slash /)
        return os.path.join(relative_dir, filename).replace(os.sep, '/')
    except Exception as e: 
        logger.error(f"Gagal simpan gambar: {e}")
        return None

# Status yang disimpan untuk NVR yang tidak bisa dicek + SEMUA kamera di bawahnya.
# (Kolom status maks 20 karakter -> label tampilan "NVR Tidak Bisa Dicek" ada di report_status.py)
STATUS_UNCHECKABLE = "Tidak Bisa Dicek"


def process_camera_status(target_site_id=None):
    """Scan status kamera & NVR.

    PENTING: scan TIDAK membuat / menutup ticket. Tiket hanya dievaluasi saat
    Update to Report (apps/home/ticket_rules.py). Scan hanya menulis DeviceStatus
    (data siap di-report) dan mengembalikan hasil KHUSUS scan ini untuk halaman Hasil Scan.
    """
    logger.info(f"🚀 Memulai scan kamera... (Site: {target_site_id if target_site_id else 'ALL'})")

    query = db.session.query(Camera).join(Site).filter(Site.ai_report_enabled == True, Camera.is_active == True)
    if target_site_id: query = query.filter(Camera.site_id == int(target_site_id))
    camera_data = query.all()

    updates = 0
    ctp_list, gagal_list, nodata_list = [], [], []
    counts = {'online': 0, 'ctp': 0, 'offline': 0, 'available': 0, 'tidak_ditemukan': 0, 'tidak_bisa_dicek': 0,
              'nvr_ok': 0, 'nvr_gagal': 0}

    nvrs = {cam.ip_address: cam for cam in camera_data if cam.channel == 0}
    channels = [cam for cam in camera_data if cam.channel > 0]

    cam_ids = [cam.id for cam in camera_data]
    existing_device_statuses = {ds.camera_id: ds for ds in DeviceStatus.query.filter(DeviceStatus.camera_id.in_(cam_ids)).all()} if cam_ids else {}

    def save_to_db(cam, st, det, path):
        try:
            entry = existing_device_statuses.get(cam.id)
            if not entry:
                entry = DeviceStatus(camera_id=cam.id)
                db.session.add(entry)
                existing_device_statuses[cam.id] = entry
            entry.status = st
            entry.details = det
            entry.checked_at = datetime.now()
            entry.screenshot_path = path  # None jika CTP / Offline / tidak bisa dicek
            if st == "Online":
                entry.last_online = datetime.now()
        except Exception as e:
            logger.error(f"DB Error {cam.id}: {e}")
            db.session.rollback()

    # =========================================================
    # 1. DAFTAR CHANNEL TIAP NVR (PARALEL), API SESUAI MERK
    # =========================================================
    nvr_api_results = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_channels_by_brand, nvr): ip for ip, nvr in nvrs.items()}
        for future in as_completed(futures):
            ip = futures[future]
            try:
                nvr_api_results[ip] = future.result()
            except Exception as e:
                nvr_api_results[ip] = ({}, f"Error tak terduga: {e}")

    nvr_reason = {}  # ip -> alasan gagal (hanya NVR yang gagal)
    for ip, nvr in nvrs.items():
        api_data, reason = nvr_api_results.get(ip, ({}, REASON_NO_RESPONSE))
        site_name = nvr.site.name if nvr.site else "-"
        if api_data:
            counts['nvr_ok'] += 1
            save_to_db(nvr, "Online", "NVR Online (API Connected)", None)
        else:
            counts['nvr_gagal'] += 1
            nvr_reason[ip] = reason
            save_to_db(nvr, STATUS_UNCHECKABLE, f"NVR Tidak Bisa Dicek: {reason}", None)
            gagal_list.append({'site': site_name, 'nvr': nvr.name, 'ip': ip, 'api': 'Kamera (daftar channel)', 'reason': reason})

    # =========================================================
    # 2. AUTO-DISCOVERY KAMERA BARU (channel ada di NVR tapi belum ada di SMC)
    # =========================================================
    existing_channels_map = {(cam.ip_address, cam.channel): cam for cam in channels}
    new_channels_added = False
    for ip, nvr in nvrs.items():
        api_data = nvr_api_results.get(ip, ({}, None))[0]
        for ch_id_str, ch_info in api_data.items():
            try: ch_id = int(ch_id_str)
            except (TypeError, ValueError): continue
            if (ip, ch_id) not in existing_channels_map:
                new_cam = Camera(
                    site_id=nvr.site_id, name=ch_info.get('name') or f"Channel {ch_id}",
                    ip_address=ip, internal_ip=ch_info.get('ip'),
                    username=nvr.username, password=nvr.password,
                    device_type='Channel', channel=ch_id, model=nvr.model, is_active=True
                )
                db.session.add(new_cam)
                channels.append(new_cam)
                existing_channels_map[(ip, ch_id)] = new_cam
                new_channels_added = True
                logger.info(f"Auto-Discover Kamera Baru: {new_cam.name} (Ch {ch_id}) di NVR {ip}")
    if new_channels_added:
        db.session.commit()  # Supaya kamera baru punya ID sebelum difoto

    # =========================================================
    # 3. STATUS TIAP CHANNEL
    # =========================================================
    cams_for_snapshot = []
    cam_eval_results = {}
    for cam in channels:
        if cam.ip_address in nvr_reason:
            cam_eval_results[cam.id] = {"st": STATUS_UNCHECKABLE, "det": f"NVR Tidak Bisa Dicek: {nvr_reason[cam.ip_address]}", "int_ip": None}
            continue
        if cam.ip_address not in nvrs:
            # Kamera tanpa NVR induk yang ikut di-scan -> tidak bisa dinilai
            cam_eval_results[cam.id] = {"st": STATUS_UNCHECKABLE, "det": "NVR induk tidak ikut di-scan", "int_ip": None}
            continue
        api_data = nvr_api_results.get(cam.ip_address, ({}, None))[0]
        batch_info = api_data.get(cam.channel)

        if batch_info:
            api_status, int_ip, api_name = batch_info['status'], batch_info['ip'], batch_info.get('name')
            # Simpan nama device apa adanya. Nama NVR & nomor channel ditambahkan saat tampil
            if api_name and api_name != cam.name:
                cam.name = api_name
            if api_status == "Offline":
                cam_eval_results[cam.id] = {"st": "Offline", "det": "Offline dari API NVR (NO VIDEO)", "int_ip": int_ip}
            elif api_status == "Available":
                cam_eval_results[cam.id] = {"st": "Available", "det": "Channel Kosong / Idle", "int_ip": int_ip}
            else:
                cams_for_snapshot.append(cam)
                cam_eval_results[cam.id] = {"st": "Online", "det": "Online", "int_ip": int_ip}
        else:
            cam_eval_results[cam.id] = {"st": "Tidak Ditemukan", "det": "Channel tidak ditemukan di NVR", "int_ip": None}

    # Snapshot paralel (hanya kamera Online)
    snapshot_results = {}
    def fetch_snap(c):
        ok, frame_bytes, msg = capture_snapshot(c)
        return c.id, ok, frame_bytes
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_snap, c) for c in cams_for_snapshot]
        for future in as_completed(futures):
            c_id, ok, frame_bytes = future.result()
            snapshot_results[c_id] = (ok, frame_bytes)

    # Simpan hasil (sequential agar DB aman)
    count_key = {"Online": 'online', "Offline": 'offline', "Available": 'available',
                 "Tidak Ditemukan": 'tidak_ditemukan', STATUS_UNCHECKABLE: 'tidak_bisa_dicek'}
    for cam in channels:
        res = cam_eval_results.get(cam.id)
        if not res: continue

        final_path = None
        if res['st'] == "Online":
            ok, frame_bytes = snapshot_results.get(cam.id, (False, None))
            if ok:
                final_path = save_snapshot_and_get_path(cam, frame_bytes, nvrs)
            if not final_path:
                res['det'] = "CTP (Gagal Snapshot)"
                parent = nvrs.get(cam.ip_address)
                ctp_list.append({'site': cam.site.name if cam.site else "-",
                                 'nvr': parent.name if parent else cam.ip_address,
                                 'channel': cam.channel, 'camera': cam.name, 'camera_id': cam.id})

        if res['st'] == "Tidak Ditemukan":
            parent = nvrs.get(cam.ip_address)
            nodata_list.append({'site': cam.site.name if cam.site else "-",
                                'nvr': parent.name if parent else cam.ip_address,
                                'channel': cam.channel, 'camera': cam.name, 'camera_id': cam.id})

        if res['det'].startswith("CTP"):
            counts['ctp'] += 1
        else:
            counts[count_key.get(res['st'], 'online')] += 1

        if res['int_ip']: cam.internal_ip = res['int_ip']
        save_to_db(cam, res['st'], res['det'], final_path)
        updates += 1

    try:
        db.session.commit()
    except Exception as e:
        logger.error(f"Bulk Commit DB Error: {e}")
        db.session.rollback()

    logger.info(f"📊 Scan selesai: {counts}")
    # Jumlah kamera yang ikut 'tidak bisa dicek' per NVR gagal (untuk popup Hasil Scan)
    cams_per_ip = {}
    for cam in channels:
        cams_per_ip[cam.ip_address] = cams_per_ip.get(cam.ip_address, 0) + 1
    for g in gagal_list:
        g['cams'] = cams_per_ip.get(g['ip'], 0)
    by_loc = lambda c: (c['site'], c['nvr'], c['channel'] or 0)
    scan_result = {
        'counts': counts,
        'ctp': sorted(ctp_list, key=by_loc),
        'nodata': sorted(nodata_list, key=by_loc),
        'sites': sorted({cam.site.name for cam in camera_data if cam.site}),
        'gagal': gagal_list,
        'nvr_ids': [n.id for n in nvrs.values()],
    }
    return updates, scan_result
