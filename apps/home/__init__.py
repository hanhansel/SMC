from flask import Blueprint
import os
import sys
from apps.home.camera_naming import camera_display_name

blueprint = Blueprint(
    'home_blueprint',
    __name__,
    url_prefix=''
)
blueprint.add_app_template_global(camera_display_name)

from apps.home.report_status import status_label as report_status_label, status_css_class as report_status_class
blueprint.add_app_template_global(report_status_label, name='report_status_label')
blueprint.add_app_template_global(report_status_class, name='report_status_class')

from apps.branding import TEMPLATE_VALUES as BRANDING_VALUES
blueprint.add_app_template_global(BRANDING_VALUES, name='branding')

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Mode EXE (PyInstaller)
    BASE_DIR = sys._MEIPASS
    SCREENSHOT_BASE_PATH = os.path.join(BASE_DIR, "apps", "static", "screenshots")
else:
    # Mode Development (run.py)
    app_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    SCREENSHOT_BASE_PATH = os.path.join(app_root_dir, "static", "screenshots")

# PENTING: Map ini harus sesuai dengan CSS/JS di templates/home/index.html
STATUS_MAP = {
    'Online': {'color': 'text-green-600', 'bg': 'bg-green-500', 'border': 'border-green-600', 'badge': 'bg-green-500', 'icon': 'check-circle', 'text_status': 'Online'},
    'Offline': {'color': 'text-red-600', 'bg': 'bg-red-500', 'border': 'border-red-600', 'badge': 'bg-red-500 status-offline-animate', 'icon': 'x-circle', 'text_status': 'Offline'},
    'Abnormal': {'color': 'text-yellow-600', 'bg': 'bg-yellow-500', 'border': 'border-yellow-600', 'badge': 'bg-yellow-500', 'icon': 'alert-triangle', 'text_status': 'Abnormal'},
    'Uncheckable': {'color': 'text-orange-600', 'bg': 'bg-orange-400', 'border': 'border-orange-500', 'badge': 'bg-orange-400', 'icon': 'alert-octagon', 'text_status': 'Tidak Bisa Dicek'},
    'Unknown': {'color': 'text-gray-500', 'bg': 'bg-gray-400', 'border': 'border-gray-400', 'badge': 'bg-gray-400', 'icon': 'clock', 'text_status': 'Unknown'},
    # Status untuk Weekly Report
    'NO DATA': {'color': 'text-gray-500', 'bg': 'bg-gray-200', 'border': 'border-gray-400', 'badge': 'bg-gray-200', 'icon': 'clock', 'text_status': 'No Data'},
}

# Status di database -> key STATUS_MAP untuk dashboard
DASHBOARD_STATUS_ALIAS = {'Tidak Bisa Dicek': 'Uncheckable'}

def dashboard_status_key(raw):
    key = DASHBOARD_STATUS_ALIAS.get(raw, raw)
    return key if key in STATUS_MAP else 'Unknown'
