#!/usr/bin/env python3
"""Tes formtest.py — seluruh keputusan diuji tanpa device.

Transport-nya palsu: kelas IOPalsu di bawah meniru Appium dengan menyajikan
dump XML yang sudah disiapkan. Yang diuji bukan kemampuan mengetik, tapi
kemampuan MEMUTUSKAN diterima/ditolak — bagian yang paling mudah salah.

Jalankan:  python tests/test_formtest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from formtest import (BUNTU, DITERIMA, DITOLAK, GAGAL,  # noqa: E402
                      FormRunner, Observation, classify, periksa_masukan)
from oracle import Oracle  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "oracle_rules.yaml")
FIX = os.path.join(ROOT, "tests", "fixtures")
LULUS, GAGAL_TES = 0, []


def cek(nama, aktual, harap):
    global LULUS
    if aktual == harap:
        LULUS += 1
    else:
        GAGAL_TES.append(f"{nama}\n      harap : {harap!r}\n      aktual: {aktual!r}")


def oracle():
    return Oracle(RULES)


def fixture(nama):
    with open(os.path.join(FIX, nama), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------
# KEPUTUSAN
# ---------------------------------------------------------------
def test_ditolak_lewat_toast_validasi():
    o = oracle()
    obs = Observation(muncul=["Nomor KTP harus 16 digit"],
                      layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    outcome, bukti = classify(o, obs)
    cek("validasi = ditolak", outcome, DITOLAK)
    cek("bukti menyebut pesannya", "Nomor KTP harus 16 digit" in bukti, True)


def test_diterima_lewat_perpindahan_layar():
    o = oracle()
    obs = Observation(muncul=[], layar_sebelum=["registrasi"], layar_sesudah=[])
    cek("layar berpindah = diterima", classify(o, obs)[0], DITERIMA)


def test_kegagalan_teknis_bukan_penolakan():
    """Error server tidak boleh tercatat sebagai 'validasi bekerja'."""
    o = oracle()
    obs = Observation(muncul=["Gagal: 500"],
                      layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    cek("toast kegagalan = gagal teknis", classify(o, obs)[0], GAGAL)

    # kegagalan diperiksa LEBIH DULU: kalau app sempat memindahkan layar
    # sekalipun, pesan kegagalannya yang lebih penting
    obs2 = Observation(muncul=["Gagal memuat profil"],
                      layar_sebelum=["registrasi"], layar_sesudah=[])
    cek("kegagalan menang atas perpindahan", classify(o, obs2)[0], GAGAL)


def test_diam_tidak_dipaksa_jadi_diterima():
    """Inti rancangan: app membisu bukan berarti nilainya diterima."""
    o = oracle()
    obs = Observation(muncul=[], layar_sebelum=["registrasi"],
                      layar_sesudah=["registrasi"])
    outcome, bukti = classify(o, obs)
    cek("diam = tidak konklusif", outcome, BUNTU)
    cek("bukti menjelaskan", "tidak ada toast" in bukti, True)


def test_hanya_grup_bertanda_yang_memutuskan():
    """`ui_expected` bukan satu kelas. Empty state dan permintaan izin wajar
    muncul tapi tidak berkata apa pun tentang nilai yang baru diketik; hanya
    grup ber-`menandakan` yang boleh memutuskan."""
    o = oracle()
    diam = Observation(muncul=["Belum Ada Notifikasi", "14/8, 13.52"],
                       layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    cek("empty-state tidak memutuskan apa pun", classify(o, diam)[0], BUNTU)

    izin = Observation(muncul=["Izin kamera diperlukan untuk scan QR code / barcode."],
                       layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    cek("permintaan izin tidak memutuskan", classify(o, izin)[0], BUNTU)

    sukses = Observation(muncul=["Data berhasil disinkronkan"],
                         layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    outcome, bukti = classify(o, sukses)
    cek("konfirmasi sukses = diterima", outcome, DITERIMA)
    cek("bukti menyebut konfirmasi", bukti.startswith("konfirmasi:"), True)

    tolak = Observation(muncul=["Alamat tidak boleh kosong"],
                        layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    cek("validasi tetap = ditolak", classify(o, tolak)[0], DITOLAK)


# ---------------------------------------------------------------
# PEMBACAAN ULANG ISI FIELD
# ---------------------------------------------------------------
def test_periksa_masukan():
    PH = "Contoh: 3273 0101 0190 0001 (16 digit)"
    cek("nilai utuh", periksa_masukan("1234567890123456", "1234567890123456", PH), None)
    cek("field kosong menampilkan placeholder",
        periksa_masukan("", PH, PH), None)

    dipotong = periksa_masukan("1234567890123456789", "1234 5678 9012 3456", PH)
    cek("pemotongan terdeteksi", "memotong ketikan" in dipotong, True)
    cek("panjangnya disebut", "19 → 16" in dipotong, True)

    beda = periksa_masukan("abc", "xyz", PH)
    cek("isi menyimpang terdeteksi", "tidak sama" in beda, True)


def test_format_tampilan_bukan_perubahan_nilai():
    """Field KTP menyisipkan spasi tiap 4 digit. Terukur di device: ketikan
    '111111111111111' terbaca '1111 1111 1111 111'. Tanpa normalisasi, tiap
    kasus uji akan dilaporkan 'isi tidak sama' — bising yang menenggelamkan
    pemotongan sungguhan."""
    PH = "Contoh: 3273 0101 0190 0001 (16 digit)"
    pesan = periksa_masukan("111111111111111", "1111 1111 1111 111", PH)
    cek("format bukan alarm", "nilainya sendiri utuh" in pesan, True)
    cek("bukan dilaporkan sebagai pemotongan", "memotong" in pesan, False)


def test_pesan_validasi_ganda_dipilah_per_field():
    """App memunculkan beberapa pesan validasi sekaligus. Terukur: satu submit
    memberi 'Nomor KTP harus 16 digit' DAN 'Tanggal berlaku SIM belum dipilih'.
    Bukti yang dicatat harus milik field yang sedang diuji."""
    o = oracle()
    obs = Observation(
        muncul=["Nomor KTP harus 16 digit", "Tanggal berlaku SIM belum dipilih"],
        layar_sebelum=["registrasi"], layar_sesudah=["registrasi"])
    outcome, bukti = classify(o, obs, label="Nomor KTP")
    cek("tetap ditolak", outcome, DITOLAK)
    cek("bukti milik field yang diuji", bukti.startswith("validasi: 'Nomor KTP"), True)
    cek("pesan lain tetap dicatat, tidak disembunyikan",
        "pesan field lain" in bukti, True)


# ---------------------------------------------------------------
# PELAKSANA, DENGAN TRANSPORT PALSU
# ---------------------------------------------------------------
class IOPalsu:
    """Meniru Appium: menyajikan dump yang sudah disiapkan, mencatat aksi."""

    def __init__(self, layar: list[str]):
        self.layar = list(layar)          # antrean XML yang akan disajikan
        self.aksi: list[tuple] = []
        self.nilai: dict[int, str] = {}

    def source(self):
        return self.layar[0] if len(self.layar) == 1 else self.layar.pop(0)

    def editable(self, n):
        return f"el{n}"

    def clear(self, el):
        self.aksi.append(("clear", el))

    def type_into(self, el, teks):
        self.aksi.append(("type", el, teks))

    def tap(self, x, y):
        self.aksi.append(("tap", x, y))

    def hide_keyboard(self):
        self.aksi.append(("hide_keyboard",))


def test_placeholder_direkam_dari_form_kosong():
    io = IOPalsu([fixture("registrasi.xml")])
    r = FormRunner(io, oracle())
    ph = r.catat_placeholder(bersihkan=False)
    cek("enam placeholder terekam", len(ph), 6)
    cek("placeholder KTP",
        ph["Nomor KTP *"], "Contoh: 3273 0101 0190 0001 (16 digit)")


def test_index_field_ikut_urutan_dokumen():
    """XPath (//EditText)[n] harus menunjuk field yang sama dengan parse_elements."""
    io = IOPalsu([fixture("registrasi.xml")])
    r = FormRunner(io, oracle())
    cek("Nama Sopir index 0", r._index_field("Nama Sopir"), 0)
    cek("Nomor KTP index 3", r._index_field("Nomor KTP"), 3)
    cek("Akhir Berlaku SIM index 5", r._index_field("Akhir Berlaku SIM"), 5)
    try:
        r._index_field("Nomor Rekening")
        cek("field asing harus error", "tidak error", "LookupError")
    except LookupError:
        cek("field asing harus error", "LookupError", "LookupError")


def test_isi_mengosongkan_lalu_mengetik():
    io = IOPalsu([fixture("registrasi.xml")])
    r = FormRunner(io, oracle())
    r.isi("Nomor KTP", "1234567890123456")
    cek("urutan aksi", [a[0] for a in io.aksi],
        ["clear", "type", "hide_keyboard"])
    cek("mengetik ke element yang benar", io.aksi[1][1], "el3")
    cek("nilai tidak lewat shell", io.aksi[1][2], "1234567890123456")


def test_submit_menolak_tombol_hilang():
    """Kalau tombolnya tidak ada atau disabled, jangan pura-pura menekan."""
    io = IOPalsu([fixture("profil_saya.xml")])   # layar tanpa tombol Simpan
    r = FormRunner(io, oracle())
    try:
        r.submit()
        cek("tombol hilang harus error", "tidak error", "LookupError")
    except LookupError:
        cek("tombol hilang harus error", "LookupError", "LookupError")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{LULUS} lulus, {len(GAGAL_TES)} gagal")
    for g in GAGAL_TES:
        print(f"  GAGAL {g}")
    sys.exit(1 if GAGAL_TES else 0)
