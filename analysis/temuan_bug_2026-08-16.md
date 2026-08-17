# Temuan Bug — Driver Hub [STG] v2.2.0 (16 Agu 2026, Galaxy A52s)

Konteks run: menyelesaikan penugasan end-to-end via agen Appium (akun Ikhsan,
`6283830011881`). Tiga order TSH diselesaikan penuh; temuan di bawah tertangkap
oracle (`oracle_rules.yaml`) selama run tersebut.

## 1. Pengirim duplikat di Detail Tracking (data, HIGH)
- **Order:** `TSH-ORD5293580177` (Gresik → Balikpapan, tahap "Selesai Muat").
- **Gejala:** field Pengirim dirender `PT. Ternak Ikan Buntal (IK), PT. Ternak
  Ikan Buntal (IK)` — satu TextView berisi nilai dobel.
- **Bukti:** invariant `no_duplicate_sender` (unique_parts) FAIL; sama persis
  dengan bug 14 Agu di v2.1.1 — **belum diperbaiki di v2.2.0**.
- **Catatan penting:** order sebelahnya (`TSH-ORD5293581224`, rute & pengirim
  sama) tampil TUNGGAL — cacatnya per-order, kemungkinan di data/join sisi
  server, bukan di kode render client.

## 2. Daftar Beranda basi sesudah order keluar (UI, MEDIUM)
- **Gejala:** sesudah sebuah order selesai dan hilang dari daftar, kartu
  terakhir tampil terpotong (judul+tanggal saja, tombol tak terjangkau) dan
  daftar mentok tidak bisa discroll ke bawah.
- **Pemulihan:** pull-to-refresh menyembuhkan layout dan sekaligus
  menyegarkan state enabled tombol "Isi Penugasan".
- **Dampak:** pengguna nyata bisa mengira ordernya "hilang tombolnya" sampai
  kebetulan me-refresh.

## 3. (Minor) Keterangan tidak selalu tersedia di form tahap
- Form foto+Simpan pada beberapa tahap tidak memuat field Keterangan yang
  bisa diketik (elemen tidak ditemukan padahal di tahap lain ada). Belum
  dipastikan by-design atau bug; dicatat untuk dicek desainnya.

---

## Hasil run penyelesaian LKL (16 Agu, lanjutan)
Tidak ada temuan cacat baru dari oracle selama 6 order diselesaikan; nol
crash/ANR di logcat sepanjang seluruh run.

Perilaku yang DIKONFIRMASI BENAR (bukan bug, tercatat sebagai pengetahuan):
- Tahap ber-resi (sub-tipe LKL-LTL dan LKL-AFR) menolak nilai karangan:
  `Resi tidak dikenal: LKL892889` — resi divalidasi server terhadap data
  order. Pola pesan ini sudah dimasukkan ke `oracle_rules.yaml`.
- Order FTL (LKL-FTL*) dan TSH tidak meminta resi; siklus tahapnya lolos
  penuh dengan foto + Simpan / tombol aksi.
- Order multi-shipment (mis. LKL-LTL5377813262, 2 shipment / 3 resi):
  tahap berjalan hanya menerima resi shipment gilirannya, dan pesan
  penolakan MENYEBUT resi yang salah — dipakai `tms_web.py` + loop koreksi
  untuk menyelesaikan seluruh siklus (shipment 1 muat→bongkar, lalu
  shipment 2 muat→bongkar) tanpa campur tangan manusia.

Run 16 Agu (sesi lanjutan) menyelesaikan SELURUH 9 order; Beranda berakhir
di empty state "Belum ada penugasan". Nol crash/ANR di semua sesi.

## 5. Order FTL MULTIPICKUP terlambat tampil di daftar tugas app (±1 jam) (HIGH → direvisi)
- **Kronologi 17 Agu (uji diferensial, dua order seed identik kecuali
  tipePengiriman):**
  - `FTL6940683725` (NORMAL): tampil di app DALAM HITUNGAN DETIK + push FCM.
  - `FTL6941601827` (MULTIPICKUP, dibuat ±11:37): push FCM langsung
    terkirim & diterima, web ASSIGNED — tapi daftar tugas app KOSONG
    selama ±1 jam (4x pull-to-refresh + relaunch, sopir & PIC terverifikasi
    identik dengan order NORMAL).
  - ±12:40: order multipickup KEDUA (`FTL6942920480`) dibuat — dan KEDUA
    order multipickup tiba-tiba tampil bersamaan.
- **Revisi kesimpulan:** bukan hilang permanen, tapi TERLAMBAT tampil.
  Konsisten dengan cache/materialisasi tertunda di jalur
  `GET api/v1/mobile/penugasan` (apicore-staging) khusus tipe multipickup,
  atau flush yang baru terpicu oleh penugasan berikutnya. Jalur push dan
  jalur list tetap tidak konsisten — itu inti cacatnya.
- **Dampak:** di jendela keterlambatan, sopir dinotifikasi ada tugas,
  membuka app, dan tidak menemukan apa-apa.
- **Sesudah tampil, siklusnya normal:** `FTL6942920480` diselesaikan penuh
  6 tahap (muat per titik ×2 + bongkar) tanpa kendala.

## 4. POST /api/orders tidak mendenormalisasi nama kota (API/UX, MEDIUM)
- **Ditemukan 17 Agu** saat menyemai order via API (pilot `LTL6933610417`):
  `POST /orders` hanya mewajibkan `kotaAsalId`/`kotaTujuanId`; field
  `kotaAsalName`/`kotaTujuanName` diterima kosong dan backend TIDAK
  mengisinya dari ID.
- **Dampak di app supir:** bagian lokasi kartu penugasan tampil "-" —
  order sah tapi tidak terbaca rutenya oleh sopir.
- **Perbaikan sementara:** `PATCH /orders/{id}` dengan kedua nama (berhasil);
  seeder kini selalu mengirim nama saat create.
- **Saran produk:** backend sebaiknya menurunkan nama dari ID (satu sumber
  kebenaran), atau menolak order tanpa nama kota.
