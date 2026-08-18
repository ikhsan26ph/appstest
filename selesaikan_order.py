#!/usr/bin/env python3
"""Selesaikan satu order Driver Hub tahap demi tahap sampai keluar dari daftar.

Pakai: selesaikan_order.py <ID_ORDER> <ROUTE> <TANGGAL> [SESSION]
       (SESSION = id sesi Appium yang sudah hidup; tanpa itu, buka sendiri)

Gerbang resi (LKL-LTL*/LKL-AFR*) diisi otomatis dari TMS web (tms_web.py,
env QA_TMS_USER/QA_TMS_PASS) — dengan loop koreksi: pada order multi-shipment
server hanya menerima resi shipment gilirannya dan MENYEBUT yang ditolak
("Resi tidak dikenal: X, Y"); yang disebut dibuang, sisanya disimpan ulang.

Pelajaran yang dipatenkan di sini (16–17 Agu 2026, semuanya pernah gagal):
- Kartu disasar ROUTE+TANGGAL, bukan route saja — Beranda bisa memuat 4 kartu
  se-rute; dan ID DIVERIFIKASI DI DALAM form sebelum aksi apa pun.
- Sidik jari layar = (teks, y). Set teks saja ambigu: ada kartu kembar persis,
  dan pembanding set salah menyimpulkan "mentok" di antara dua kartu kembar.
- Kartu terakhir daftar: panel Lihat Data Order memanjang ke bawah layar —
  jangan andalkan panel untuk menemukan ID (akordeon buka-tutup tanpa akhir).
- Daftar bisa basi sesudah order hilang (kartu terpotong, mentok scroll);
  pull-to-refresh di awal tiap putaran menyembuhkan + menyegarkan tombol.
- Dua rupa form tahap: Foto*(+Keterangan+No. Resi)+Simpan, ATAU tombol aksi
  tunggal di dasar halaman ("Berangkat Muat"/"Berangkat Bongkar").
- Sesudah Simpan: antrean "Sinkronkan (n)" wajib ditekan sampai kering.
- Bug DICATAT (analysis/temuan_bug_*.md) lalu run diteruskan; hanya
  crash/sesi-putus yang menghentikan.
"""

import base64
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from appium_client import Appium
from oracle import Oracle
import uitree

TARGET_ID, TARGET_ROUTE, TARGET_TGL = sys.argv[1], sys.argv[2], sys.argv[3]
SESSION = sys.argv[4] if len(sys.argv) > 4 else None
UDID = os.environ.get("QA_UDID", "RRCR901KJKW")
PKG = "com.phbid_darat.supir.stg"
ACT = "com.phbid_darat.supir.MainActivity"
EVID = os.environ.get("QA_EVID", "/tmp/qa_evidence")
os.makedirs(EVID, exist_ok=True)
BUGLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "analysis", "temuan_bug_2026-08-16.md")
ROUTE_RE = re.compile(r"^(Kota|Kabupaten) .+ - (Kota|Kabupaten) .+$")
ID_RE = re.compile(r"^[A-Z]{2,4}-[A-Z]{2,4}\d{6,}$")
HEADER_Y = 250
MAX_PUTARAN = int(os.environ.get("QA_MAX_PUTARAN", "10"))

o = Oracle(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "oracle_rules.yaml"))
io = Appium(UDID, PKG, ACT)
if SESSION:
    io.session = SESSION
else:
    io.start()
    time.sleep(1.0)
    for _ in range(20):
        time.sleep(1.0)
        if len(uitree.all_texts(io.source())) > 1:
            break


def peta(src=None):
    out = []
    try:
        root = ET.fromstring(src or io.source())
    except ET.ParseError:
        return out
    for n in root.iter():
        t = uitree.node_text(n.attrib)
        r = uitree.rect(n.attrib.get("bounds"))
        if t and r:
            out.append((t, r))
    return out


def sidik(src=None):
    return tuple(sorted((t, r[1]) for t, r in peta(src)))


def swipe(jarak, ms=900):
    jarak = max(-1300, min(1300, jarak))
    y1 = 1600 if jarak > 0 else 800
    io._sess("POST", "/actions", {"actions": [{
        "type": "pointer", "id": "f", "parameters": {"pointerType": "touch"},
        "actions": [
            {"type": "pointerMove", "duration": 0, "x": 540, "y": y1},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": ms, "x": 540, "y": y1 - jarak},
            {"type": "pause", "duration": 250},
            {"type": "pointerUp", "button": 0}]}]})
    time.sleep(1.1)


def ke_atas():
    for _ in range(10):
        s = sidik()
        swipe(-1200)
        if sidik() == s:
            break


