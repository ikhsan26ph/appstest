#!/usr/bin/env python3
"""Pembacaan UI tree Android — murni, tanpa Appium dan tanpa jaringan.

Seluruh modul ini bekerja di atas string XML hasil `page_source` (Appium)
atau `uiautomator dump` (adb), jadi bisa diuji tanpa device sama sekali —
lihat `tests/test_uitree.py`, yang berjalan atas dump asli Driver Hub.

Dipisahkan dari qa_agent.py agar tidak ikut mati bersama loop LLM-nya.

Kenapa modul ini ada sama sekali: Driver Hub merender tombol sebagai
`android.view.View` kosong berisi TextView, dan label form sebagai TextView
sibling — bukan atribut `hint`. Tanpa asosiasi geometris di sini, pembaca UI
hanya melihat deretan elemen tanpa nama dan tidak bisa membedakan mana Nomor
KTP dan mana Nomor SIM.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Callable

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

MAX_LABEL_GAP = 260  # px vertikal maksimum antara label dan field-nya
MAX_VALUE_GAP = 80   # px vertikal maksimum antara label dan NILAI di bawahnya
TOAST_POLL = 0.35    # interval polling; toast validasi hidup ~2 detik saja
TOAST_WINDOW = 4.5   # lama memantau setelah tiap aksi; > LENGTH_LONG (3,5 s)


# ---------------------------------------------------------------
# GEOMETRI & TEKS NODE
# ---------------------------------------------------------------
def rect(bounds: str | None) -> tuple[int, int, int, int] | None:
    m = BOUNDS_RE.search(bounds or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))  # x1, y1, x2, y2


def center(bounds: str | None) -> tuple[int, int] | None:
    r = rect(bounds)
    return None if r is None else ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)


def node_text(attrib: dict) -> str:
    return (attrib.get("text") or attrib.get("content-desc") or "").strip()


def norm_label(s: str | None) -> str:
    """Samakan label dari layar dengan label yang tertulis di aturan.

    Tanda wajib menyatu di TextView yang sama ('Nomor KTP *'), dan spasi di
    dump kadang ganda. Tanpa normalisasi ini pencocokan label selalu meleset.
    """
    return re.sub(r"\s+", " ", (s or "").replace("*", " ")).strip().casefold()


# ---------------------------------------------------------------
# ASOSIASI LABEL
# ---------------------------------------------------------------
def _inner_label(r: tuple[int, int, int, int], texts: list) -> str:
    """Teks yang tergambar DI DALAM elemen: placeholder field, atau caption
    tombol yang dirender sebagai TextView terpisah di dalam View kosong."""
    best, best_area = "", None
    for a, tr in texts:
        cx, cy = (tr[0] + tr[2]) // 2, (tr[1] + tr[3]) // 2
        if not (r[0] <= cx <= r[2] and r[1] <= cy <= r[3]):
            continue
        area = (tr[2] - tr[0]) * (tr[3] - tr[1])
        if best_area is None or area < best_area:
            best, best_area = node_text(a), area
    return best


def _label_above(r: tuple[int, int, int, int], texts: list) -> str:
    """Label form biasanya TextView tepat di atas field-nya, bukan atribut
    `hint` -- tanpa ini semua EditText terlihat anonim."""
    best, best_y = "", None
    for a, tr in texts:
        if tr[3] > r[1]:                      # harus berada di atas elemen
            continue
        if tr[2] <= r[0] or tr[0] >= r[2]:    # harus beririsan horizontal
            continue
        if r[1] - tr[3] > MAX_LABEL_GAP:
            continue
        if best_y is None or tr[3] > best_y:  # ambil yang paling dekat
            best, best_y = node_text(a), tr[3]
    return best


# ---------------------------------------------------------------
# PARSING
# ---------------------------------------------------------------
def parse_elements(page_source: str) -> list[dict]:
    """Ekstrak elemen interaktif, lengkap dengan label turunan.

    Tiap elemen: index, text, label, id, class, clickable, editable, xy, bounds.
    `text` = teks sendiri, atau caption/placeholder di dalamnya.
    `label` = teks tepat di atasnya; hanya diisi untuk field, karena tombol
    sudah bernama lewat caption-nya sendiri dan label di atasnya cuma noise.
    """
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return []

    nodes = []
    for node in root.iter():
        r = rect(node.attrib.get("bounds"))
        if r:
            nodes.append((node.attrib, r))

    texts = [(a, r) for a, r in nodes if node_text(a)]

    els = []
    for a, r in nodes:
        clickable = a.get("clickable") == "true"
        editable = a.get("class", "").endswith("EditText")
        if not (clickable or editable):
            continue
        own = node_text(a)
        inner = "" if own else _inner_label(r, texts)
        above = _label_above(r, texts) if editable else ""
        els.append({
            "index": len(els),
            "text": (own or inner)[:60],
            "label": above[:40],
            "id": (a.get("resource-id") or "").split("/")[-1][:40],
            "class": a.get("class", "").split(".")[-1],
            "clickable": clickable,
            "editable": editable,
            "xy": ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2),
            "bounds": a.get("bounds", ""),
        })
    return els


def _value_below(r: tuple[int, int, int, int], texts: list) -> str:
    """Kebalikan _label_above: nilai berada tepat DI BAWAH labelnya.

    Tiga jarak terukur (Galaxy A52, 14 Agu):
      11 px  label→nilai di layar tampilan (Profil Saya)
      42 px  nilai→label berikutnya di layar yang sama
      74 px  label→field di form (Registrasi)
    MAX_VALUE_GAP 80 px melayani tampilan dan form sekaligus, dan yang dipilih
    selalu yang PALING DEKAT — jadi label berikutnya tidak pernah menang atas
    nilainya sendiri.
    """
    best, best_y = "", None
    for a, tr in texts:
        if tr[1] < r[3]:                      # harus berada di bawah label
            continue
        if tr[2] <= r[0] or tr[0] >= r[2]:    # harus beririsan horizontal
            continue
        if tr[1] - r[3] > MAX_VALUE_GAP:
            continue
        if best_y is None or tr[1] < best_y:  # ambil yang paling dekat
            best, best_y = node_text(a), tr[1]
    return best


def value_for(page_source: str, label: str) -> str:
    """Nilai yang tergambar di bawah sebuah label. '' kalau labelnya tak ada.

    Ini "selector" untuk layar tampilan: app merender pasangan label/nilai
    sebagai dua TextView bertumpuk tanpa resource-id, jadi satu-satunya
    pegangan adalah teks labelnya sendiri plus geometri.

    Hati-hati: kalau nilainya KOSONG, yang terdekat di bawah label justru
    label berikutnya (42 px), dan fungsi ini akan mengembalikannya seolah
    nilai. Pemakaian lewat observed_by_label() sudah dijaga dari jebakan itu.
    """
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return ""
    nodes = [(n.attrib, rect(n.attrib.get("bounds"))) for n in root.iter()]
    texts = [(a, r) for a, r in nodes if r and node_text(a)]
    want = norm_label(label)
    for a, r in texts:
        if norm_label(node_text(a)) == want:
            return _value_below(r, [t for t in texts if t[1] != r])
    return ""


def observed_by_label(page_source: str, labels: list[str]) -> dict[str, list[str]]:
    """Bahan untuk Oracle.check_screen(): {label: [nilai]}, label yang diminta saja.

    Sengaja hanya label yang diminta. Menebak sendiri mana node yang label dan
    mana yang nilai akan salah pada layar yang label & nilainya sama-sama
    TextView polos — aturan yang tahu, bukan pembaca UI.

    Nilai yang ternyata sama dengan label lain yang diminta dibuang: itu tanda
    nilainya kosong dan yang terbaca justru label berikutnya. Lebih baik
    melaporkan "tidak ada" (yang akan ketahuan oleh invariant min_count)
    daripada menyerahkan nilai palsu yang tampak masuk akal.
    """
    lain = {norm_label(x) for x in labels}
    out: dict[str, list[str]] = {}
    for label in labels:
        val = value_for(page_source, label)
        if val and norm_label(val) in lain - {norm_label(label)}:
            val = ""
        out[label] = [val] if val else []
    return out


def find_field(elements: list[dict], label: str) -> dict | None:
    """Elemen yang bisa diketik, dicari lewat label di atasnya."""
    want = norm_label(label)
    for e in elements:
        if e["editable"] and norm_label(e["label"]) == want:
            return e
    return None


def find_tappable(elements: list[dict], caption: str) -> dict | None:
    """Elemen yang bisa ditekan, dicari lewat caption di dalamnya.

    Tombol app ini adalah android.view.View kosong berisi TextView, jadi
    caption-nya sudah ikut terangkat oleh parse_elements().
    """
    want = norm_label(caption)
    for e in elements:
        if e["clickable"] and norm_label(e["text"]) == want:
            return e
    return None


def all_texts(page_source: str) -> set[str]:
    """Seluruh teks yang terlihat di layar — dasar deteksi toast."""
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return set()
    return {node_text(n.attrib) for n in root.iter() if node_text(n.attrib)}


# ---------------------------------------------------------------
# PENANGKAP PESAN TRANSIEN
# ---------------------------------------------------------------
def observe(read_source: Callable[[], str], before: set[str],
            window: float = TOAST_WINDOW, interval: float = TOAST_POLL) -> list[str]:
    """Tangkap SEMUA teks yang baru muncul setelah sebuah aksi.

    `read_source` adalah callable tanpa argumen yang mengembalikan XML layar --
    sengaja bukan objek driver, supaya bisa diuji tanpa Appium.

    Pesan aplikasi di Driver Hub tampil sebagai toast yang cepat hilang. Pola
    "sleep lalu dump sekali" melewatkannya sepenuhnya: layar tampak tidak
    bereaksi dan mudah salah disimpulkan sebagai tombol rusak. Karena log
    jaringan di-strip pada build release, toast inilah satu-satunya kanal
    tempat status server (mis. "Gagal: 403") terlihat sama sekali.

    Versi sebelumnya mengembalikan `appeared - last` -- hanya teks yang sempat
    muncul LALU HILANG lagi. Itu keliru dan terbukti membuang justru temuan
    yang dicari: pengukuran 14 Agu di Galaxy A52 menunjukkan toast "Gagal: 403"
    bertahan ~3,5 detik (Toast.LENGTH_LONG), masih terpampang saat jendela 3
    detik habis, sehingga ikut terbuang dan `observe()` mengembalikan daftar
    kosong padahal pesannya jelas terbaca di tiap polling.

    Sekarang: apa pun yang belum ada sebelum aksi dan muncul sesudahnya ikut
    dikembalikan, tak peduli masih tampak atau tidak. Efek sampingnya justru
    berguna -- pesan galat yang dirender menetap di layar (bukan toast) ikut
    terbaca, dan penyaringan mana yang cacat adalah urusan Oracle.scan_ui(),
    bukan urusan pembaca UI ini.
    """
    appeared: set[str] = set()
    seen = set(before)
    deadline = time.time() + window
    while time.time() < deadline:
        time.sleep(interval)
        try:
            now = all_texts(read_source())
        except Exception:
            break
        appeared |= now - seen
        seen |= now
    return sorted(appeared)
