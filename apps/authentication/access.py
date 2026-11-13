# apps/authentication/access.py
# =========================================================
# SAKLAR HAK AKSES (RBAC)
# ---------------------------------------------------------
# MATI (default) : cukup login. Semua izin can_* dianggap True untuk setiap akun,
#                  dan bagian Role / hak akses di Account Management disembunyikan.
# NYALA          : izin per menu dibaca dari database.
#
# Dinyalakan lewat config.ini (lihat apps/config.py):
#   [ACCESS]
#   enabled = true
#
# Nilai izin di database tidak pernah diubah oleh saklar ini, jadi saat dinyalakan
# lagi setiap akun kembali ke izin yang tersimpan.
# =========================================================

_enabled = False


def init_access_control(app):
    global _enabled
    _enabled = bool(app.config.get('ACCESS_CONTROL_ENABLED', False))
    app.add_template_global(access_control_enabled, 'access_control_enabled')


def access_control_enabled():
    return _enabled
