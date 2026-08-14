# Driver Hub [STG] — Seed Test Plan (QA Fungsional)

> Digenerate otomatis dari analisis statis APK.

## Identitas Build

| Field | Nilai |
|---|---|
| Package | `com.phbid_darat.supir.stg` |
| Version | 2.1.1-stg (code 14) |
| Min / Target SDK | 23 / 36 |
| Main activity | `com.phbid_darat.supir.MainActivity` |
| Environment | **Staging** — aman untuk diuji end-to-end |

## Entry Point

- Launcher: `com.phbid_darat.supir.MainActivity`

## Permission Sensitif (uji granted vs denied)

- **Lokasi background** (`ACCESS_BACKGROUND_LOCATION`) — skenario izin diberikan & ditolak
- **Lokasi kasar** (`ACCESS_COARSE_LOCATION`) — skenario izin diberikan & ditolak
- **Lokasi presisi** (`ACCESS_FINE_LOCATION`) — skenario izin diberikan & ditolak
- **Kamera** (`CAMERA`) — skenario izin diberikan & ditolak
- **Notifikasi** (`POST_NOTIFICATIONS`) — skenario izin diberikan & ditolak

## Suite per Modul Fitur

Modul fitur terdeteksi dari kode app. Tiap suite minimal mencakup: buka layar, happy path, error path.

### 1. Auth

| ID | Skenario | Ekspektasi |
|---|---|---|
| AUTH-01 | Buka layar auth | Termuat tanpa crash |
| AUTH-02 | Happy path auth | Alur utama sukses |
| AUTH-03 | Error path auth | Error ditangani & ada recovery |

### 2. Notification

| ID | Skenario | Ekspektasi |
|---|---|---|
| NOTI-01 | Buka layar notification | Termuat tanpa crash |
| NOTI-02 | Happy path notification | Alur utama sukses |
| NOTI-03 | Error path notification | Error ditangani & ada recovery |

### 3. Profile

| ID | Skenario | Ekspektasi |
|---|---|---|
| PROF-01 | Buka layar profile | Termuat tanpa crash |
| PROF-02 | Happy path profile | Alur utama sukses |
| PROF-03 | Error path profile | Error ditangani & ada recovery |

### 4. Tracking

| ID | Skenario | Ekspektasi |
|---|---|---|
| TRAC-01 | Buka layar tracking | Termuat tanpa crash |
| TRAC-02 | Happy path tracking | Alur utama sukses |
| TRAC-03 | Error path tracking | Error ditangani & ada recovery |

## Kapabilitas Terdeteksi (dari library & aset ter-bundle)

Fitur berikut ikut ter-bundle di APK meski belum tentu punya komponen di manifest — sering luput kalau hanya membaca permission.

| ID | Kapabilitas | Skenario | Bukti |
|---|---|---|---|
| CAP-01 | Pemindaian barcode/QR | Buka pemindai, uji kode valid, kode rusak, dan izin kamera ditolak | `barcode-scanning`, `barcode-scanning-common`, `barcode_ssd_mobilenet_v1_dmp25_quant.tflite`, `mlkit_barcode_models` |
| CAP-02 | ML Kit vision (on-device) | Uji inferensi berhasil, gagal, dan saat model belum terunduh | `vision-common`, `vision-interfaces` |
| CAP-03 | Kamera / CameraX | Uji ambil gambar, batal, dan izin kamera ditolak | `androidx.camera_camera-camera2`, `androidx.camera_camera-core`, `androidx.camera_camera-lifecycle`, `androidx.camera_camera-video` |
| CAP-04 | Push notification (FCM) | Uji terima notifikasi foreground, background, dan app mati | `play-services-cloud-messaging` |
| CAP-05 | Database lokal | Uji data tersimpan, migrasi skema, dan mode offline | `androidx.room_room-ktx`, `androidx.room_room-runtime` |
| CAP-06 | Background job (WorkManager) | Uji job jalan saat app mati dan saat baterai hemat | `androidx.work_work-runtime`, `androidx.work_work-runtime-ktx` |

### Aset model on-device

- `assets/mlkit_barcode_models/` — 3 berkas, 0.8 MB (model: barcode_ssd_mobilenet_v1_dmp25_quant.tflite, oned_auto_regressor_mobile.tflite, oned_feature_extractor_mobile.tflite)

Uji juga saat model gagal dimuat / storage penuh.

## Suite Robustness (dari aset app)

| ID | Skenario | Ekspektasi |
|---|---|---|
| ROB-01 | Tampilan offline / tanpa koneksi | Ditangani dengan tampilan yang benar |
| ROB-02 | Mode maintenance server | Ditangani dengan tampilan yang benar |
| ROB-03 | Force update versi usang | Ditangani dengan tampilan yang benar |
| ROB-04 | Layar pengaturan notifikasi | Ditangani dengan tampilan yang benar |
| ROB-05 | Splash screen saat cold start | Ditangani dengan tampilan yang benar |

## Catatan untuk agen otomatis

- Deteksi crash/ANR: pantau `logcat` untuk `FATAL EXCEPTION` & `ANR in`.
- Titik masuk cepat: launcher + deep link di atas.
- Total 4 modul fitur, 0 deep link, 14 permission, 6 kapabilitas, 117 library ter-bundle.
