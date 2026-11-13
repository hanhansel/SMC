from flask import render_template, request, url_for, redirect, flash
from flask_login import login_required, current_user
from apps import db
from apps.home import blueprint
from apps.authentication.models import Users
from apps.authentication.util import hash_pass
from apps.authentication.access import access_control_enabled

# Daftar menu & izin RBAC (parent -> children)
RBAC_MENUS = [
    {
        "id": "can_view_dashboard", "label": "Dashboard", "is_parent": True, "children": []
    },
    {
        "id": "can_view_weekly_report", "label": "Weekly Report", "is_parent": True,
        "children": [
            {"id": "can_view_report", "label": "Weekly Camera Report"},
            {"id": "can_view_weekly_hdd", "label": "Weekly HDD Report"},
            {"id": "can_edit_daily_report", "label": "Revisi Manual Camera Report"}
        ]
    },
    {
        "id": "can_view_daily_report", "label": "Daily Report", "is_parent": True,
        "children": [
            {"id": "can_view_daily_camera", "label": "Daily Camera Report"},
            {"id": "can_view_daily_hdd", "label": "Daily HDD Report"}
        ]
    },
    {
        "id": "can_view_ticket_management", "label": "Ticket Management", "is_parent": True,
        "children": [
            {"id": "can_view_ticket", "label": "Ticket Checker"},
            {"id": "can_manage_ticket_log", "label": "Edit Ticket Log"},
            {'id': 'can_view_ticket_archive', 'label': 'Akses Ticket Archive'},
            {"id": "can_manage_email_settings", "label": "Auto Email Setting"}
        ]
    },
    {
        "id": "can_manage_master_location", "label": "Master Location", "is_parent": True, "children": []
    },
    {
        "id": "can_manage_system_settings", "label": "NVR & Site Settings", "is_parent": True, "children": []
    },
    {
        "id": "can_manage_users", "label": "Account Management (Super Admin)", "is_parent": True, "children": []
    }
]

def _all_permission_ids():
    ids = []
    for perm in RBAC_MENUS:
        ids.append(perm['id'])
        ids.extend(child['id'] for child in perm.get('children', []))
    return ids

@blueprint.route('/accounts')
@login_required
def account_management():
    if not current_user.can_manage_users: return redirect(url_for('home_blueprint.index'))
    users = Users.query.all()
    return render_template('home/account_management.html', users=users, segment='accounts', rbac_menus=RBAC_MENUS)

@blueprint.route('/accounts/add', methods=['POST'])
@login_required
def add_account():
    if not current_user.can_manage_users: return redirect(url_for('home_blueprint.index'))
    
    username, email = request.form.get('username'), request.form.get('email')
    password, role = request.form.get('password'), (request.form.get('role') or 'Teknisi')
    
    if Users.query.filter((Users.username == username) | (Users.email == email)).first():
        flash("❌ Username atau Email sudah terdaftar", "warning")
        return redirect(url_for('home_blueprint.account_management'))
        
    # Ambil izin dari form (parent & child)
    if access_control_enabled():
        permissions = {pid: (request.form.get(pid) == 'on') for pid in _all_permission_ids()}
    else:
        # Hak akses disembunyikan (cukup login): akun baru diberi semua izin, sama dengan
        # yang berlaku sekarang, supaya tetap lengkap kalau saklar hak akses dinyalakan lagi.
        permissions = {pid: True for pid in _all_permission_ids()}
            
    new_user = Users(username=username, email=email, role=role, **permissions)
    new_user.password = hash_pass(password) 
    try:
        db.session.add(new_user)
        db.session.commit()
        flash("✅ Akun berhasil dibuat", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal membuat akun: {e}", "danger")
    return redirect(url_for('home_blueprint.account_management'))

@blueprint.route('/accounts/edit/<int:user_id>', methods=['POST'])
@login_required
def edit_account(user_id):
    if not current_user.can_manage_users: return redirect(url_for('home_blueprint.index'))
    user = Users.query.get_or_404(user_id)
    user.username, user.email = request.form.get('username'), request.form.get('email')
    user.role = request.form.get('role') or user.role   # kolom Role disembunyikan saat hak akses mati
    
    new_password = request.form.get('password')
    if new_password and new_password.strip() != "": user.password = hash_pass(new_password)
        
    # Perbarui izin (parent & child)
    # Saat hak akses mati, form tidak berisi centang izin -> izin tersimpan JANGAN ditimpa.
    if access_control_enabled():
        for pid in _all_permission_ids():
            setattr(user, pid, request.form.get(pid) == 'on')
    try:
        db.session.commit()
        flash("✅ Akun berhasil diperbarui", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal memperbarui akun: {e}", "danger")
    return redirect(url_for('home_blueprint.account_management'))

@blueprint.route('/accounts/delete/<int:user_id>', methods=['POST'])
@login_required
def delete_account(user_id):
    if not current_user.can_manage_users: return redirect(url_for('home_blueprint.index'))
    if current_user.id == user_id:
        flash("❌ Anda tidak bisa menghapus akun Anda sendiri", "danger")
        return redirect(url_for('home_blueprint.account_management'))
    user = Users.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash("✅ Akun berhasil dihapus", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Gagal: {e}", "danger")
    return redirect(url_for('home_blueprint.account_management'))