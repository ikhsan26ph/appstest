#!/usr/bin/env python3
"""Tes uitree.py — berjalan tanpa device, tanpa Appium, tanpa jaringan.

Fixture adalah dump UI asli Driver Hub [STG] yang diambil 14 Agu 2026 dari
Galaxy A52. Jalankan:  python tests/test_uitree.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uitree import (all_texts, center, find_field, find_tappable,  # noqa: E402
                    node_text, norm_label, observe, observed_by_label,
                    parse_elements, rect, value_for)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
LULUS, GAGAL = 0, []


def cek(nama, aktual, harap):
    global LULUS
    if aktual == harap:
        LULUS += 1
    else:
        GAGAL.append(f"{nama}\n      harap : {harap!r}\n      aktual: {aktual!r}")


def fixture(nama):
    with open(os.path.join(FIX, nama), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------
def test_geometri():
    cek("rect", rect("[10,20][110,220]"), (10, 20, 110, 220))
    cek("center", center("[10,20][110,220]"), (60, 120))
    cek("rect kosong", rect(""), None)
    cek("rect None", rect(None), None)
    cek("node_text pakai text", node_text({"text": " Simpan "}), "Simpan")
    cek("node_text fallback desc", node_text({"content-desc": "Back"}), "Back")
    cek("node_text kosong", node_text({}), "")


def test_label_registrasi():
    """Inti modul ini: 6 field yang tanpa asosiasi label akan anonim."""
    els = parse_elements(fixture("registrasi.xml"))
    field = [(e["label"], e["text"]) for e in els if e["editable"]]

    cek("jumlah field Registrasi", len(field), 6)
    cek("label field", [l for l, _ in field], [
        "Nama Sopir *", "Nomor WhatsApp *", "Alamat *",
        "Nomor KTP *", "Nomor SIM *", "Akhir Berlaku SIM *",
    ])
    # placeholder terbaca sebagai `text` walau bukan atribut hint
    cek("placeholder KTP", field[3][1], "Contoh: 3273 0101 0190 0001 (16 digit)")
    # nilai nyata menang atas placeholder (nomor di fixture sudah disamarkan)
    cek("nilai WhatsApp", field[1][1], "6281122334455")


def test_caption_tombol():
    """Tombol = View kosong berisi TextView; caption harus terangkat."""
    els = parse_elements(fixture("registrasi.xml"))
    caption = {e["text"] for e in els if e["clickable"] and not e["editable"]}
    cek("caption Simpan ada", "Simpan" in caption, True)
    # tombol tidak boleh mewarisi label di atasnya (itu noise)
    simpan = next(e for e in els if e["text"] == "Simpan")
    cek("tombol tanpa label", simpan["label"], "")


def test_launcher_punya_id():
    """Kontras: launcher Samsung PUNYA resource-id, app-nya nyaris tidak.
    Fixture ini dulu bernama beranda.xml, padahal isinya home screen HP —
    bukan Beranda aplikasi. Beranda yang asli ada di beranda_app.xml."""
    els = parse_elements(fixture("launcher_samsung.xml"))
    cek("launcher ada elemen", len(els) > 0, True)
    cek("sebagian punya id", any(e["id"] for e in els), True)
    cek("app sendiri nyaris tanpa id",
        [e["id"] for e in parse_elements(fixture("profil_saya.xml")) if e["id"]], [])


def test_all_texts():
    t = all_texts(fixture("registrasi.xml"))
    # tanda wajib menyatu di TextView label yang sama, bukan node terpisah
    cek("teks label ada", "Nomor KTP *" in t, True)
    cek("bintang bukan node sendiri", "*" in t, False)
    cek("teks tombol ada", "Simpan" in t, True)
    cek("tanpa string kosong", "" in t, False)


def test_parse_rusak():
    cek("XML rusak -> []", parse_elements("<bukan xml"), [])
    cek("XML rusak -> set()", all_texts("<bukan xml"), set())


def test_observe_toast():
    """Teks yang baru muncul setelah aksi. Sumber palsu, tanpa device."""
    kosong = '<hierarchy><node bounds="[0,0][10,10]" text="Layar"/></hierarchy>'
    toast = ('<hierarchy><node bounds="[0,0][10,10]" text="Layar"/>'
             '<node bounds="[0,0][10,10]" text="Gagal: 403"/></hierarchy>')
    urutan = [kosong, toast, toast, kosong, kosong]
    sisa = list(urutan)

    def baca():
        return sisa.pop(0) if sisa else kosong

    pesan = observe(baca, before={"Layar"}, window=0.25, interval=0.04)
    cek("toast tertangkap", pesan, ["Gagal: 403"])

    cek("teks lama tidak dilaporkan ulang",
        observe(lambda: kosong, before={"Layar"}, window=0.15, interval=0.04), [])


def test_observe_toast_lebih_panjang_dari_jendela():
    """REGRESI 14 Agu: versi lama mengembalikan `appeared - last`, jadi toast
    yang MASIH tampak saat jendela habis ikut terbuang. Diukur di Galaxy A52,
    'Gagal: 403' bertahan ~3,5 s (Toast.LENGTH_LONG) melewati jendela 3 s —
    hasilnya daftar kosong padahal pesannya terbaca di tiap polling."""
    toast = ('<hierarchy><node bounds="[0,0][10,10]" text="Layar"/>'
             '<node bounds="[0,0][10,10]" text="Gagal: 403"/></hierarchy>')

    cek("toast masih tampak saat jendela habis tetap dilaporkan",
        observe(lambda: toast, before={"Layar"}, window=0.2, interval=0.04),
        ["Gagal: 403"])


def test_value_for_label():
    """Selector layar tampilan: nilai = TextView tepat di bawah label.

    Dump asli Profil Saya (Galaxy A52, 14 Agu). Keenam label ini persis yang
    tertulis di `fields:` oracle_rules.yaml — kalau salah satu berhenti
    resolve, aturan itu diam-diam jadi mati.
    """
    src = fixture("profil_saya.xml")
    cek("Nama Sopir", value_for(src, "Nama Sopir"), "Budi")
    cek("Nomor WhatsApp", value_for(src, "Nomor WhatsApp"), "62 811 2233 4455")
    cek("Alamat", value_for(src, "Alamat"), "Jalan Mawar")
    cek("Nomor SIM", value_for(src, "Nomor SIM"), "11223344556")
    cek("Akhir Berlaku SIM", value_for(src, "Akhir Berlaku SIM"), "26/06/2026")

    # label berikutnya berjarak 42 px, nilainya sendiri 11 px — yang terdekat
    # harus menang, kalau tidak setiap nilai tertukar dengan label sesudahnya
    cek("nilai menang atas label berikutnya",
        value_for(src, "Nomor KTP"), "1234 5600 7788 9911 222")

    cek("label tak ada", value_for(src, "Nomor Rekening"), "")
    cek("xml rusak", value_for("<hierarchy", "Nomor KTP"), "")

    # form berjarak 74 px (label→field), tampilan 11 px — satu ambang, dua layar
    cek("label berbintang di form tetap resolve",
        value_for(fixture("registrasi.xml"), "Nomor KTP"),
        "Contoh: 3273 0101 0190 0001 (16 digit)")


def test_observed_by_label():
    """Bahan siap pakai untuk Oracle.check_screen()."""
    obs = observed_by_label(fixture("profil_saya.xml"),
                            ["Nomor KTP", "Nomor SIM", "Nomor Rekening"])
    cek("bentuk observed", obs, {
        "Nomor KTP": ["1234 5600 7788 9911 222"],
        "Nomor SIM": ["11223344556"],
        "Nomor Rekening": [],          # tidak ada di layar → daftar kosong
    })


def test_nilai_kosong_tidak_tertukar_label():
    """Nilai kosong tidak boleh diisi oleh label berikutnya.

    Di layar Profil Saya jarak label→label berikutnya 95 px, di luar jendela
    80 px, jadi nilai yang dikosongkan menghasilkan '' dengan sendirinya —
    geometri layar itu sudah melindungi.
    """
    src = fixture("profil_saya.xml").replace('text="62 811 2233 4455"', 'text=""')
    cek("nilai kosong → kosong", value_for(src, "Nomor WhatsApp"), "")
    cek("observed ikut kosong",
        observed_by_label(src, ["Nomor WhatsApp", "Alamat"])["Nomor WhatsApp"], [])

    # Tapi pada layar yang lebih rapat jebakannya nyata. XML sintetis di bawah
    # meniru jarak 40 px antar-label — value_for polos menyerahkan 'Alamat'
    # sebagai nilai 'Nomor WhatsApp', dan penjagaan di observed_by_label-lah
    # yang menahannya.
    rapat = ('<hierarchy>'
             '<node bounds="[95,100][344,140]" text="Nomor WhatsApp"/>'
             '<node bounds="[95,180][344,220]" text="Alamat"/>'
             '<node bounds="[95,230][344,270]" text="Jalan Mawar"/>'
             '</hierarchy>')
    cek("layar rapat menipu value_for", value_for(rapat, "Nomor WhatsApp"), "Alamat")
    cek("observed_by_label menolak nilai palsu",
        observed_by_label(rapat, ["Nomor WhatsApp", "Alamat"]),
        {"Nomor WhatsApp": [], "Alamat": ["Jalan Mawar"]})


def test_find_field_dan_tappable():
    els = parse_elements(fixture("registrasi.xml"))
    ktp = find_field(els, "Nomor KTP")
    cek("field KTP ketemu", ktp is not None and ktp["editable"], True)
    cek("field KTP posisinya benar", ktp["label"], "Nomor KTP *")
    cek("field tak ada", find_field(els, "Nomor Rekening"), None)
    # tombol bukan field
    cek("Simpan bukan field", find_field(els, "Simpan"), None)

    simpan = find_tappable(els, "Simpan")
    cek("tombol Simpan ketemu", simpan is not None and simpan["clickable"], True)

    beranda = parse_elements(fixture("beranda_app.xml"))
    lihat = find_tappable(beranda, "Lihat Data Order")
    cek("caption di dalam View kosong terangkat", lihat is not None, True)
    cek("titik tekan di dalam tombol",
        rect(lihat["bounds"])[0] <= lihat["xy"][0] <= rect(lihat["bounds"])[2], True)


def test_tombol_disabled_dilewati():
    """Tombol disabled TETAP clickable=true di UI tree.

    14 Agu: kedua tombol "Isi Penugasan" di Beranda ber-enabled=false karena
    belum giliran order itu — aturan produknya, tombol hanya aktif untuk
    penugasan yang sedang berjalan. Tanpa membaca atribut `enabled`, tap ke
    tombol itu terbaca sebagai "tombol membisu tanpa umpan balik" dan
    dilaporkan sebagai cacat. Itu false positive; pembacanya yang buta.
    """
    beranda = parse_elements(fixture("beranda_app.xml"))
    isi = [e for e in beranda if e["text"] == "Isi Penugasan"]
    cek("dua tombol Isi Penugasan ada", len(isi), 2)
    cek("keduanya clickable", [e["clickable"] for e in isi], [True, True])
    cek("keduanya disabled", [e["enabled"] for e in isi], [False, False])

    cek("find_tappable melewati yang disabled",
        find_tappable(beranda, "Isi Penugasan"), None)
    cek("bisa diminta eksplisit",
        find_tappable(beranda, "Isi Penugasan", include_disabled=True) is not None, True)
    cek("yang aktif tetap ketemu",
        find_tappable(beranda, "Lihat Data Order")["enabled"], True)

    # REGRESI: tombol "Lapor" menumpang di dalam kotak kartu kedua. Dengan
    # aturan caption "paling kecil", kartu itu ikut bernama 'Lapor' dan tombol
    # "Isi Penugasan" kedua hilang dari hasil parse — bug di atas jadi tak
    # terlihat. Keduanya harus muncul sebagai elemen sendiri-sendiri.
    lapor = find_tappable(beranda, "Lapor")
    cek("Lapor tetap punya elemennya sendiri", lapor is not None, True)
    cek("Lapor tidak menelan kartu",
        rect(lapor["bounds"])[0] >= 700, True)


def test_reproducible():
    """Batasan proyek: dua kali jalan harus identik."""
    src = fixture("registrasi.xml")
    cek("parse deterministik", parse_elements(src), parse_elements(src))
    cek("all_texts deterministik", all_texts(src), all_texts(src))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{LULUS} lulus, {len(GAGAL)} gagal")
    for g in GAGAL:
        print(f"  GAGAL {g}")
    sys.exit(1 if GAGAL else 0)
