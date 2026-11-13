# apps/branding.py
# =========================================================
# TEKS KHUSUS PERUSAHAAN (satu-satunya tempat)
# ---------------------------------------------------------
# - Format nomor ticket (Ticket Log)
# - Contoh isian server & email pengirim (Auto Email Settings)
# =========================================================

# Nomor ticket yang diketik angka saja (mis. "15") otomatis diformat.
#   {no} = angka yang diketik, {mm} = bulan 2 digit, {yy} = tahun 2 digit
TICKET_FORMAT = "{no}"
TICKET_FORMAT_HINT = "TICKET FORMAT"

SMTP_SERVER_EXAMPLE = "smtp.example.com"
SENDER_EMAIL_EXAMPLE = "monitoring@example.com"

# Dipakai di template sebagai {{ branding.<nama> }}
TEMPLATE_VALUES = {
    'ticket_format_hint': TICKET_FORMAT_HINT,
    'smtp_server_example': SMTP_SERVER_EXAMPLE,
    'sender_email_example': SENDER_EMAIL_EXAMPLE,
}
