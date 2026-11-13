# -*- encoding: utf-8 -*-

from apps import db
from flask_login import UserMixin
from apps.authentication.util import hash_pass
from datetime import datetime

class Site(db.Model):
    __tablename__ = 'site'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.String(200))
    report_days = db.Column(db.String(100)) 
    ai_report_enabled = db.Column(db.Boolean, default=False)
    is_daily_report = db.Column(db.Boolean, default=False)
    daily_report_time = db.Column(db.String(10), nullable=True) # Jam laporan harian (HH:MM)
    cameras = db.relationship('Camera', backref='site', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Site {self.name}>'

class Camera(db.Model):
    __tablename__ = 'camera'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(50), nullable=False)
    internal_ip = db.Column(db.String(50), nullable=True)
    username = db.Column(db.String(50))
    password = db.Column(db.String(200))
    device_type = db.Column(db.String(20)) 
    channel = db.Column(db.Integer, default=0) 
    channel_count = db.Column(db.Integer, default=0) 
    model = db.Column(db.String(50), nullable=True) 
    is_active = db.Column(db.Boolean, default=True)
    
    # Urutan tampil NVR di halaman
    sort_order = db.Column(db.Integer, default=0)
    
    # Label set HDD yang sedang aktif per NVR
    current_hdd_set = db.Column(db.String(50), nullable=True) 

    status_record = db.relationship('DeviceStatus', uselist=False, back_populates='camera', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Camera {self.name}>'

class DeviceStatus(db.Model):
    __tablename__ = 'device_status'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), unique=True, nullable=False)
    status = db.Column(db.String(20), default='Unknown') 
    details = db.Column(db.Text, nullable=True)
    last_online = db.Column(db.DateTime, nullable=True)
    checked_at = db.Column(db.DateTime, default=datetime.now)
    screenshot_path = db.Column(db.String(255), nullable=True)

    camera = db.relationship('Camera', back_populates='status_record')

class WeeklyStatusSnapshot(db.Model):
    __tablename__ = 'weekly_status_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    status = db.Column(db.String(20))
    snapshot_date = db.Column(db.DateTime, default=datetime.now)
    screenshot_path = db.Column(db.String(255), nullable=True)
    # CTP = API bilang Online tapi snapshot gagal. Disimpan eksplisit (bukan ditebak dari
    # "tidak ada foto"), karena foto lama memang dihapus setelah 150 hari.
    is_ctp = db.Column(db.Boolean, default=False)
    camera = db.relationship('Camera', backref=db.backref('weekly_snapshots', cascade='all, delete-orphan'))

class ScanRun(db.Model):
    """Hasil 1x scan (Cek Status). Dipakai halaman 'Hasil Scan' — selalu per scan, bukan akumulasi hari."""
    __tablename__ = 'scan_run'
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime, nullable=True)
    scope = db.Column(db.String(200))       # "Semua site AI aktif" / nama site
    result_json = db.Column(db.Text)         # ringkasan + daftar CTP / Gagal Cek / status HDD tak dikenal

class CameraAIConfig(db.Model):
    __tablename__ = 'camera_ai_config'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), unique=True, nullable=False)
    detect_person = db.Column(db.Boolean, default=False)
    confidence_threshold = db.Column(db.Float, default=0.5)
    roi_points = db.Column(db.Text, nullable=True) 
    alert_telegram = db.Column(db.Boolean, default=False)
    camera = db.relationship('Camera', backref=db.backref('ai_config', uselist=False, cascade="all, delete-orphan"))

class PersonDetectionLog(db.Model):
    __tablename__ = 'person_detection_log'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    image_path = db.Column(db.String(255))
    confidence = db.Column(db.Float)
    camera = db.relationship('Camera', backref=db.backref('person_detection_logs', cascade='all, delete-orphan'))

class HDDStatus(db.Model):
    __tablename__ = 'hdd_status'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    disk_no = db.Column(db.Integer, default=1)
    serial_number = db.Column(db.String(100), default="-")
    brand = db.Column(db.String(50), nullable=True) # Merk HDD (Seagate, WD, dll) untuk kolom Disk Info
    status = db.Column(db.String(50), default="No Disk")
    capacity = db.Column(db.String(50), default="-")
    free_space = db.Column(db.String(50), default="-") 
    property = db.Column(db.String(20), default="-") 
    last_checked = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    camera = db.relationship('Camera', backref=db.backref('hdd_statuses', cascade="all, delete-orphan"))

class HDDStatusHistory(db.Model):
    __tablename__ = 'hdd_status_history'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    disk_no = db.Column(db.Integer, default=1)
    status = db.Column(db.String(50))
    
    capacity_at_time = db.Column(db.String(50))
    free_space_at_time = db.Column(db.String(50))
    hdd_set_at_time = db.Column(db.String(50)) 
    serial_number_at_time = db.Column(db.String(100))
    brand_at_time = db.Column(db.String(50), nullable=True)
    snapshot_date = db.Column(db.Date, default=datetime.now)
    camera = db.relationship('Camera', backref=db.backref('hdd_status_histories', cascade='all, delete-orphan'))

class MasterLocation(db.Model):
    __tablename__ = 'master_location'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), unique=True, nullable=False)
    image_path = db.Column(db.String(255), nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.now)
    
    camera = db.relationship('Camera', backref=db.backref('master_location', uselist=False, cascade="all, delete-orphan"))

class TicketNotification(db.Model):
    __tablename__ = 'ticket_notification'
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    status = db.Column(db.String(20), default='Open') 
    created_at = db.Column(db.DateTime, default=datetime.now)
    resolved_at = db.Column(db.DateTime, nullable=True) 
    issue_type = db.Column(db.String(50), default='Unknown')
    ticket_number = db.Column(db.String(100), nullable=True)
    masalah = db.Column(db.Text, nullable=True)
    perbaikan = db.Column(db.Text, nullable=True)
    is_logged = db.Column(db.Boolean, default=False)
    camera = db.relationship('Camera', backref=db.backref('ticket_history', cascade="all, delete-orphan"))

class EmailSettings(db.Model):
    __tablename__ = 'email_settings'
    id = db.Column(db.Integer, primary_key=True)
    smtp_server = db.Column(db.String(100), default='smtp.office365.com')
    smtp_port = db.Column(db.Integer, default=587)
    sender_email = db.Column(db.String(100), nullable=True)
    sender_password = db.Column(db.String(255), nullable=True) # Disimpan terenkripsi
    receiver_emails = db.Column(db.Text, nullable=True) # Pisahkan dengan koma jika lebih dari satu
    cc_emails = db.Column(db.Text, nullable=True) # Pisahkan dengan koma
    schedule_time = db.Column(db.String(10), default='08:00') # Format HH:MM
    is_active = db.Column(db.Boolean, default=False)
    email_template = db.Column(db.Text, nullable=True)