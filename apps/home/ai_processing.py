import cv2 as cv
import numpy as np
import os

# --- Konfigurasi Model dan Kelas ---

# Dapatkan path absolut ke direktori saat ini
base_dir = os.path.dirname(os.path.abspath(__file__))

# Path ke file model Caffe relatif terhadap file ini
# Model ada di folder 'det' (opsional)
MODEL_CAFFE = os.path.join(base_dir, "..", "..", "det", "MobileNetSSD_deploy.prototxt")
WEIGHTS_CAFFE = os.path.join(base_dir, "..", "..", "det", "MobileNetSSD_deploy.caffemodel")

CONFIDENCE_THRESHOLD = 0.5 # Batas ambang kepercayaan deteksi

# Label kelas (PASCAL VOC)
CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat",
    "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
    "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
    "sofa", "train", "tvmonitor"
]

# Warna acak untuk setiap kelas
COLORS = np.random.uniform(0, 255, size=(len(CLASSES), 3))

# --- Muat Model Jaringan Saraf Dalam (DNN) ---
net = None

# Model hanya dimuat jika filenya ada
if os.path.exists(MODEL_CAFFE) and os.path.exists(WEIGHTS_CAFFE):
    try:
        print("[INFO] Memuat model MobileNet-SSD...")
        net = cv.dnn.readNetFromCaffe(MODEL_CAFFE, WEIGHTS_CAFFE)
        print("[INFO] Model AI berhasil dimuat.")
    except Exception as e:
        # File rusak -> fitur AI nonaktif, server tetap jalan
        print(f"[WARNING] File model ada tapi gagal dimuat: {e}")
        net = None
else:
    # File tidak ada -> fitur AI nonaktif, server tetap jalan
    print("[INFO] Model AI tidak ditemukan. Fitur deteksi manusia dinonaktifkan (Server tetap jalan).")
    net = None

def process_frame(frame):
    """Fungsi untuk memproses satu frame dan melakukan deteksi objek."""
    
    # Model tidak dimuat -> kembalikan frame asli
    if net is None:
        return frame

    try:
        (h, w) = frame.shape[:2]
        blob = cv.dnn.blobFromImage(cv.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)

        net.setInput(blob)
        detections = net.forward()

        for i in np.arange(0, detections.shape[2]):
            confidence = detections[0, 0, i, 2]

            if confidence > CONFIDENCE_THRESHOLD:
                idx = int(detections[0, 0, i, 1])
                # Pastikan index kelas valid
                if idx < len(CLASSES):
                    box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                    (startX, startY, endX, endY) = box.astype("int")

                    label = f"{CLASSES[idx]}: {confidence:.2f}"
                    cv.rectangle(frame, (startX, startY), (endX, endY), COLORS[idx], 2)
                    y = startY - 15 if startY - 15 > 15 else startY + 15
                    cv.putText(frame, label, (startX, y), cv.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[idx], 2)
    except Exception:
        pass # Skip jika ada error saat processing frame
            
    return frame