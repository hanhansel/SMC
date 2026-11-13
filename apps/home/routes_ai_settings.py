from flask import render_template, request, jsonify, flash, redirect, url_for
from apps.home import blueprint
from apps import db
from apps.home.models import Camera, CameraAIConfig, Site
from flask_login import login_required, current_user


def serialize_ai_config(config):
    """Mengubah objek CameraAIConfig SQLAlchemy menjadi dictionary JSON serializable."""
    if not config:
        return None
    return {
        'id': config.id,
        'camera_id': config.camera_id,
        'rtsp_url': config.rtsp_url,
        'is_enabled': config.is_enabled,
        'detect_people': config.detect_people,
        'detect_helmet': config.detect_helmet,
        'detect_custom': config.detect_custom,
        'custom_label': config.custom_label,
    }


## 1. ROUTE PENGATURAN HALAMAN
# ---------------------------------------------------------

@blueprint.route('/ai_settings')
@login_required
def ai_settings_page():
    """
    Menampilkan halaman pengaturan AI. 
    Mengambil semua kamera channel (> 0) dan melampirkan konfigurasi AI yang sudah ada (dalam bentuk dictionary).
    """
    if not current_user.can_manage_users: 
        flash("❌ Akses Ditolak: Hanya Super Admin yang bisa membuka AI Settings.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    # Ambil semua Site
    all_sites = Site.query.order_by(Site.name).all()
    
    # Ambil semua kamera dengan eager loading
    all_cameras = Camera.query.options(
        db.joinedload(Camera.ai_config),
        db.joinedload(Camera.site)
    ).order_by(Camera.site_id, Camera.ip_address, Camera.channel).all()
    
    structured_data = {}
    
    # Pre-map NVR parents for quick lookup to prevent O(n^2) search
    nvr_parents = {c.ip_address: c for c in all_cameras if c.channel == 0}
    
    for cam in all_cameras:
        site_id = cam.site_id
        ip_address = cam.ip_address
        
        # 1. Inisialisasi Struktur Data
        if site_id not in structured_data:
            # Pastikan cam.site tidak None sebelum mengakses .name
            site_name = cam.site.name if cam.site else 'Unknown Site'
            structured_data[site_id] = {
                'site_name': site_name,
                'nvrs': {}
            }
        
        # 2. Kelompokkan berdasarkan NVR/IP
        if ip_address not in structured_data[site_id]['nvrs']:
            nvr_parent = nvr_parents.get(ip_address)
            
            structured_data[site_id]['nvrs'][ip_address] = {
                'nvr_name': nvr_parent.name if nvr_parent else f'Single Camera/{ip_address}',
                'channels': []
            }
            
        # 3. Hanya tampilkan Channel yang dapat dikonfigurasi AI.
        is_configurable = (cam.channel > 0) or (cam.channel == 0 and cam.device_type == 'Camera')
        
        if is_configurable:
            # Serialisasi objek ORM sebelum di-render
            serialized_config = serialize_ai_config(cam.ai_config)
            
            channel_data = {
                'id': cam.id,
                'name': cam.name,
                'ip_address': cam.ip_address,
                'channel': cam.channel,
                'ai_config': serialized_config, 
                'is_configured': cam.ai_config is not None 
            }
            
            structured_data[site_id]['nvrs'][ip_address]['channels'].append(channel_data)

    return render_template(
        'home/ai_settings.html',
        segment='ai_settings',
        structured_config_data=structured_data,
        all_sites=all_sites 
    )

## 2. API UPDATE KONFIGURASI
# ---------------------------------------------------------

@blueprint.route('/api/update_camera_config/<int:camera_id>', methods=['POST'])
@login_required
def update_camera_config(camera_id):
    if not current_user.can_manage_users:
        return jsonify({'status': 'error', 'message': 'Akses Ditolak: Anda bukan Super Admin.'}), 403
        
    """API untuk update konfigurasi RTSP, Site, dan Deteksi AI."""
    camera = Camera.query.get_or_404(camera_id)
    
    if camera.device_type == 'NVR' and camera.channel == 0:
        return jsonify({'status': 'error', 'message': 'Tidak dapat mengkonfigurasi NVR induk, hanya channel atau kamera tunggal.'}), 403

    data = request.get_json()

    if not data:
        return jsonify({'status': 'error', 'message': 'Data tidak ditemukan.'}), 400

    try:
        # 1. Update Site
        if 'site_id' in data and data['site_id']:
            camera.site_id = int(data['site_id'])

        # 2. Handle Konfigurasi AI (Tabel CameraAIConfig)
        ai_config = camera.ai_config
        if not ai_config:
            ai_config = CameraAIConfig(camera_id=camera.id)
            db.session.add(ai_config)
        
        # Update Field di tabel CameraAIConfig
        if 'rtsp_url' in data:
            rtsp_value = data['rtsp_url'].strip() if data['rtsp_url'] else None
            ai_config.rtsp_url = rtsp_value
            
        # Update setting deteksi
        ai_config.detect_people = bool(data.get('detect_people', False))
        ai_config.detect_helmet = bool(data.get('detect_helmet', False))
        ai_config.detect_custom = bool(data.get('detect_custom', False))
        # Update label custom jika ada
        if 'custom_label' in data:
            ai_config.custom_label = data['custom_label'].strip() if data['custom_label'] else None
        
        # Update Master Toggle (is_enabled)
        ai_config.is_enabled = any([
            ai_config.detect_people, 
            ai_config.detect_helmet, 
            ai_config.detect_custom
        ])

        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'message': f'Konfigurasi {camera.name} berhasil disimpan. AI Status: {"ACTIVE" if ai_config.is_enabled else "INACTIVE"}'
        })

    except Exception as e:
        db.session.rollback()
        print(f"ERROR: Failed to update camera config for ID {camera_id}: {e}") 
        return jsonify({'status': 'error', 'message': f'Database Error: {str(e)}'}), 500