def screenshot(nama):
    b64 = io._sess("GET", "/screenshot")
    with open(os.path.join(EVID, nama), "wb") as f:
        f.write(base64.b64decode(b64))


def aktifkan_app():
    """Bawa app ke depan tanpa menebak berapa kali Back — Back buta pernah
    keluar sampai launcher dan loop mengira order hilang."""
    try:
        io._sess("POST", "/appium/device/activate_app", {"appId": PKG})
    except Exception:
        pass
    time.sleep(3.0)


def tutup_modal():
    for _ in range(8):
        src = io.source()
        texts = uitree.all_texts(src)
        els = uitree.parse_elements(src)
        # panel notifikasi menutupi app (pernah menipu loop jadi "order
        # hilang"): tandanya tombol Clear/Notification settings sistem
        if "Notification settings" in texts or "Brightness settings panel" in texts:
            print("   (panel notifikasi terbuka — tutup dengan Back)")
            io.back()
            time.sleep(1.5)
            continue
        if "Aktifkan Autostart" in texts:
            io.back(); time.sleep(1.3)
            if "Aktifkan Autostart" in uitree.all_texts(io.source()):
                b = uitree.find_tappable(uitree.parse_elements(io.source()),
                                         "Buka Pengaturan")
                if b:
                    io.tap(*b["xy"]); time.sleep(2.3)
                    io.back(); time.sleep(1.8)
            continue
        b = None
        for cap in ("Sudah saya aktifkan", "Nanti", "Izinkan",
                    "While using the app", "Saat aplikasi digunakan",
                    "Allow all the time", "Allow"):
            b = uitree.find_tappable(els, cap)
            if b:
                break
        if b is None:
            return
        print(f"   (modal) tap {b['text']!r}")
        io.tap(*b["xy"])
        time.sleep(1.8)


def cari_kartu():
    """('ada', tombol) | ('hilang'|'buntu', None). Kartu = blok judul
    TARGET_ROUTE yang memuat TARGET_TGL."""
    ke_atas()
    terlihat_separuh = False
    for _ in range(24):
        src = io.source()
        m = peta(src)
        # `t == TARGET_ROUTE`: kartu cacat tampilan (rute "-", temuan #4)
        # tetap harus bisa disasar — tanggal + verifikasi ID di form yang
        # menjaga dari salah kartu
        judul = sorted([(r[1], t) for t, r in m
                        if ROUTE_RE.match(t) or t == TARGET_ROUTE])
        separuh = False
        for i, (y0, t) in enumerate(judul):
            if t != TARGET_ROUTE or y0 <= HEADER_Y:
                continue
            y1 = judul[i + 1][0] if i + 1 < len(judul) else 10**6
            blok = [(t2, r2) for t2, r2 in m if y0 < r2[1] < y1]
            if not any(t2 == TARGET_TGL for t2, _ in blok):
                continue
            els = uitree.parse_elements(src)
            isi = [e for e in els if e["text"] == "Isi Penugasan"
                   and y0 < e["xy"][1] < y1]
            if isi:
                return "ada", isi[0]
            separuh = True
        if separuh:
            terlihat_separuh = True
            swipe(400)
            continue
        s = sidik(src)
        swipe(900)
        if sidik() == s:
            return ("buntu" if terlihat_separuh else "hilang"), None
    return "buntu", None


def lampirkan_foto():
    els = uitree.parse_elements(io.source())
    foto = uitree.find_tappable(els, "Tambah Foto") or \
        uitree.find_tappable(els, "Add Photo")
    if foto is None:
        for arah in (-1200, 700, 700, 700):
            swipe(arah)
            els = uitree.parse_elements(io.source())
            foto = uitree.find_tappable(els, "Tambah Foto") or \
                uitree.find_tappable(els, "Add Photo")
            if foto:
                break
    if foto is None:
        return "tanpa_foto"
    io.tap(*foto["xy"])
    time.sleep(2.0)
    els = uitree.parse_elements(io.source())
    gal = uitree.find_tappable(els, "Galeri")
    if gal is None:
        return "picker_aneh"
    io.tap(*gal["xy"])
    time.sleep(3.0)
    # foto teratas picker (paling baru) — cukup untuk bukti QA
    target = None
    root = ET.fromstring(io.source())
    for n in root.iter():
        d = n.attrib.get("content-desc") or ""
        if d.startswith("Photo taken on"):
            r = uitree.rect(n.attrib.get("bounds"))
            if r:
                target = ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
                break
    if target is None:
        return "galeri_kosong"
    io.tap(*target)
    time.sleep(1.5)
    els = uitree.parse_elements(io.source())
    done = uitree.find_tappable(els, "Done") or uitree.find_tappable(els, "Add")
    if done:
        io.tap(*done["xy"])
        time.sleep(2.0)
    return "ok"


