# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from flask import render_template, redirect, request, url_for, flash, jsonify
from flask_login import (
    current_user,
    login_user,
    logout_user
)

from apps import login_manager
from apps.authentication import blueprint
from apps.authentication.forms import LoginForm
from apps.authentication.models import Users

from apps.authentication.util import verify_pass

@blueprint.route('/')
def route_default():
    return redirect(url_for('home_blueprint.index'))
# Login (akun baru hanya dibuat Admin lewat Account Management)

@blueprint.route('/login', methods=['GET', 'POST'])
def login():
    login_form = LoginForm(request.form)
    if 'login' in request.form:

        # read form data
        user_id  = request.form['username'] # we can have here username OR email
        password = request.form['password']

        # Locate user
        user = Users.find_by_username(user_id)

        # if user not found
        if not user:

            user = Users.find_by_email(user_id)

            if not user:
                return render_template( 'accounts/login.html',
                                        msg='Unknown User or Email',
                                        form=login_form)

        # Check the password
        if verify_pass(password, user.password):

            login_user(user)
            # Kembali ke halaman yang tadi dibuka (mis. link /weekly_report) -> hanya path lokal, bukan situs lain
            nxt = request.args.get('next') or ''
            if nxt.startswith('/') and not nxt.startswith('//') and '\\' not in nxt:
                return redirect(nxt)
            return redirect(url_for('authentication_blueprint.route_default'))

        # Something (user or pass) is not ok
        return render_template('accounts/login.html',
                               msg='Wrong user or password',
                               form=login_form)

    if not current_user.is_authenticated:
        return render_template('accounts/login.html',
                               form=login_form)
    return redirect(url_for('home_blueprint.index'))


@blueprint.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('authentication_blueprint.login'))

# Errors

@login_manager.unauthorized_handler
def unauthorized_handler():
    # Tamu yang belum login diarahkan ke halaman Login (bukan 403).
    # Permintaan dari JavaScript (fetch / AJAX) -> JSON 401, supaya halaman bisa menampilkan pesan, bukan HTML login.
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': False, 'message': "Sesi login sudah habis (server dinyalakan ulang). Silakan login lagi."}), 401
    if request.method == 'GET':
        return redirect(url_for('authentication_blueprint.login', next=request.full_path.rstrip('?')))
    return redirect(url_for('authentication_blueprint.login'))


@blueprint.errorhandler(403)
def access_forbidden(error):
    return render_template('home/page-403.html'), 403


@blueprint.errorhandler(404)
def not_found_error(error):
    return render_template('home/page-404.html'), 404


@blueprint.errorhandler(500)
def internal_error(error):
    return render_template('home/page-500.html'), 500
