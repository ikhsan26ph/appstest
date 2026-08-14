#!/usr/bin/env python3
"""Tes uitree.py — berjalan tanpa device, tanpa Appium, tanpa jaringan.

Fixture adalah dump UI asli Driver Hub [STG] yang diambil 14 Agu 2026 dari
Galaxy A52. Jalankan:  python tests/test_uitree.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uitree import (all_texts, center, node_text, observe,  # noqa: E402
                    parse_elements, rect)

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
    # nilai nyata menang atas placeholder
    cek("nilai WhatsApp", field[1][1], "6285155070869")


def test_caption_tombol():
    """Tombol = View kosong berisi TextView; caption harus terangkat."""
    els = parse_elements(fixture("registrasi.xml"))
    caption = {e["text"] for e in els if e["clickable"] and not e["editable"]}
    cek("caption Simpan ada", "Simpan" in caption, True)
    # tombol tidak boleh mewarisi label di atasnya (itu noise)
    simpan = next(e for e in els if e["text"] == "Simpan")
    cek("tombol tanpa label", simpan["label"], "")


def test_beranda_punya_id():
    """Layar launcher punya resource-id; pastikan id ikut terbaca."""
    els = parse_elements(fixture("beranda.xml"))
    cek("beranda ada elemen", len(els) > 0, True)
    cek("sebagian punya id", any(e["id"] for e in els), True)


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