def cari_field_dgn_scroll(label):
    for _ in range(6):
        s = sidik(); swipe(-1200)
        if sidik() == s:
            break
    for _ in range(6):
        els = uitree.parse_elements(io.source())
        f = uitree.find_field(els, label)
        if f:
            return f
        s = sidik(); swipe(800)
        if sidik() == s:
            return None
    return None


def isi_berlabel(label, nilai, wajib=True):
    f = cari_field_dgn_scroll(label)
    if f is None:
        if wajib:
            print(f"   field {label!r} tidak ada di form")
        return False
    els = uitree.parse_elements(io.source())
    f2 = uitree.find_field(els, label)
    editables = [e for e in els if e["editable"]]
    idx = editables.index(f2)
    io.tap(*f2["xy"]); time.sleep(0.6)
    el = io.editable(idx)
    io.clear(el)
    io.type_into(el, nilai)
    io.hide_keyboard(); time.sleep(0.6)
    for e in uitree.parse_elements(io.source()):
        if e["editable"] and uitree.norm_label(e["label"]) == uitree.norm_label(f2["label"]):
            print(f"   {label} = {e['text']!r}")
    return True


def sinkron():
    els = uitree.parse_elements(io.source())
    sink = next((e for e in els if e["text"].startswith("Sinkronkan")), None)
    if sink:
        sebelum = uitree.all_texts(io.source())
        print(f"   tap {sink['text']!r}")
        io.tap(*sink["xy"])
        muncul = uitree.observe(io.source, sebelum, window=6.0)
        for v in o.scan_ui(sorted(muncul)):
            print("   ORACLE:", v)
        time.sleep(1.5)


