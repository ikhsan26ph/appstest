# appstest — AI-Driven QA untuk Aplikasi Android

Serahkan sebuah `.apk` → dapatkan (1) analisis statis + draft test plan, dan
(2) agen yang mengeksplorasi app secara otomatis (Appium + Claude) sambil
mendeteksi crash. App-agnostic: cocok untuk beberapa app dalam satu ekosistem.

## Isi
| File | Fungsi |
|---|---|
| `apk_analyzer.py` | Analisis statis APK → `analysis/<package>.json` + test plan `.md` |
| `qa_agent.py` | Agen eksplorasi otomatis (Appium ↔ Claude) → `qa_report.json` |
| `requirements.txt` | Dependensi Python |
| `CLAUDE.md` | Konteks proyek untuk Claude Code |

---

## Setup di Ubuntu

Verifikasi tiap perintah dengan mesinmu; versi paket bisa berbeda. Kamu bisa
minta Claude Code menjalankan & mengonfigurasi langkah-langkah ini secara
interaktif.

### 1. Python & dependensi
```bash
sudo apt update && sudo apt install -y python3 python3-pip
pip install -r requirements.txt
```

### 2. Analisis statis (tidak butuh emulator)
```bash
python3 apk_analyzer.py /path/ke/app.apk --out analysis
```
Menghasilkan `analysis/<package>.json` dan `analysis/<package>_testplan.md`.

### 3. Lingkungan Android (untuk agen dinamis)
Butuh: JDK, Android SDK/platform-tools (`adb`), emulator (atau HP fisik), dan
Node.js untuk Appium.
```bash
sudo apt install -y openjdk-17-jdk adb
# Android SDK cmdline-tools + emulator: lewat Android Studio, atau unduh
#   "Command line tools only" dari developer.android.com, lalu:
#   sdkmanager "platform-tools" "emulator" "system-images;android-34;google_apis;x86_64"
#   avdmanager create avd -n test -k "system-images;android-34;google_apis;x86_64"
# Node.js + Appium 2:
sudo apt install -y nodejs npm
npm install -g appium
appium driver install uiautomator2
```
Catatan: emulator butuh virtualisasi (KVM) aktif. Cek: `kvm-ok`.

### 4. Jalankan emulator & Appium
```bash
emulator -avd test &            # atau colok HP dengan USB debugging aktif
adb devices                     # pastikan 1 device muncul
appium                          # server di http://127.0.0.1:4723
```

### 5. Jalankan agen QA
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 qa_agent.py --apk /path/ke/app.apk \
    --goal "Uji alur login PIN lalu jelajahi menu utama" \
    --max-steps 25
```
Atau jika app sudah terinstal:
```bash
python3 qa_agent.py --package com.phbid_darat.supir.stg \
    --activity com.phbid_darat.supir.MainActivity --max-steps 30
```
Hasil: `qa_report.json` (langkah, layar unik, crash/ANR).

### 6. Uji deep link (opsional, cepat)
```bash
adb shell am start -a android.intent.action.VIEW -d "driverhubstg://fallback"
```

---

## Alur kerja disarankan
1. `apk_analyzer.py` → baca test plan → tentukan `--goal` per suite.
2. Jalankan `qa_agent.py` per goal (auth, tracking, dst.).
3. Review `qa_report.json`; reproduksi crash secara manual bila ada.

## Variabel lingkungan
| Var | Default | Fungsi |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | wajib untuk agen |
| `CLAUDE_MODEL` | `claude-sonnet-5` | model otak agen |
| `APPIUM_URL` | `http://127.0.0.1:4723` | alamat server Appium |
