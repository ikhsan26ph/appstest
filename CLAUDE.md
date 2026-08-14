# Proyek: AI-Driven QA untuk Aplikasi Android (appstest)

## Tujuan
Tool untuk **QA fungsional otomatis** aplikasi Android buatan sendiri: serahkan
sebuah `.apk`, lalu sistem (1) menganalisis strukturnya secara statis untuk
menyeed test plan, dan (2) menjalankan agen eksplorasi otomatis (Appium sebagai
"tangan", Claude sebagai "otak") yang menavigasi app, menguji fitur, dan
mendeteksi crash/ANR.

Ekosistem `phbid` kemungkinan punya beberapa app (supir/customer/merchant),
maka semua tooling harus **app-agnostic**: identitas, permission, deep link, dan
modul fitur selalu diturunkan otomatis dari tiap APK — jangan hardcode.

## Lingkungan
- OS: Ubuntu (Linux). Semua tooling Android native di sini.
- Claude Code CLI sudah terpasang.
- Folder proyek: `/home/icun/Project/appstest`

## Struktur & file
- `apk_analyzer.py` — analisis statis APK (androguard) → JSON + draft test plan.
  Jalankan: `python apk_analyzer.py <apk> --out analysis`
- `qa_agent.py` — agen eksplorasi Appium↔Claude (loop screenshot/UI → aksi →
  deteksi crash → laporan).
- `analysis/` — output analyzer per app (`<package>.json`, `<package>_testplan.md`).
- `requirements.txt` — dependensi Python.

## Konvensi
- Environment **staging** (package berakhiran `.stg`) aman untuk uji end-to-end.
- Deteksi crash: pantau `logcat` untuk `FATAL EXCEPTION` dan `ANR in`.
- Agen tidak boleh melakukan aksi destruktif (hapus akun, logout) kecuali goal
  memintanya eksplisit.
- Model default agen: `claude-sonnet-5` (bisa diubah via env `CLAUDE_MODEL`).

## App yang sudah dianalisis
- **Driver Hub [STG]** (`com.phbid_darat.supir.stg`, v2.1.1) — app supir.
  Modul: auth (login PIN + login-approval lintas device), tracking (lokasi batch,
  penugasan, foreground service), notification (FCM), profile. Punya scanner
  barcode ML Kit. Deep link: `driverhubstg://fallback`,
  `https://driver-hub-stg.onelink.me`.

## Cara membantu (untuk Claude Code)
1. Untuk APK baru: jalankan `apk_analyzer.py` dulu, review test plan yang
   dihasilkan, baru rancang skenario agen.
2. Saat menyiapkan lingkungan Android, tampilkan perintah dan minta persetujuan
   sebelum menjalankan (install SDK/Appium, buat AVD, dsb.).
3. Jaga agar analyzer & harness tetap app-agnostic.
