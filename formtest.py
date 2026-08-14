#!/usr/bin/env python3
"""Penguji field: Oracle.generate() → ketik → baca reaksi app → Oracle.judge().

Bagian tersulitnya bukan mengetik, tapi memutuskan **diterima atau ditolak**.
Rancangan di sini berdiri di atas empat pengukuran di Driver Hub [STG]:

1. Penolakan selalu lewat TOAST. Tombol "Simpan" ber-`enabled=true` bahkan pada
   form yang seluruhnya kosong, jadi disable-state tidak bisa dipakai sebagai
   gerbang validasi — nilainya selalu sama.
2. Toast kegagalan hidup ~3,5 detik dan toast validasi ~2 detik, jadi
   pembacanya harus polling (uitree.observe), bukan sekali dump.
3. Penerimaan terlihat sebagai PERPINDAHAN LAYAR: form Registrasi hilang.
4. Field yang kosong menampilkan placeholder sebagai `text`, jadi pembacaan
   ulang isi field harus membandingkan dengan placeholder — kalau tidak, form
   kosong terbaca "berisi 'Contoh: Budi Santoso'".

Ada hasil KEEMPAT yang sengaja tidak dipaksakan jadi ya/tidak: `tidak_konklusif`
— app diam, layar tidak pindah, tidak ada toast. Memaksanya jadi "diterima"
akan memberi PASS palsu, dan itu persis kelas kesalahan yang sudah dua kali
menipu kami hari ini.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field as dc_field

import uitree
from oracle import Case, Oracle, Verdict

DITERIMA = "diterima"
DITOLAK = "ditolak"
GAGAL = "gagal_teknis"
BUNTU = "tidak_konklusif"


@dataclass
class Observation:
    """Apa yang terlihat sesudah satu aksi submit."""
    muncul: list[str]                     # teks baru (uitree.observe)
    layar_sebelum: list[str]              # hasil Oracle.identify sebelum aksi
    layar_sesudah: list[str]
    isi_field: str = ""                   # pembacaan ulang field yang diuji
    diketik: str = ""


@dataclass
class Hasil:
    """Satu kasus uji beserta jejak buktinya."""
    case: Case
    outcome: str
    bukti: str
    verdict: Verdict | None = None
    catatan: list[str] = dc_field(default_factory=list)

    def __str__(self) -> str:
        v = f"  {self.verdict}" if self.verdict else "  (tidak dinilai)"
        return f"[{self.outcome:16}] {self.case.value[:24]!r:28} {self.bukti}\n{v}"


# ---------------------------------------------------------------
# KEPUTUSAN — murni, bisa diuji tanpa device
# ---------------------------------------------------------------
def classify(oracle: Oracle, obs: Observation, label: str = "") -> tuple[str, str]:
    """(outcome, bukti). Urutannya penting dan tidak boleh ditukar.

    Toast diperiksa lebih dulu daripada perpindahan layar: app bisa saja
    menampilkan pesan lalu tetap berpindah, dan pesan itulah yang lebih
    informatif. Kegagalan teknis dipisahkan dari penolakan validasi supaya
    error server tidak tercatat sebagai "validasi bekerja".

    `label` = field yang sedang diuji. Perlu karena app ini menampilkan
    BEBERAPA pesan validasi sekaligus — terukur 14 Agu: satu submit
    memunculkan 'Nomor KTP harus 16 digit' dan 'Tanggal berlaku SIM belum
    dipilih' berbarengan. Tanpa petunjuk label, bukti yang tercatat bisa
    milik field tetangga dan laporannya menyesatkan.
    """
    pesan = [(teks, oracle.match_ui(teks)) for teks in obs.muncul]

    for teks, hit in pesan:
        if hit and hit[0] == "forbidden":
            return GAGAL, f"pesan kegagalan: {teks!r}"

    # hanya grup ber-`menandakan` yang boleh memutuskan. Sisa ui_expected —
    # empty state, permintaan izin — memang wajar muncul tapi tidak berkata
    # apa pun tentang nilai yang baru diketik.
    penolakan = [t for t, hit in pesan
                 if hit and hit[0] == "expected"
                 and hit[1].get("menandakan") == "penolakan"]
    if penolakan:
        milik_field = [t for t in penolakan
                       if uitree.norm_label(label) and uitree.norm_label(label) in
                       uitree.norm_label(t)]
        pilih = milik_field[0] if milik_field else penolakan[0]
        lain = [t for t in penolakan if t != pilih]
        bukti = f"validasi: {pilih!r}"
        if lain:
            bukti += f" (+{len(lain)} pesan field lain: {lain})"
        return DITOLAK, bukti

    for teks, hit in pesan:
        if hit and hit[0] == "expected" and hit[1].get("menandakan") == "keberhasilan":
            return DITERIMA, f"konfirmasi: {teks!r}"

    if obs.layar_sebelum and obs.layar_sesudah != obs.layar_sebelum:
        return DITERIMA, f"layar berpindah: {obs.layar_sebelum} → {obs.layar_sesudah}"

    return BUNTU, "tidak ada toast, layar tidak berpindah"


def periksa_masukan(diketik: str, terbaca: str, placeholder: str) -> str | None:
    """Apakah field menyimpan persis yang diketik.

    Ini sinyal yang tidak ada di rencana awal dan ternyata paling tajam untuk
    bug KTP: kalau app memfilter di level ketikan (maxLength), 19 digit akan
    tersimpan jadi 16 dan submit-nya lolos wajar. Kalau tersimpan utuh 19,
    berarti tidak ada filter sama sekali dan validasinya hanya di server.
    """
    isi = "" if terbaca == placeholder else terbaca
    if isi == diketik:
        return None

    # app menyisipkan spasi tiap 4 digit di field KTP ('1111 1111 1111 111'),
    # jadi perbandingan mentah selalu gagal. Yang dinilai adalah isinya,
    # bukan format tampilannya.
    polos, polos_diketik = re.sub(r"\s+", "", isi), re.sub(r"\s+", "", diketik)
    if polos == polos_diketik:
        return f"app memformat tampilan jadi {isi!r}; nilainya sendiri utuh"
    if polos and polos_diketik.startswith(polos):
        return (f"app memotong ketikan: {len(polos_diketik)} → {len(polos)} "
                f"karakter ({isi!r})")
    return f"isi field tidak sama dengan yang diketik: {isi!r} vs {diketik!r}"


def pilih_tanggal(io, tanggal: str, pembuka: str = "Pilih tanggal") -> list[str]:
    """Isi field tanggal lewat date picker Material, mode input teks.

    Field tanggal di app ini EditText di UI tree tapi menolak diketik langsung
    (Appium: `invalid element state`) — nilainya hanya boleh datang dari picker.
    Jalur kalender berarti menghitung koordinat per hari; mode teks jauh lebih
    tahan banting, dan tombol pengalihnya disediakan picker itu sendiri
    ("Beralih ke mode input teks").

    Mengembalikan teks layar sesudah OK, supaya penelepon bisa memeriksa apakah
    picker menolak tanggalnya (mis. di luar rentang yang diizinkan).
    """
    els = uitree.parse_elements(io.source())
    buka = uitree.find_tappable(els, pembuka)
    if buka is None:
        raise LookupError(f"pembuka picker {pembuka!r} tidak ada di layar")
    io.tap(*buka["xy"])
    time.sleep(1.5)

    els = uitree.parse_elements(io.source())
    mode = uitree.find_tappable(els, "Beralih ke mode input teks")
    if mode is not None:
        io.tap(*mode["xy"])
        time.sleep(1.2)

    els = uitree.parse_elements(io.source())
    field = next((e for e in els if e["editable"]), None)
    if field is None:
        raise LookupError("field tanggal tidak ada di picker")

    # dua syarat yang keduanya diam-diam kalau dilanggar:
    #   1. field harus difokuskan dulu (dump menunjukkan focused=false, dan
    #      set-value ke field tak berfokus tidak berbekas sama sekali)
    #   2. hanya DIGIT yang boleh diketik — pemisah '/' disisipkan sendiri oleh
    #      Material. Mengetik '30/06/2026' gagal total tanpa pesan apa pun,
    #      sedangkan '30062026' menjadi '30/06/2026'.
    io.tap(*field["xy"])
    time.sleep(0.8)
    el = io.editable([e for e in els if e["editable"]].index(field))
    io.clear(el)
    io.type_into(el, re.sub(r"\D", "", tanggal))
    # JANGAN hide_keyboard() di sini: di HP ini ia mengirim Back, dan Back
    # menutup dialog picker-nya sekalian — tanggalnya hilang tanpa jejak.
    time.sleep(0.8)

    terbaca = [e["text"] for e in uitree.parse_elements(io.source()) if e["editable"]]
    if tanggal not in terbaca:
        raise ValueError(f"picker tidak menerima {tanggal!r}; field berisi {terbaca}")

    els = uitree.parse_elements(io.source())
    ok = uitree.find_tappable(els, "OK")
    if ok is None:
        raise LookupError("tombol OK picker tidak ada / disabled")
    io.tap(*ok["xy"])
    time.sleep(1.5)
    return sorted(uitree.all_texts(io.source()))


# ---------------------------------------------------------------
# PELAKSANA — butuh transport, tapi transport-nya disuntikkan
# ---------------------------------------------------------------
class FormRunner:
    """Menjalankan kasus uji atas satu form.

    `io` cukup punya: source(), editable(n), clear(id), type_into(id, teks),
    tap(x, y), hide_keyboard(). Sengaja duck-typed supaya bisa diberi transport
    palsu di tes — seluruh keputusan di kelas ini terverifikasi tanpa device.
    """

    def __init__(self, io, oracle: Oracle, tombol_submit: str = "Simpan"):
        self.io, self.oracle, self.tombol = io, oracle, tombol_submit
        self.placeholder: dict[str, str] = {}

    # -- pembacaan layar ------------------------------------------
    def elemen(self) -> list[dict]:
        return uitree.parse_elements(self.io.source())

    def layar(self) -> list[str]:
        return self.oracle.identify(uitree.all_texts(self.io.source()))

    def catat_placeholder(self, bersihkan: bool = True) -> dict[str, str]:
        """Rekam teks form KOSONG supaya nanti bisa dibedakan dari isi asli.

        Form dikosongkan lebih dulu secara bawaan. Kalau tidak, sisa nilai dari
        run sebelumnya ikut terekam sebagai "placeholder" — dan sesudah itu
        setiap pembacaan ulang membandingkan diri dengan sampah, sehingga field
        yang benar-benar kosong dilaporkan "berubah". Sudah kejadian sekali.
        """
        if bersihkan:
            for i, e in enumerate([x for x in self.elemen() if x["editable"]]):
                try:
                    self.io.clear(self.io.editable(i))
                except Exception as err:
                    if "invalid element state" not in str(err):
                        raise           # field picker-only: memang tak bisa dikosongkan
            self.io.hide_keyboard()
        self.placeholder = {e["label"]: e["text"]
                            for e in self.elemen() if e["editable"]}
        return self.placeholder

    def _index_field(self, label: str) -> int:
        editables = [e for e in self.elemen() if e["editable"]]
        for i, e in enumerate(editables):
            if uitree.norm_label(e["label"]) == uitree.norm_label(label):
                return i
        raise LookupError(f"field {label!r} tidak ada di layar ini")

    # -- aksi -----------------------------------------------------
    def isi(self, label: str, nilai: str) -> str:
        """Kosongkan lalu ketik; kembalikan apa yang benar-benar terbaca."""
        i = self._index_field(label)
        el = self.io.editable(i)
        self.io.clear(el)
        if nilai:
            self.io.type_into(el, nilai)
        self.io.hide_keyboard()
        for e in self.elemen():
            if e["editable"] and uitree.norm_label(e["label"]) == uitree.norm_label(label):
                return e["text"]
        return ""

    def submit(self) -> Observation:
        els = self.elemen()
        tombol = uitree.find_tappable(els, self.tombol)
        if tombol is None:
            raise LookupError(f"tombol {self.tombol!r} tidak ada / disabled")
        sebelum_teks = uitree.all_texts(self.io.source())
        sebelum = self.oracle.identify(sebelum_teks)
        self.io.tap(*tombol["xy"])
        muncul = uitree.observe(self.io.source, sebelum_teks)
        return Observation(muncul=muncul, layar_sebelum=sebelum,
                           layar_sesudah=self.layar())

    # -- satu kasus -----------------------------------------------
    def jalankan(self, field_key: str, case: Case) -> Hasil:
        spec = self.oracle.fields[field_key]
        label = spec["label"]
        terbaca = self.isi(label, case.value)

        catatan = []
        beda = periksa_masukan(case.value, terbaca,
                               self.placeholder.get(label + " *",
                                                    self.placeholder.get(label, "")))
        if beda:
            catatan.append(beda)

        obs = self.submit()
        obs.diketik, obs.isi_field = case.value, terbaca
        outcome, bukti = classify(self.oracle, obs, label)

        verdict = None
        if outcome in (DITERIMA, DITOLAK):
            verdict = self.oracle.judge(field_key, case, outcome == DITERIMA)
        return Hasil(case, outcome, bukti, verdict, catatan)

    # -- seluruh kasus satu field ---------------------------------
    def jalankan_field(self, field_key: str, isi_lain: bool = True) -> list[Hasil]:
        """Kasus tidak-valid dulu, baru yang valid — dan BERHENTI di penerimaan.

        Urutan ini bukan selera. Form Registrasi hanya bisa sukses sekali:
        begitu tersimpan, profil lengkap dan app menolak dengan "Profil sudah
        pernah dilengkapi." — formnya hilang selamanya. Jadi kasus yang
        diharapkan ditolak dijalankan lebih dulu (form bertahan tiap kali
        ditolak), dan kasus valid paling akhir. Kalau ada nilai tidak-valid
        yang ternyata DITERIMA, run berhenti di situ juga: itu sudah bug-nya,
        dan meneruskan hanya akan menekan tombol pada layar yang sudah pindah.
        """
        if isi_lain:
            self.isi_field_lain(kecuali=field_key)

        cases = sorted(self.oracle.generate(field_key),
                       key=lambda c: c.expect_accept)     # False dulu
        hasil: list[Hasil] = []
        for case in cases:
            h = self.jalankan(field_key, case)
            hasil.append(h)
            if h.outcome == DITERIMA:
                h.catatan.append("form sudah tersimpan — sisa kasus dihentikan")
                break
        return hasil

    def isi_field_lain(self, kecuali: str) -> dict[str, str]:
        """Isi field lain dengan contoh valid supaya toast menyebut field yang
        SEDANG diuji, bukan tetangganya.

        Tidak semua elemen `editable` benar-benar bisa diketik: "Akhir Berlaku
        SIM" adalah EditText di UI tree, tapi Appium menolaknya dengan
        `invalid element state` karena nilainya hanya boleh datang dari date
        picker. Field seperti itu dicatat, bukan dianggap terisi — kalau
        disembunyikan, hasil uji field lain jadi tidak bisa dipercaya.
        """
        terisi = {}
        self.tak_bisa_diketik: list[str] = []
        for key, spec in self.oracle.fields.items():
            nilai = spec.get("contoh_valid")
            if key == kecuali or not nilai:
                continue
            try:
                terisi[spec["label"]] = self.isi(spec["label"], nilai)
            except LookupError:
                continue                  # field itu tidak ada di layar ini
            except Exception as e:        # AppiumError; transport disuntikkan
                if "invalid element state" not in str(e):
                    raise
                self.tak_bisa_diketik.append(spec["label"])
        return terisi


# ---------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    from appium_client import Appium

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--field", required=True, help="kunci field di oracle_rules.yaml")
    p.add_argument("--rules", default="oracle_rules.yaml")
    p.add_argument("--udid", default="RR8R309NG6L")
    p.add_argument("--package", default="com.phbid_darat.supir.stg")
    p.add_argument("--activity", default="com.phbid_darat.supir.MainActivity")
    p.add_argument("--tombol", default="Simpan")
    p.add_argument("--tanpa-isi-lain", action="store_true",
                   help="jangan isi field lain dengan contoh valid")
    args = p.parse_args()

    o = Oracle(args.rules)
    with Appium(args.udid, args.package, args.activity) as io:
        r = FormRunner(io, o, args.tombol)
        print("placeholder:", r.catat_placeholder())
        print("layar:", r.layar(), "\n")
        for h in r.jalankan_field(args.field, isi_lain=not args.tanpa_isi_lain):
            print(h)
            for c in h.catatan:
                print(f"    catatan: {c}")
