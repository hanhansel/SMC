# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

import os

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from importlib import import_module

from flask.json.provider import DefaultJSONProvider
from datetime import datetime
from jinja2 import Undefined # Import Undefined untuk penanganan khusus

# --- CUSTOM JSON PROVIDER (Flask 3.x) ---
class CustomJSONProvider(DefaultJSONProvider):
   """Mengubah objek datetime menjadi string dan menangani Jinja2 Undefined."""
   def default(self, obj):
      if isinstance(obj, datetime):
         return obj.strftime('%Y-%m-%d %H:%M:%S') 
      if isinstance(obj, Undefined):
         return 'Unknown' 
      return super().default(obj)
# ----------------------------------------


db = SQLAlchemy()
login_manager = LoginManager()


def register_extensions(app):
   db.init_app(app)
   login_manager.init_app(app)


def register_blueprints(app):
   for module_name in ('authentication', 'home'):
      module = import_module('apps.{}.routes'.format(module_name))
      app.register_blueprint(module.blueprint)


def configure_database(app):
   # Flask 3.x: before_first_request dihapus, gunakan app_context langsung
   with app.app_context():
      try:
         db.create_all()
         # Auto-migration: tambah kolom sort_order jika belum ada
         try:
             db.session.execute(db.text("ALTER TABLE camera ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0"))
             db.session.execute(db.text("UPDATE camera SET sort_order = id WHERE sort_order = 0 OR sort_order IS NULL"))
             db.session.commit()
         except Exception:
             db.session.rollback()
         # Auto-migration: kolom brand untuk Disk Info HDD Report
         try:
             db.session.execute(db.text("ALTER TABLE hdd_status ADD COLUMN IF NOT EXISTS brand VARCHAR(50)"))
             db.session.execute(db.text("ALTER TABLE hdd_status_history ADD COLUMN IF NOT EXISTS brand_at_time VARCHAR(50)"))
             db.session.commit()
         except Exception:
             db.session.rollback()
         print('> [OK] Database PostgreSQL Terhubung Sukses!')
      except Exception as e:
         print('> [ERROR] FATAL DATABASE: ' + str(e) )
         print('> Pastikan PostgreSQL berjalan dan password di config.py sudah benar!')
         raise e

   @app.teardown_request
   def shutdown_session(exception=None):
      db.session.remove()

def create_app(config):
   app = Flask(__name__)
   app.config.from_object(config)

   # Saklar hak akses per menu (default mati = cukup login). Lihat apps/authentication/access.py
   from apps.authentication.access import init_access_control
   init_access_control(app)

   # --- Flask 3.x: JSON Provider ---
   app.json = CustomJSONProvider(app)
   
   register_extensions(app)
   register_blueprints(app)
   configure_database(app)
   return app