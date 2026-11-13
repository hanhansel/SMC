from flask import render_template, jsonify, request, Response, stream_with_context
import cv2
from apps import db
from apps.home import blueprint
from flask_login import login_required, current_user

from apps.home.models import Site, Camera, CameraAIConfig 

# --- Fungsi deteksi AI (fallback jika modul AI tidak bisa di-import) ---
try:
    from apps.home.ai_processing import process_frame
    print("[INFO] AI processing function imported successfully.")
except ImportError:
    print("[WARNING] Could not import ai_processing. Using placeholder.")
    # Fallback: tampilkan pesan error di frame
    def process_frame(frame):
        cv2.putText(frame, "AI PROCESSING FAILED (Import Error)", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return frame
# ----------------------------------------------------------------------


## BAGIAN 1: VIDEO STREAM GENERATOR & ROUTE
# --- Konfigurasi Optimasi ---
TARGET_WIDTH = 800 # Lebar frame target dalam piksel
JPEG_QUALITY = 80  # Kualitas JPEG (0-100), 80 adalah kompromi yang baik
FRAME_SKIP = 2     # Hanya proses 1 dari setiap (FRAME_SKIP + 1) frame
# ----------------------------

def generate_frames(camera_id):
    cam = Camera.query.options(db.joinedload(Camera.ai_config)).get_or_404(camera_id)
    
    ai_config = cam.ai_config
    
    source = None
    is_person_detection_enabled = False # Deteksi orang aktif?
    
    # Dapatkan sumber dan status AI
    if ai_config:
        # Gunakan rtsp_url dari konfigurasi AI jika ada
        source = ai_config.rtsp_url
        # Cek apakah deteksi ORANG diaktifkan
        is_person_detection_enabled = ai_config.is_enabled and ai_config.detect_people
        
    # Gunakan 0 jika source masih None atau string kosong (fallback ke webcam lokal)
    source = source if source and source.strip() else 0
    
    video_capture = cv2.VideoCapture(source)

    if not video_capture.isOpened():
        print(f"Error: Tidak bisa membuka video stream dari sumber: {source}")
        # Tambahkan frame placeholder error jika stream gagal dibuka
        frame = 255 * cv2.ones((480, 640, 3), dtype='uint8')
        cv2.putText(frame, "STREAM OFFLINE / RTSP ERROR", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        return

    frame_count = 0
    while video_capture.isOpened():
        success, frame = video_capture.read()
        if not success:
            break
        
        # --- OPTIMASI 1: FRAME SKIPPING ---
        if frame_count % (FRAME_SKIP + 1) != 0:
            frame_count += 1
            continue
        
        # --- OPTIMASI 2: RESIZE FRAME ---
        (h, w) = frame.shape[:2]
        if w > TARGET_WIDTH: # Hanya resize jika lebar asli lebih besar
            aspect_ratio = w / h
            target_height = int(TARGET_WIDTH / aspect_ratio)
            frame = cv2.resize(frame, (TARGET_WIDTH, target_height), interpolation=cv2.INTER_AREA)

        # --- JALANKAN PROSES AI HANYA JIKA DETEKSI ORANG AKTIF ---
        if is_person_detection_enabled:
            try:
                # Memanggil fungsi AI yang sekarang hanya mendeteksi 'person'
                frame = process_frame(frame) 
                
                # Tambahkan overlay status AI
                cv2.putText(frame, "AI Status: PERSON DETECT ENABLED", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            except Exception as e:
                # Tampilkan pesan error jika AI gagal saat runtime
                cv2.putText(frame, "AI Runtime Error", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                print(f"AI Runtime Error: {e}")
        else:
            # Tambahkan overlay status AI jika tidak aktif
            cv2.putText(frame, "AI Status: DISABLED", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)


        # --- OPTIMASI 3: KUALITAS JPEG ---
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        ret, buffer = cv2.imencode('.jpg', frame, encode_param)
        frame = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        frame_count += 1

    video_capture.release()

@blueprint.route('/video_feed/<int:camera_id>')
@login_required
def video_feed(camera_id):
    if not current_user.can_view_dashboard:
        return "Akses Ditolak", 403
        
    return Response(stream_with_context(generate_frames(camera_id)),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


## BAGIAN 2: LIVE VIEW ROUTE (Filtering)
# ----------------------------------------------------------------------

@blueprint.route('/live_view') 
@login_required
def live_view_page(): 
    if not current_user.can_view_dashboard:
        flash("❌ Akses Ditolak.", "danger")
        return redirect(url_for('home_blueprint.master_location'))

    all_sites = Site.query.order_by(Site.name).all()
    sites_with_ai_cameras = []

    for site in all_sites:
        # Ambil semua perangkat induk (NVR atau Kamera Standalone)
        parent_devices = [c for c in site.cameras if c.channel == 0] 
        valid_nvrs = []

        for device in parent_devices:
            # Kasus 1: Perangkat adalah NVR
            if device.device_type == 'NVR':
                ai_channels = [
                    c for c in site.cameras 
                    if c.ip_address == device.ip_address
                    and c.channel > 0
                    and c.ai_config is not None
                    and c.ai_config.is_enabled == True
                ]
                if ai_channels:
                    device.ai_channels_list = ai_channels
                    valid_nvrs.append(device)
            
            # Kasus 2: Perangkat adalah Kamera Standalone dengan AI aktif
            elif device.device_type == 'Camera':
                if device.ai_config and device.ai_config.is_enabled == True:
                    # Kamera standalone diperlakukan sebagai "NVR" dengan satu channel (dirinya sendiri)
                    device.ai_channels_list = [device] 
                    valid_nvrs.append(device)

        # Jika ada NVR atau Kamera Standalone yang valid di site ini
        if valid_nvrs:
            site.valid_nvrs_list = valid_nvrs
            sites_with_ai_cameras.append(site)

    return render_template(
        'home/live_view.html', 
        sites=sites_with_ai_cameras,
        segment='live_view' 
    )