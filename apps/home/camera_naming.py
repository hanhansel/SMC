# apps/home/camera_naming.py
# Format tampilan nama kamera:
#   Hikvision : "<NVR> - <nama device>"            (nama device Hikvision sudah memuat channel)
#   Lainnya   : "<NVR> - <nama device> - CH <n>"
# Nama kamera di DB bisa sudah mengandung nama NVR / channel (rename NVR "NVR X - Ch 7", dsb).
# Bagian yang dobel dibuang dulu.
import re

def clean_camera_name(cam_name, nvr_name=None, channel=None, keep_channel=False):
    """Nama device tanpa nama NVR di depannya. keep_channel=False juga membuang token channel
    yang sama dengan nomor channel-nya (karena "CH <n>" akan ditambahkan terpisah)."""
    nvr_lower = (nvr_name or "").strip().lower()
    token = None
    if channel is not None and not keep_channel:
        token = re.compile(rf'(?<![A-Za-z0-9])(?:channel|chan|ch)\s*[-_#.:]?\s*0*{int(channel)}(?![0-9])', re.IGNORECASE)

    parts = []
    for part in re.split(r'\s+[-|]\s+', (cam_name or "").strip()):
        if nvr_lower and part.lower().startswith(nvr_lower):
            part = part[len(nvr_lower):]
        if token:
            part = token.sub('', part)
        # Kurung yang jadi kosong setelah token channel dibuang ("Camera (CH 5)" -> "Camera ()") ikut dihapus,
        # kurung yang masih berisi ("Kamera (Lobby)") dibiarkan utuh
        part = re.sub(r'\(\s*\)|\[\s*\]', '', part).strip(" -_|:")
        if part and part.lower() != nvr_lower:
            parts.append(part)
    return " - ".join(parts)

def camera_display_name(nvr_name, cam_name, channel, include_nvr=True, model=None):
    """Hikvision: 'NVR Area Luar - Camera IP 07 - Channel 7'. Lainnya: 'NVR UNV - Lobby - CH 7'.
    Nama device kosong -> 'NVR Area Luar - CH 7'."""
    is_hikvision = "HIKVISION" in (model or "").upper()
    parts = [nvr_name] if include_nvr and nvr_name else []
    device = clean_camera_name(cam_name, nvr_name, channel, keep_channel=is_hikvision)
    if device: parts.append(device)
    if not (is_hikvision and device):
        parts.append(f"CH {channel}")
    return " - ".join(parts)