for putaran in range(1, MAX_PUTARAN + 1):
    print(f"\n===== PUTARAN {putaran} — {TARGET_ID} =====")
    tutup_modal()
    texts_awal = uitree.all_texts(io.source())
    if "Beranda" not in texts_awal:
        if "Detail Tracking" in texts_awal:
            print("   (masih di Detail Tracking — Back ke Beranda)")
            io.back(); time.sleep(1.5)
        else:
            print("   (bukan layar app — aktifkan app)")
            aktifkan_app()
        tutup_modal()
    ke_atas()
    swipe(-900, ms=500)          # pull-to-refresh: daftar bisa basi
    time.sleep(3.0)
    sinkron()

    state, tombol = cari_kartu()
    if state == "hilang":
        print(f"ORDER TIDAK ADA LAGI DI DAFTAR — {TARGET_ID} SELESAI ✔")
        screenshot(f"selesai_{TARGET_ID}.png")
        break
    if state == "buntu":
        print("!! kartu terlihat tapi tombol tak terjangkau — berhenti, cek manual")
        screenshot(f"buntu_{TARGET_ID}.png")
        break
    if not tombol["enabled"]:
        print("tombol Isi Penugasan DISABLED — giliran berhenti di sini")
        screenshot(f"disabled_{TARGET_ID}.png")
        break

    io.tap(*tombol["xy"])
    time.sleep(2.0)
    tutup_modal()

    texts = uitree.all_texts(io.source())
    if "Detail Tracking" not in texts:
        print("!! bukan Detail Tracking; teks:", sorted(texts)[:10])
        screenshot(f"nyasar_{TARGET_ID}_{putaran}.png")
        break
    # konten form dimuat dari jaringan — ID bisa telat beberapa detik;
    # menghakimi terlalu cepat membuat form sah dibatalkan sebagai salah kartu
    id_form = ""
    for _ in range(6):
        m = peta()
        id_form = next((t for t, r in m if ID_RE.match(t)), "")
        if id_form:
            break
        time.sleep(1.5)
    if id_form != TARGET_ID:
        print(f"!! ID di form {id_form!r} ≠ {TARGET_ID} — BATAL, mundur")
        screenshot(f"salah_kartu_{TARGET_ID}_{putaran}.png")
        io.back(); time.sleep(1.5)
        break
    print(f"form OK, ID {id_form}")

    hasil_foto = lampirkan_foto()
    print(f"lampiran foto: {hasil_foto}")
    if hasil_foto not in ("ok", "tanpa_foto"):
        print("!! gagal melampirkan foto — berhenti")
        screenshot(f"foto_gagal_{TARGET_ID}.png")
        break

    isi_berlabel("Keterangan",
                 f"Uji QA otomatis putaran {putaran}: kondisi baik", wajib=False)

    daftar_resi = None
    if cari_field_dgn_scroll("No. Resi"):
        import tms_web
        try:
            daftar_resi = tms_web.TmsWeb().resi_untuk(TARGET_ID)
        except Exception as e:
            print(f"!! ambil resi dari TMS gagal: {e} — berhenti")
            break
        print(f"   resi dari TMS web ({len(daftar_resi)}): {', '.join(daftar_resi)}")
        if not isi_berlabel("No. Resi", ", ".join(daftar_resi)):
            print("!! gagal mengisi No. Resi — berhenti")
            break

    # multidrop: tahap "Berangkat Bongkar" menuntut alamat tujuan dipilih
    # dulu ("Pilih alamat tujuan terlebih dahulu") — pemicunya elemen
    # "Pilih Alamat", opsinya bottom-sheet berisi alamat tiap titik drop.
    # Pilih yang PERTAMA; server yang tahu giliran drop mana yang sah.
    els = uitree.parse_elements(io.source())
    pa = uitree.find_tappable(els, "Pilih Alamat")
    if pa is None:
        swipe(-800)
        els = uitree.parse_elements(io.source())
        pa = uitree.find_tappable(els, "Pilih Alamat")
    if pa:
        io.tap(*pa["xy"]); time.sleep(1.8)
        opsi = [e for e in uitree.parse_elements(io.source())
                if e["clickable"] and len(e["text"]) > 25
                and e["text"] != "Pilih Alamat"]
        if opsi:
            print(f"   pilih alamat tujuan: {opsi[0]['text'][:55]!r}")
            io.tap(*opsi[0]["xy"]); time.sleep(1.5)
        else:
            print("!! sheet Pilih Alamat tanpa opsi — berhenti")
            screenshot(f"alamat_kosong_{TARGET_ID}_{putaran}.png")
            io.back(); time.sleep(1.2)
            break

    els = uitree.parse_elements(io.source())
    simpan = uitree.find_tappable(els, "Simpan")
    if simpan is None:
        swipe(700)
        els = uitree.parse_elements(io.source())
        simpan = uitree.find_tappable(els, "Simpan")
    if simpan is None:
        aksi = [e for e in els if e["clickable"] and e["enabled"]
                and e["xy"][1] > 1900 and e["text"]
                and e["text"] not in ("Lapor", "Back", "Beranda", "Profil",
                                      "Notification", "Tambah Foto", "Add Photo")]
        if not aksi:
            print("!! tidak ada tombol Simpan maupun aksi tahap")
            screenshot(f"tanpa_aksi_{TARGET_ID}_{putaran}.png")
            break
        simpan = aksi[-1]
        print(f"   pakai tombol aksi tahap: {simpan['text']!r}")

    muncul = []
    for percobaan in range(3):
        sebelum = uitree.all_texts(io.source())
        io.tap(*simpan["xy"])
        muncul = uitree.observe(io.source, sebelum, window=8.0)
        tolak = next((t for t in muncul
                      if t.startswith("Resi tidak dikenal:")), None)
        if not (tolak and daftar_resi):
            break
        buruk = {x.strip() for x in tolak.split(":", 1)[1].split(",")}
        daftar_resi = [r for r in daftar_resi if r not in buruk]
        if not daftar_resi:
            print("!! semua resi ditolak server — berhenti")
            break
        print(f"   server menolak {sorted(buruk)}; coba ulang dengan "
              f"{', '.join(daftar_resi)}")
        isi_berlabel("No. Resi", ", ".join(daftar_resi))
        els = uitree.parse_elements(io.source())
        simpan = uitree.find_tappable(els, "Simpan") or simpan
    print("teks baru:", sorted(muncul)[:12])
    cacat = o.scan_ui(sorted(muncul))
    for v in cacat:
        print("  ORACLE:", v)
        with open(BUGLOG, "a") as f:
            f.write(f"- [{v.severity}] {TARGET_ID} putaran {putaran}: "
                    f"{v.field} — {v.message} ({v.value!r})\n")
    if any(v.kind in ("crash", "session") for v in cacat):
        print("!! crash/sesi putus — berhenti")
        screenshot(f"cacat_{TARGET_ID}_{putaran}.png")
        break
    screenshot(f"putaran_{TARGET_ID}_{putaran}.png")

    if "Detail Tracking" in uitree.all_texts(io.source()):
        print("!! layar tidak beranjak sesudah aksi — tahap menolak; berhenti")
        print("   teks terlihat:", sorted(uitree.all_texts(io.source()))[:14])
        screenshot(f"menolak_{TARGET_ID}_{putaran}.png")
        io.back(); time.sleep(1.5)
        break
    sinkron()

print(f"\nSESSION={io.session}  (masih hidup)")
