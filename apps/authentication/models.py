# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from flask_login import UserMixin

from apps import db, login_manager

from apps.authentication.util import hash_pass
from apps.authentication.access import access_control_enabled

class Users(db.Model, UserMixin):

    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True)
    email         = db.Column(db.String(64), unique=True)
    password      = db.Column(db.LargeBinary)

    role = db.Column(db.String(20), default='Teknisi')
    can_manage_users = db.Column(db.Boolean, default=False)
    can_view_dashboard = db.Column(db.Boolean, default=False)
    can_view_report = db.Column(db.Boolean, default=False)
    can_edit_daily_report = db.Column(db.Boolean, default=False)
    can_manage_master_location = db.Column(db.Boolean, default=False)
    can_view_ticket = db.Column(db.Boolean, default=False)
    can_manage_ticket_log = db.Column(db.Boolean, default=False)
    can_view_ticket_archive = db.Column(db.Boolean, default=False)
    can_manage_email_settings = db.Column(db.Boolean, default=False)
    can_manage_system_settings = db.Column(db.Boolean, default=False)
    can_view_weekly_report = db.Column(db.Boolean, default=False) # Parent Weekly
    can_view_weekly_hdd = db.Column(db.Boolean, default=False)    # Child Weekly HDD
    can_view_daily_report = db.Column(db.Boolean, default=False)  # Parent Daily
    can_view_daily_camera = db.Column(db.Boolean, default=False)  # Child Daily Camera
    can_view_daily_hdd = db.Column(db.Boolean, default=False)     # Child Daily HDD
    
    can_view_ticket_management = db.Column(db.Boolean, default=False) # Parent Ticket
    
    def __init__(self, **kwargs):
        for property, value in kwargs.items():
            # depending on whether value is an iterable or not, we must
            # unpack it's value (when **kwargs is request.form, some values
            # will be a 1-element list)
            if hasattr(value, '__iter__') and not isinstance(value, str):
                # the ,= unpack of a singleton fails PEP8 (travis flake8 test)
                value = value[0]

            if property == 'password':
                value = hash_pass(value)  # we need bytes here (not plain str)

            setattr(self, property, value)

    def __getattribute__(self, name):
        # Saklar hak akses MATI (default) -> semua izin can_* dibaca True = cukup login.
        # Hanya dibaca, nilai asli di database tidak diubah (lihat apps/authentication/access.py).
        if name.startswith('can_') and not access_control_enabled():
            return True
        return super().__getattribute__(name)

    def __repr__(self):
        return str(self.username)

    @classmethod
    def find_by_email(cls, email: str) -> "Users":
        return cls.query.filter_by(email=email).first()

    @classmethod
    def find_by_username(cls, username: str) -> "Users":
        return cls.query.filter_by(username=username).first()
    
    @classmethod
    def find_by_id(cls, _id: int) -> "Users":
        return cls.query.filter_by(id=_id).first()
   
    def save(self) -> None:
        try:
            db.session.add(self)
            db.session.commit()
          
        except SQLAlchemyError as e:
            db.session.rollback()
            db.session.close()
            error = str(e.__dict__['orig'])
            raise InvalidUsage(error, 422)
    
    def delete_from_db(self) -> None:
        try:
            db.session.delete(self)
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            db.session.close()
            error = str(e.__dict__['orig'])
            raise InvalidUsage(error, 422)
        return

@login_manager.user_loader
def user_loader(id):
    return Users.query.filter_by(id=id).first()

# Tidak ada request_loader: login hanya lewat /login (username + password).
