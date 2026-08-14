#!/usr/bin/env python3
"""Tes oracle.py — berjalan tanpa device, tanpa Appium, tanpa jaringan.

Seluruh string pesan di sini diambil harfiah dari 94.906 string DEX APK
terpasang (Driver Hub [STG] v2.1.1) — bukan karangan tes. Jalankan:
    python tests/test_oracle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oracle import Oracle, Verdict, norm_label  # noqa: E402

RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "oracle_rules.yaml")
LULUS, GAGAL = 0, []


def cek(nama, aktual, harap):
    global LULUS
    if aktual == harap:
        LULUS += 1
    else:
        GAGAL.append(f"{nama}\n      harap : {harap!r}\n      aktual: {aktual!r}")


def oracle():
    return Oracle(RULES)


def ids(verdicts):
    return sorted(v.field for v in verdicts)


# ---------------------------------------------------------------
# ORACLE IMPLISIT — UI
# ---------------------------------------------------------------
def test_toast_kegagalan_terdeteksi():
    o = oracle()
    # bug 14 Agu: Test Push mengembalikan 403, tampil sebagai toast ini saja
    v = o.scan_ui(["Gagal: 403"])
    cek("Gagal: 403 terdeteksi", ids(v), ["gagal_kode_status"])
    cek("severity 403", v[0].severity, "high")
    cek("kind default bug", v[0].kind, "bug")
    cek("aturan v1 'HTTP 4xx' tak dipakai lagi",
        "HTTP (4[0-9]{2}" in open(RULES, encoding="utf-8").read(), False)

    cek("gagal memuat", ids(o.scan_ui(["Gagal memuat profil"])), ["gagal_memuat"])
    cek("gagal menulis", ids(o.scan_ui(["Gagal menyimpan data. Coba lagi."])),
        ["gagal_menulis"])
    cek("respons invalid kritis",
        o.scan_ui(["Respons server tidak valid"])[0].severity, "critical")
    cek("sesi terputus ditandai session",
        o.scan_ui(["Kamu telah logout karena login di perangkat lain"])[0].kind,
        "session")


def test_validasi_tidak_dilaporkan():
    """Allowlist harus menang; kalau tidak, tiap uji nilai batas jadi 'bug'."""
    o = oracle()
    sah = [
        "Nomor KTP harus 16 digit",
        "Nomor KTP tidak boleh kosong",
        "Nomor SIM tidak boleh kosong",
        "Nomor WhatsApp tidak valid",
        "Nama tidak boleh kosong",
        "Alamat tidak boleh kosong",
        "Tanggal berlaku SIM belum dipilih",
        "Foto wajib untuk tahap ini",
        "No. Resi Belum Diisi",
        "Kode tidak sesuai. Periksa kembali.",
        "Izin kamera diperlukan untuk scan QR code / barcode.",
    ]
    cek("validasi bukan cacat", o.scan_ui(sah), [])


def test_empty_state_dan_sukses_bukan_cacat():
    o = oracle()
    cek("empty state diabaikan",
        o.scan_ui(["Belum Ada Notifikasi", "Belum ada penugasan",
                   "Saat ini anda belum memiliki tugas aktif"]), [])
    # PASS-nya Test Push; harus lolos meski mengandung kata yang mirip pola lain
    cek("sukses diabaikan",
        o.scan_ui(["Notifikasi terkirim!", "Data berhasil disinkronkan"]), [])


def test_lingkungan_bukan_bug():
    o = oracle()
    v = o.scan_ui(["Tidak ada koneksi internet"])
    cek("offline dilaporkan", ids(v), ["offline"])
    cek("offline severity info", v[0].severity, "info")
    cek("offline kind lingkungan", v[0].kind, "lingkungan")
    cek("offline dicetak INFO bukan FAIL", str(v[0]).startswith("[INFO]"), True)


def test_urutan_allowlist_menang():
    """'Gagal memuat data' cacat, tapi 'Gagal Sinkron' vs empty state jangan
    saling menimpa. Yang diperiksa: satu teks hanya menghasilkan satu verdict."""
    o = oracle()
    v = o.scan_ui(["Gagal memuat data"])
    cek("satu verdict per teks", len(v), 1)
    cek("teks kosong diabaikan", o.scan_ui(["", "   ", None]), [])
    cek("spasi ganda dinormalkan",
        ids(o.scan_ui(["Gagal:   403"])), ["gagal_kode_status"])


def test_teks_asing_diabaikan():
    """Toast app lain / teks layar biasa tidak boleh memancing laporan."""
    o = oracle()
    cek("teks netral", o.scan_ui(["Beranda", "Penugasan", "Profil",
                                  "Jumat Pon, 14 Agustus 2026"]), [])


# ---------------------------------------------------------------
# ORACLE IMPLISIT — LOGCAT
# ---------------------------------------------------------------
def test_logcat_crash_saja():
    o = oracle()
    log = [
        "E AndroidRuntime: FATAL EXCEPTION: main",
        "E ActivityManager: ANR in com.phbid_darat.supir.stg",
        "F DEBUG   : signal 11 (SIGSEGV), code 1",
    ]
    cek("tiga sinyal crash", len(o.scan_logcat(log)), 3)
    cek("kind crash", {v.kind for v in o.scan_logcat(log)}, {"crash"})

    # aturan v1 yang sengaja dibuang: di HP fisik logcat bercampur seluruh
    # sistem, ketiganya memanen baris milik app lain
    bising = [
        "W System : java.lang.NullPointerException at com.samsung.foo",
        "E SQLiteLog: (283) SQLiteException in com.google.android.gms",
        "D OkHttp  : HTTP 404 dari layanan lain",
    ]
    cek("bising sistem tidak dilaporkan", o.scan_logcat(bising), [])


def test_logcat_kredensial():
    o = oracle()
    cek("kode akses 6 digit bocor",
        ids(o.scan_logcat(["D Auth: verify-pin kode=738201 terkirim"])),
        ["kode_akses_bocor"])
    cek("angka biasa bukan kredensial",
        o.scan_logcat(["I Sys: batch 918273 titik terkirim"]), [])
    cek("nilai disamarkan di laporan",
        "738201" in str(o.scan_logcat(["D Auth: kode=738201"])[0]), False)


def test_aturan_mati_dilaporkan():
    """'Tidak ada temuan' tidak boleh tertukar dengan 'tidak diperiksa'."""
    os.environ.pop("QA_PHONE", None)
    cek("tanpa env, aturan nomor WA mati",
        any("nomor_wa_bocor" in x for x in oracle().inactive), True)

    os.environ["QA_PHONE"] = "6285155070869"
    o = oracle()
    cek("dengan env, aturan hidup", o.inactive, [])
    # nomor yang sama muncul 3 bentuk di log; ketiganya harus tertangkap
    for bentuk in ["6285155070869", "+6285155070869", "085155070869"]:
        cek(f"nomor WA bentuk {bentuk}",
            ids(o.scan_logcat([f"D Api: request-pin phone={bentuk}"])),
            ["nomor_wa_bocor"])
    os.environ.pop("QA_PHONE", None)


# ---------------------------------------------------------------
# ORACLE EKSPLISIT — LAYAR BERBASIS LABEL
# ---------------------------------------------------------------
def test_norm_label():
    cek("tanda wajib dibuang", norm_label("Nomor KTP *"), "nomor ktp")
    cek("spasi ganda", norm_label("Nomor  KTP"), "nomor ktp")
    cek("beda huruf besar", norm_label("NOMOR ktp"), "nomor ktp")
    cek("kosong aman", norm_label(None), "")


def test_duplikat_pengirim_bentuk_sebenarnya():
    """Bug 14 Agu: duplikat terjadi DI DALAM satu nilai, bukan sebagai dua node.

    String di bawah ini harfiah dari dump layar detail penugasan
    (order TSH-ORD5293580177) — 56 karakter, nama pengirim yang sama dua kali
    dipisah koma. `unique_list` mustahil menangkapnya karena node-nya cuma satu.
    """
    o = oracle()
    asli = {"Pengirim": ["PT. Ternak Ikan Buntal (IK), PT. Ternak Ikan Buntal (IK)"]}
    v = o.check_screen("detail_penugasan", asli)
    cek("duplikat dalam satu nilai terdeteksi", ids(v), ["no_duplicate_sender"])
    cek("bagian berulang disebut",
        "PT. Ternak Ikan Buntal (IK)" in v[0].message, True)

    cek("pengirim tunggal lolos",
        o.check_screen("detail_penugasan",
                       {"Pengirim": ["PT. Ternak Ikan Buntal (IK)"]}), [])
    cek("dua pengirim berbeda lolos",
        o.check_screen("detail_penugasan",
                       {"Pengirim": ["PT. Sinar Jaya, CV. Maju"]}), [])

    cek("nilai kosong kena min_count",
        ids(o.check_screen("detail_penugasan", {"Pengirim": []})),
        ["sender_not_empty"])


def test_check_screen_berbasis_label():
    o = oracle()

    # inti perubahan kontrak: label dari dump berbintang, YAML tidak
    berbintang = {"Nomor KTP *": ["3273010101900001"]}
    cek("label berbintang tetap cocok",
        o.check_screen("registrasi", berbintang), [])
    cek("field hilang ketahuan",
        ids(o.check_screen("registrasi", {})), ["field_wajib_lengkap"])


def test_rantai_uitree_ke_oracle():
    """Rantai Tahap 2 di atas dump asli: baca layar → kenali → periksa invariant.

    Ini yang menggantikan "selector nyata": tidak ada resource-id yang bisa
    ditulis, jadi yang diverifikasi adalah label di aturan benar-benar
    menemukan nilainya di dump asli.
    """
    from uitree import all_texts, observed_by_label
    fix = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def baca(nama):
        with open(os.path.join(fix, nama), encoding="utf-8") as f:
            return f.read()

    o = oracle()
    profil = baca("profil_saya.xml")
    cek("Profil Saya dikenali", o.identify(all_texts(profil)), ["profil_saya"])
    cek("Registrasi dikenali",
        o.identify(all_texts(baca("registrasi.xml"))), ["registrasi"])
    cek("home screen HP bukan layar app",
        o.identify(all_texts(baca("launcher_samsung.xml"))), [])

    obs = observed_by_label(profil, ["Nomor KTP"])
    cek("nilai KTP terbaca dari dump", obs["Nomor KTP"],
        ["1234 5600 7788 9911 222"])
    cek("invariant layar lolos", o.check_screen("profil_saya", obs), [])

    # seluruh label di `fields:` harus resolve; kalau tidak, aturannya mati diam
    hilang = [f["label"] for f in o.fields.values()
              if not observed_by_label(profil, [f["label"]])[f["label"]]]
    cek("semua label fields resolve di Profil Saya", hilang, [])


def test_identify_layar_tanpa_activity():
    """Manifest cuma punya satu activity; layar dikenali dari teks."""
    o = oracle()
    cek("registrasi dikenali",
        o.identify({"Registrasi", "Nomor KTP *", "Simpan"}), ["registrasi"])
    cek("layar asing tidak dikenali", o.identify({"Beranda", "Penugasan"}), [])


# ---------------------------------------------------------------
# PAGAR CRAWLER
# ---------------------------------------------------------------
def test_aksi_terlarang():
    o = oracle()
    for teks in ["Keluar Akun", "Berangkat Muat", "Simpan", "Lapor",
                 "Tandai Sudah Dibaca", "Setujui", "Ya, ini saya"]:
        cek(f"terlarang: {teks}", o.is_forbidden(teks), True)
    for teks in ["Beranda", "Lihat Profil Saya", "Refresh", ""]:
        cek(f"boleh: {teks!r}", o.is_forbidden(teks), False)


# ---------------------------------------------------------------
def test_reproducible():
    """Batasan proyek: dua kali jalan memberi hasil identik."""
    a, b = oracle(), oracle()
    pesan = ["Gagal: 403", "Nomor KTP harus 16 digit", "Tidak ada koneksi internet"]
    cek("scan_ui deterministik",
        [str(v) for v in a.scan_ui(pesan)], [str(v) for v in b.scan_ui(pesan)])
    cek("generate deterministik",
        list(a.generate("ktp")), list(b.generate("ktp")))


def test_kontrak_lama_utuh():
    """Case/Verdict/generate/judge tidak boleh berubah tanpa persetujuan."""
    o = oracle()
    c = next(iter(o.generate("ktp")))
    cek("Case punya 4 field", (c.field, c.expect_accept, bool(c.reason)),
        ("ktp", True, True))
    cek("judge sesuai", o.judge("ktp", c, True).ok, True)
    cek("judge nilai salah diterima", o.judge("ktp", c, False).ok, False)
    cek("Verdict default kind", Verdict(False, "x", "y", "z").kind, "bug")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{LULUS} lulus, {len(GAGAL)} gagal")
    for g in GAGAL:
        print(f"  GAGAL {g}")
    sys.exit(1 if GAGAL else 0)
