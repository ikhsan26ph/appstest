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
