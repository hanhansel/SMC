# apps/home/device_api.py
# =========================================================
# PATH API NVR PER MERK (satu-satunya tempat)
# ---------------------------------------------------------
# Dipakai oleh ai_report.py (status kamera & snapshot) dan
# hdd_checker.py (status HDD). Semua request memakai Digest auth.
# Path asli tidak disertakan di versi publik ini.
# =========================================================

# Uniview (UNV)
UNV_CHANNELS = "API CALL FORMAT"
UNV_SNAPSHOT_PREVIEW = "API CALL FORMAT"
UNV_SNAPSHOT = "API CALL FORMAT"
UNV_STORAGE = "API CALL FORMAT"

# Hikvision
HIK_CHANNELS = "API CALL FORMAT"
HIK_SNAPSHOT = "API CALL FORMAT"
HIK_STORAGE = "API CALL FORMAT"


def device_url(ip, path, **params):
    """URL lengkap: http://<ip><path>, dengan {channel} / {stream} diisi dari params."""
    return f"http://{ip}{path.format(**params)}"
