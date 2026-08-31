#!/usr/bin/env python3
"""Penyemai data uji TMS: shipment → order → penugasan, sampai tampil di app.

Melengkapi tms_web.py (pembaca resi): modul ini MENULIS. Seluruh alur
diverifikasi 17 Agu 2026 dengan pilot sungguhan:
    SHP26080008 → order LTL6933610417 → penugasan ASSIGNED
(LTL Semarang→Surabaya, pengirim PT. H3 IK, penerima Alluka, 2 barang → 2 resi
dibangkitkan server otomatis.)

Jebakan skema yang SUDAH dibayar mahal — jangan ditemukan ulang:
- `pengirim[]`/`penerima[]` wajib menyertakan `customerName` (bukan hanya id).
- `dropPointId` untuk penerima INDIVIDU harus DIHILANGKAN dari payload —
  `null` ditolak validator ("expected string, received null").
- `kotaAsalId`/`kotaTujuanId` wajib di level atas shipment DAN order.
- /jenis-armada adalah tabel PIVOT: field `id` bukan id master; yang diterima
  order adalah field `jenisArmadaId`-nya. Cocokkan NAMA persis (== , bukan
  startswith — "Tronton Wing Box" vs "Tronton Wing Box1" beda id).
- Unit armada di assignment harus ber-jenisArmada.id == jenisArmadaId order,
  kalau tidak: ARMADA_JENIS_MISMATCH.
- `assignments[].subUserIds` BUKAN baris /sopirs — referensinya /sub-users
  (staf/PIC). Minimal satu. Kecocokan nomor WA sub-user inilah (format 08…
  atau 628…) yang menentukan tugas muncul di HP siapa.
- Resi dibangkitkan otomatis satu per item barang; dibaca kembali lewat
  tms_web.TmsWeb.resi_untuk() untuk mengisi form app.
"""

from __future__ import annotations

import json
import re
import urllib.request

from tms_web import TmsWeb, BASE, UA, TmsError


def _norm_wa(wa: str | None) -> str:
    wa = re.sub(r"\D", "", wa or "")
    return "62" + wa[1:] if wa.startswith("0") else wa


class TmsSeed(TmsWeb):
    # ------------------------------------------------------------------
    def _req_method(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"User-Agent": UA, "Accept": "application/json",
                   "Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise TmsError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from e
        if not res.get("success", True):
            raise TmsError(f"{method} {path}: {res.get('message')}")
        return res

    def _pages(self, path: str, limit: int = 50):
        page = 1
        while page < 20:
            res = self._req(f"{path}?limit={limit}&page={page}")
            data = res.get("data", [])
            yield from data
            if page >= res.get("meta", {}).get("totalPage", 1) or not data:
                return
            page += 1

    # -- resolusi master ------------------------------------------------
    def customer(self, nama: str) -> dict:
        for c in self._pages("/customers"):
            if c.get("name", "").strip().casefold() == nama.strip().casefold():
                return c
        raise TmsError(f"customer {nama!r} tidak ada")

    def droppoint_milik(self, customer_id: str, nama: str | None = None) -> dict:
        for d in self._pages("/droppoints"):
            if d.get("customerId") != customer_id:
                continue
            if nama is None or d.get("name", "").strip().casefold() == nama.strip().casefold():
                return d
        raise TmsError(f"customer {customer_id} tidak punya droppoint"
                       + (f" bernama {nama!r}" if nama else ""))

    def sopir_by_wa(self, wa: str) -> dict:
        want = _norm_wa(wa)
        for s in self._pages("/sopirs"):
            if _norm_wa(s.get("whatsapp")) == want:
                return s
        raise TmsError(f"sopir ber-WA persis {wa} tidak ada")

    def subuser_by_wa(self, wa: str) -> dict:
        want = _norm_wa(wa)
        for u in self._pages("/sub-users"):
            if _norm_wa(u.get("whatsapp")) == want:
                return u
        raise TmsError(f"sub-user ber-WA persis {wa} tidak ada — "
                       "tugas tidak akan muncul di HP itu")

    def jenis_armada(self, nama: str) -> str:
        """Id MASTER jenis armada. Nama dicocokkan persis — pivot /jenis-armada
        memuat kembar sengaja ('Tronton Wing Box' vs '…Box1')."""
        for j in self._pages("/jenis-armada"):
            if j.get("name", "").strip().casefold() == nama.strip().casefold():
                return j["jenisArmadaId"]
        raise TmsError(f"jenis armada {nama!r} tidak ada")

    def armada_berjenis(self, jenis_armada_id: str) -> dict:
        for a in self._pages("/armadas"):
            if (a.get("jenisArmada") or {}).get("id") == jenis_armada_id:
                return a
        raise TmsError(f"tidak ada unit armada berjenis {jenis_armada_id}")

    # -- pihak pengirim/penerima ---------------------------------------
    def pihak_perusahaan(self, nama_customer: str, urutan: int = 0,
                         droppoint_nama: str | None = None) -> dict:
        c = self.customer(nama_customer)
        dp = self.droppoint_milik(c["id"], droppoint_nama)
        return {
            "jenis": "PERUSAHAAN", "customerId": c["id"], "customerName": c["name"],
            "picName": dp.get("picName") or "QA",
            "picWhatsapp": dp.get("picWhatsapp") or "6280000000000",
            "provinceId": dp["provinceId"], "cityId": dp["cityId"],
            "districtId": dp["districtId"], "kelurahanId": dp["kelurahanId"],
            "kodePos": (re.search(r"\b(\d{5})\b", dp.get("address", "")) or
                        [None, "00000"])[1],
            "address": dp["address"], "dropPointId": dp["id"], "urutan": urutan,
        }

    def pihak_individu(self, nama_customer: str, alamat: dict,
                       urutan: int = 0) -> dict:
        """`alamat`: dict provinceId/cityId/districtId/kelurahanId/kodePos/
        address — TANPA dropPointId (individu tidak punya; null ditolak)."""
        c = self.customer(nama_customer)
        return {"jenis": "INDIVIDU", "customerId": c["id"],
                "customerName": c["name"], "picName": c["name"],
                "picWhatsapp": _norm_wa(c.get("whatsapp")) or "6280000000000",
                "urutan": urutan, **alamat}

    # -- rantai tulis ---------------------------------------------------
    def buat_shipment(self, service: str, pengirim: list[dict],
                      penerima: list[dict], items: list[dict],
                      kota_asal_id: str, kota_tujuan_id: str,
                      tanggal_muat: str, harga: int = 150000,
                      catatan: str = "Seed QA otomatis",
                      kota_asal_name: str = "", kota_tujuan_name: str = "",
                      extra: dict | None = None) -> dict:
        # nama kota ikut dikirim — backend tidak menurunkannya dari ID, dan
        # shipment TIDAK bisa diubah lagi sesudah ditugaskan ke armada.
        # `extra`: field khusus per-serviceType (FTL: tipePengiriman,
        # jenisArmadaId, jumlahArmada — lihat template SHP26080007).
        res = self._req_method("POST", "/shipments", {
            "serviceType": service,
            "kotaAsalId": kota_asal_id, "kotaTujuanId": kota_tujuan_id,
            "kotaAsalName": kota_asal_name, "kotaTujuanName": kota_tujuan_name,
            "tanggalMuat": tanggal_muat, "catatan": catatan,
            "pengirim": pengirim, "penerima": penerima,
            "armadas": [{"harga": harga, "items": items}],
            "ppn": 0, "pph": 0, "hargaDPP": harga, "ppnAmount": 0,
            "pphAmount": 0, "totalNilaiBarang": 0, "asuransiAmount": 0,
            "totalHarga": harga, **(extra or {}),
        })
        return res["data"]

    def buat_order(self, shipment_id: str, service: str, kota_asal_id: str,
                   kota_tujuan_id: str, tanggal_muat: str,
                   jenis_armada_id: str, kota_asal_name: str = "",
                   kota_tujuan_name: str = "") -> dict:
        # kotaAsalName/kotaTujuanName WAJIB dikirim walau validator diam:
        # backend tidak menurunkannya dari ID, dan tanpa nama itu kartu di app
        # supir tampil "-" (terbukti 17 Agu di pilot LTL6933610417 — temuan
        # produk di analysis/temuan_bug_2026-08-16.md)
        res = self._req_method("POST", "/orders", {
            "serviceType": service,
            "kotaAsalId": kota_asal_id, "kotaTujuanId": kota_tujuan_id,
            "kotaAsalName": kota_asal_name, "kotaTujuanName": kota_tujuan_name,
            "tanggalMuat": tanggal_muat,
            "shipmentId": shipment_id, "shipmentIds": [shipment_id],
            "jenisArmadaId": jenis_armada_id,
        })
        return res["data"]

    def buat_penugasan(self, order_id: str, sopir_wa: str, subuser_wa: str,
                       jenis_armada_id: str | None = None,
                       unit_extra: dict | None = None) -> dict:
        """Unit assignment per moda: darat (FTL/LTL) pakai armadaId dari
        `jenis_armada_id`; laut (FCL) pakai `unit_extra` mis.
        {"jenisKontainerId": …} — validator: "FCL wajib jenisKontainerId"."""
        su = self.subuser_by_wa(subuser_wa)
        a = {"urutan": 1, "subUserIds": [su["id"]]}
        if jenis_armada_id:
            # sopirId HANYA sah bersama armadaId ("sopirId hanya boleh diisi
            # jika armadaId juga diisi") — unit kontainer (FCL) tanpa sopir;
            # tugas tetap sampai ke HP lewat WA sub-user PIC
            a["armadaId"] = self.armada_berjenis(jenis_armada_id)["id"]
            a["sopirId"] = self.sopir_by_wa(sopir_wa)["id"]
        if unit_extra:
            a.update(unit_extra)
        res = self._req_method("POST", "/penugasan", {
            "orderId": order_id, "assignments": [a],
        })
        return res["data"]

    @staticmethod
    def barang(nama: str, berat: int = 2, jumlah: int = 1,
               ongkos: int = 75000, pickup_party_idx: int | None = None,
               drop_off_party_idx: int | None = None) -> dict:
        """`pickup_party_idx`: indeks pengirim[] pemilik barang — WAJIB pada
        MULTIPICKUP (template SHP26070432). `drop_off_party_idx`: indeks
        penerima[] tujuan barang — WAJIB pada MULTIDROP (template
        SHP26080002, kuncinya `dropOffPartyIdx`). NORMAL tanpa keduanya."""
        b = {"jenisBarang": nama, "kemasan": "Sak", "jumlah": jumlah,
             "berat": berat, "panjangCm": 10, "lebarCm": 10, "tinggiCm": 10,
             "kubikasiM3": None, "nilaiBarang": 0, "withInsurance": False,
             "ongkosKirim": ongkos}
        if pickup_party_idx is not None:
            b["pickupPartyIdx"] = pickup_party_idx
        if drop_off_party_idx is not None:
            b["dropOffPartyIdx"] = drop_off_party_idx
        return b


# Alamat individu contoh (Rungkut, Surabaya) — diambil dari data asli staging;
# dipakai bila penerima INDIVIDU tidak menyebut alamat lain.
ALAMAT_RUNGKUT = {
    "provinceId": "2c61eb82-0cf2-49e7-999b-a569cf8fa48c",
    "cityId": "e0494cc5-62e1-4929-b639-009edfc24417",
    "districtId": "0ac2bd4b-caef-44a8-8cd7-2cae3541b4a1",
    "kelurahanId": "a26a4694-adba-4c69-89c9-661d8f6b85bd",
    "kodePos": "60293", "address": "jl rungkut no 55",
}


def seed_ltl(tanggal_muat: str, sopir_wa: str = "6283830011881") -> dict:
    """Rantai lengkap LTL (rute mengikuti droppoint pengirim → Surabaya).
    Mengembalikan {shipment, order, penugasan, resi}."""
    t = TmsSeed()
    t.login()
    pengirim = t.pihak_perusahaan("PT. H3 IK")
    penerima = t.pihak_individu("Alluka Fatimah Rahma", ALAMAT_RUNGKUT)
    items = [t.barang("Kardus QA A"), t.barang("Kardus QA B", berat=3)]
    sh = t.buat_shipment("LTL", [pengirim], [penerima], items,
                         pengirim["cityId"], penerima["cityId"], tanggal_muat,
                         kota_asal_name="Kota Semarang",
                         kota_tujuan_name="Kota Surabaya")
    ja = t.jenis_armada("Tronton Wing Box1")
    o = t.buat_order(sh["id"], "LTL", pengirim["cityId"], penerima["cityId"],
                     tanggal_muat, ja, kota_asal_name="Kota Semarang",
                     kota_tujuan_name="Kota Surabaya")
    p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa, ja)
    resi = t.resi_untuk(o["orderCode"])
    return {"shipment": sh["shipmentCode"], "order": o["orderCode"],
            "order_id": o["id"], "penugasan": p.get("id"), "resi": resi}


def seed_ltl_multi(tanggal_muat: str, sopir_wa: str = "6283830011881",
                   jumlah_shipment: int = 2) -> dict:
    """Satu order LTL dari BEBERAPA shipment se-rute (pola nyata staging:
    LKL-LTL5377813262 = 2 shipment / 3 resi). Kontrak POST /orders memang
    memuat `shipmentIds` jamak; `shipmentId` tunggal diisi shipment pertama.
    Di app, tahap berjalan hanya menerima resi shipment gilirannya — loop
    koreksi selesaikan_order.py yang memilah."""
    t = TmsSeed()
    t.login()
    pengirim = t.pihak_perusahaan("PT. H3 IK")
    penerima = t.pihak_individu("Alluka Fatimah Rahma", ALAMAT_RUNGKUT)
    shipments = []
    for i in range(jumlah_shipment):
        # shipment pertama 2 barang, sisanya 1 — meniru contoh 2 shipment/3 resi
        items = [t.barang(f"Kardus QA S{i + 1}A")]
        if i == 0:
            items.append(t.barang(f"Kardus QA S{i + 1}B", berat=3))
        shipments.append(t.buat_shipment(
            "LTL", [pengirim], [penerima], items,
            pengirim["cityId"], penerima["cityId"], tanggal_muat,
            kota_asal_name="Kota Semarang", kota_tujuan_name="Kota Surabaya"))
    ja = t.jenis_armada("Tronton Wing Box1")
    res = t._req_method("POST", "/orders", {
        "serviceType": "LTL",
        "kotaAsalId": pengirim["cityId"], "kotaTujuanId": penerima["cityId"],
        "kotaAsalName": "Kota Semarang", "kotaTujuanName": "Kota Surabaya",
        "tanggalMuat": tanggal_muat,
        "shipmentId": shipments[0]["id"],
        "shipmentIds": [s["id"] for s in shipments],
        "jenisArmadaId": ja,
    })
    o = res["data"]
    p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa, ja)
    resi = t.resi_untuk(o["orderCode"])
    return {"shipments": [s["shipmentCode"] for s in shipments],
            "order": o["orderCode"], "order_id": o["id"],
            "penugasan": p.get("id"), "resi": resi}


def seed_ltl_estafet(tanggal_muat: str, sopir_wa: str = "6283830011881",
                     reuse_shipment_ids: tuple[str, str] | None = None) -> dict:
    """Estafet dua leg LTL — S1 Semarang→Surabaya, S2 Surabaya (IK -
    Kenjeran)→Medan (IK - Medan) — sebagai DUA order ke sopir yang sama.

    KENAPA dua order (terbukti 17 Agu, tiga varian ditolak HTTP 422 "Rute
    shipment X tidak sesuai dengan rute order"): validator /orders menuntut
    SEMUA shipment persis se-rute (asal & tujuan) dengan order-nya — order
    lintas-rute tidak mungkin by design; multi-shipment = konsolidasi se-rute.

    `reuse_shipment_ids`: (id_leg1, id_leg2) untuk memakai shipment DRAFT
    yang sudah ada alih-alih membuat baru."""
    t = TmsSeed()
    t.login()
    if reuse_shipment_ids:
        sh1 = t._req(f"/shipments/{reuse_shipment_ids[0]}")["data"]
        sh2 = t._req(f"/shipments/{reuse_shipment_ids[1]}")["data"]
    else:
        smg = t.pihak_perusahaan("PT. H3 IK")
        sby_alluka = t.pihak_individu("Alluka Fatimah Rahma", ALAMAT_RUNGKUT)
        sby_kenjeran = t.pihak_perusahaan("PT. H3 IK",
                                          droppoint_nama="IK - Kenjeran")
        medan = t.pihak_perusahaan("PT. H3 IK", droppoint_nama="IK - Medan")
        sh1 = t.buat_shipment("LTL", [smg], [sby_alluka],
                              [t.barang("Kardus QA leg1-A"),
                               t.barang("Kardus QA leg1-B", berat=3)],
                              smg["cityId"], sby_alluka["cityId"], tanggal_muat,
                              kota_asal_name="Kota Semarang",
                              kota_tujuan_name="Kota Surabaya")
        sh2 = t.buat_shipment("LTL", [sby_kenjeran], [medan],
                              [t.barang("Kardus QA leg2")],
                              sby_kenjeran["cityId"], medan["cityId"],
                              tanggal_muat,
                              kota_asal_name="Kota Surabaya",
                              kota_tujuan_name="Kota Medan")
    ja = t.jenis_armada("Tronton Wing Box1")
    hasil = {"orders": []}
    for sh, asal, tujuan in ((sh1, "Kota Semarang", "Kota Surabaya"),
                             (sh2, "Kota Surabaya", "Kota Medan")):
        o = t.buat_order(sh["id"], "LTL", sh["kotaAsalId"], sh["kotaTujuanId"],
                         tanggal_muat, ja, kota_asal_name=asal,
                         kota_tujuan_name=tujuan)
        p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa, ja)
        hasil["orders"].append({
            "shipment": sh["shipmentCode"], "order": o["orderCode"],
            "order_id": o["id"], "rute": f"{asal} - {tujuan}",
            "penugasan": p.get("id"), "resi": t.resi_untuk(o["orderCode"])})
    return hasil


def seed_ltl_transit(tanggal_muat: str,
                     sopir_wa: str = "6283830011881") -> dict:
    """Order LTL relay SATU order ber-2 transit (kontrak dipetakan 17 Agu
    dari order web LTL6962804735): rantai Semarang → Surabaya → Balikpapan
    → Medan, tiga shipment masing-masing mengisi persis satu leg, dan order
    mendeklarasikan `transitKota: [{id, name}, …]` BERURUTAN — tanpa itu
    validator menolak "Rute shipment X tidak sesuai dengan rute order".
    Resi per leg dijaga ≤2 agar tidak menabrak temuan #6 (field No. Resi
    terpotong ±60 karakter)."""
    t = TmsSeed()
    t.login()
    smg = t.pihak_perusahaan("PT. H3 IK", droppoint_nama="IK - Semarang Oke")
    sby = t.pihak_perusahaan("PT. H3 IK", droppoint_nama="IK - Kenjeran")
    bpp = t.pihak_perusahaan("PT. H3 IK", droppoint_nama="IK - Balikpapan")
    mdn = t.pihak_perusahaan("PT. H3 IK", droppoint_nama="IK - Medan")
    legs = [
        (smg, sby, "Kota Semarang", "Kota Surabaya",
         [t.barang("Kardus QA T1-A"), t.barang("Kardus QA T1-B", berat=3)]),
        (sby, bpp, "Kota Surabaya", "Kota Balikpapan",
         [t.barang("Kardus QA T2")]),
        (bpp, mdn, "Kota Balikpapan", "Kota Medan",
         [t.barang("Kardus QA T3")]),
    ]
    shipments = [
        t.buat_shipment("LTL", [asal], [tujuan], items,
                        asal["cityId"], tujuan["cityId"], tanggal_muat,
                        kota_asal_name=nama_asal, kota_tujuan_name=nama_tujuan)
        for asal, tujuan, nama_asal, nama_tujuan, items in legs]
    ja = t.jenis_armada("Tronton Wing Box1")
    res = t._req_method("POST", "/orders", {
        "serviceType": "LTL",
        "kotaAsalId": smg["cityId"], "kotaTujuanId": mdn["cityId"],
        "kotaAsalName": "Kota Semarang", "kotaTujuanName": "Kota Medan",
        "transitKota": [{"id": sby["cityId"], "name": "Kota Surabaya"},
                        {"id": bpp["cityId"], "name": "Kota Balikpapan"}],
        "tanggalMuat": tanggal_muat,
        "shipmentId": shipments[0]["id"],
        "shipmentIds": [s["id"] for s in shipments],
        "jenisArmadaId": ja,
    })
    o = res["data"]
    p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa, ja)
    return {"shipments": [s["shipmentCode"] for s in shipments],
            "order": o["orderCode"], "order_id": o["id"],
            "penugasan": p.get("id"), "resi": t.resi_untuk(o["orderCode"])}


def seed_ftl(tanggal_muat: str, sopir_wa: str = "6283830011881",
             tipe_pengiriman: str = "NORMAL") -> dict:
    """Rantai lengkap FTL (NORMAL atau MULTIPICKUP). Beda dari LTL:
    tipePengiriman + jenisArmadaId + jumlahArmada ikut di level shipment.
    MULTIPICKUP: pengirim[] dua titik ber-`urutan`, tiap barang menunjuk
    titik muatnya lewat `pickupPartyIdx`. FTL tidak ditagih resi di app."""
    t = TmsSeed()
    t.login()
    tipe = tipe_pengiriman.upper()
    if tipe == "MULTIPOINT":
        # nama di UI web "Multipoint"; enum API-nya MULTIDROP_MULTIPICKUP
        # (dibocorkan validator: "tipePengiriman wajib NORMAL/MULTIPICKUP/
        # MULTIDROP/MULTIDROP_MULTIPICKUP untuk FTL")
        tipe = "MULTIDROP_MULTIPICKUP"
    if tipe == "MULTIPICKUP":
        pengirim = [
            t.pihak_perusahaan("PT. H3 IK", urutan=0,
                               droppoint_nama="IK - Semarang Oke"),
            t.pihak_perusahaan("PT. H3 IK", urutan=1,
                               droppoint_nama="IK - Kenjeran"),
        ]
        penerima = [t.pihak_individu("Alluka Fatimah Rahma", ALAMAT_RUNGKUT)]
        items = [t.barang("Muatan QA titik 1", berat=500, ongkos=75000,
                          pickup_party_idx=0),
                 t.barang("Muatan QA titik 2", berat=500, ongkos=75000,
                          pickup_party_idx=1)]
    elif tipe == "MULTIDROP":
        pengirim = [t.pihak_perusahaan("PT. H3 IK", urutan=0,
                                       droppoint_nama="IK - Semarang Oke")]
        penerima = [
            t.pihak_perusahaan("PT. H3 IK", urutan=0,
                               droppoint_nama="IK - Kenjeran"),
            t.pihak_perusahaan("PT. H3 IK", urutan=1,
                               droppoint_nama="IK - Bonbin"),
        ]
        items = [t.barang("Muatan QA drop 1", berat=500, ongkos=75000,
                          drop_off_party_idx=0),
                 t.barang("Muatan QA drop 2", berat=500, ongkos=75000,
                          drop_off_party_idx=1)]
    elif tipe == "MULTIDROP_MULTIPICKUP":
        # tidak ada template di staging — hipotesis gabungan multipickup +
        # multidrop (barang membawa pickupPartyIdx DAN dropOffPartyIdx),
        # divalidasi empiris 17 Agu
        pengirim = [
            t.pihak_perusahaan("PT. H3 IK", urutan=0,
                               droppoint_nama="IK - Semarang Oke"),
            t.pihak_perusahaan("PT. H3 IK", urutan=1,
                               droppoint_nama="IK - Kenjeran"),
        ]
        penerima = [
            t.pihak_perusahaan("PT. H3 IK", urutan=0,
                               droppoint_nama="IK - Bonbin"),
            t.pihak_perusahaan("PT. H3 IK", urutan=1,
                               droppoint_nama="IK - Toko Madura"),
        ]
        items = [t.barang("Muatan QA titik 1", berat=500, ongkos=75000,
                          pickup_party_idx=0, drop_off_party_idx=0),
                 t.barang("Muatan QA titik 2", berat=500, ongkos=75000,
                          pickup_party_idx=1, drop_off_party_idx=1)]
    else:
        pengirim = [t.pihak_perusahaan("PT. H3 IK")]
        penerima = [t.pihak_individu("Alluka Fatimah Rahma", ALAMAT_RUNGKUT)]
        items = [t.barang("Muatan penuh QA", berat=1000, ongkos=150000)]
    ja = t.jenis_armada("Tronton Wing Box1")
    sh = t.buat_shipment("FTL", pengirim, penerima, items,
                         pengirim[0]["cityId"], penerima[0]["cityId"],
                         tanggal_muat,
                         kota_asal_name="Kota Semarang",
                         kota_tujuan_name="Kota Surabaya",
                         extra={"tipePengiriman": tipe,
                                "jenisArmadaId": ja, "jumlahArmada": 1})
    o = t.buat_order(sh["id"], "FTL", pengirim[0]["cityId"],
                     penerima[0]["cityId"],
                     tanggal_muat, ja, kota_asal_name="Kota Semarang",
                     kota_tujuan_name="Kota Surabaya")
    p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa, ja)
    return {"shipment": sh["shipmentCode"], "order": o["orderCode"],
            "order_id": o["id"], "penugasan": p.get("id")}


def seed_fcl(tanggal_muat: str, sopir_wa: str = "6283830011881") -> dict:
    """Rantai lengkap FCL Normal (laut, rute lazim Surabaya→Balikpapan).

    Beda kontrak dari FTL (dipetakan dari SHP26070422 + order FCL5723465748):
    - shipment: pelabuhanAsal/Tujuan + jenisKontainer + jumlahArmada,
      TANPA jenisArmadaId;
    - order: + jenisJadwal (DIRECT), pelayaranId, namaKapal, voyage,
      closingTime, etd, eta — jenisArmadaId null.
    """
    t = TmsSeed()
    t.login()
    pengirim = t.pihak_perusahaan("PT. H3 IK", urutan=0,
                                  droppoint_nama="IK - Kenjeran")
    penerima = t.pihak_perusahaan("PT. H3 IK", urutan=0,
                                  droppoint_nama="IK - Balikpapan")
    items = [t.barang("Muatan kontainer QA", berat=1000, ongkos=150000)]

    def master(path, nama, kunci="name"):
        for x in t._pages(path):
            if x.get(kunci, "").strip().casefold() == nama.casefold():
                return x
        raise RuntimeError(f"{nama!r} tidak ada di {path}")

    p_asal = master("/pelabuhans", "Tanjung Perak")
    p_tujuan = master("/pelabuhans", "Balikpapan")
    kontainer = master("/jenis-kontainers", "20 Feet")
    pelayaran = master("/pelayarans", "SPIL")

    laut = {
        "pelabuhanAsalId": p_asal["id"], "pelabuhanAsalName": p_asal["name"],
        "pelabuhanTujuanId": p_tujuan["id"],
        "pelabuhanTujuanName": p_tujuan["name"],
        "jenisKontainerId": kontainer["id"],
        "jenisKontainerName": kontainer["name"],
    }
    sh = t.buat_shipment("FCL", [pengirim], [penerima], items,
                         pengirim["cityId"], penerima["cityId"], tanggal_muat,
                         kota_asal_name="Kota Surabaya",
                         kota_tujuan_name="Kota Balikpapan",
                         extra={"tipePengiriman": "NORMAL",
                                "jumlahArmada": 1, **laut})
    # jadwal kapal: closing sehari sebelum muat, ETD +1, ETA +4
    import datetime
    muat = datetime.datetime.fromisoformat(tanggal_muat.replace("Z", "+00:00"))
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    o = t._req_method("POST", "/orders", {
        "serviceType": "FCL",
        "kotaAsalId": pengirim["cityId"], "kotaTujuanId": penerima["cityId"],
        "kotaAsalName": "Kota Surabaya", "kotaTujuanName": "Kota Balikpapan",
        "tanggalMuat": tanggal_muat,
        "shipmentId": sh["id"], "shipmentIds": [sh["id"]],
        "jenisJadwal": "DIRECT",
        "pelayaranId": pelayaran["id"], "pelayaranName": pelayaran["name"],
        "namaKapal": "KM QA SATU", "voyage": "001",
        "closingTime": (muat - datetime.timedelta(days=1)).strftime(fmt),
        "etd": (muat + datetime.timedelta(days=1)).strftime(fmt),
        "eta": (muat + datetime.timedelta(days=4)).strftime(fmt),
        **laut,
    })["data"]
    # unit FCL = armada + sopir + kontainer sekaligus (kontainer saja juga
    # diterima validator). CATATAN BY-DESIGN (konfirmasi user 17 Agu):
    # order FCL/LCL TIDAK masuk app sopir — moda laut dikerjakan dari web,
    # jadi rantai seed ini berhenti sah di status ASSIGNED (tanpa push FCM,
    # tanpa kartu di HP; itu bukan bug).
    p = t.buat_penugasan(o["id"], sopir_wa, sopir_wa,
                         jenis_armada_id=t.jenis_armada("Tronton Wing Box1"),
                         unit_extra={"jenisKontainerId": kontainer["id"]})
    return {"shipment": sh["shipmentCode"], "order": o["orderCode"],
            "order_id": o["id"], "penugasan": p.get("id")}


if __name__ == "__main__":
    import sys
    jenis = (sys.argv[1] if len(sys.argv) > 1 else "ltl").lower()
    tgl = sys.argv[2] if len(sys.argv) > 2 else "2026-08-21T17:00:00.000Z"
    if jenis.startswith("ftl-"):
        hasil = seed_ftl(tgl, tipe_pengiriman=jenis.split("-", 1)[1].upper())
    else:
        hasil = {"ltl": seed_ltl, "ltl-multi": seed_ltl_multi,
                 "ltl-estafet": seed_ltl_estafet,
                 "ltl-transit": seed_ltl_transit,
                 "ftl": seed_ftl, "fcl": seed_fcl}[jenis](tgl)
    print(json.dumps(hasil, ensure_ascii=False, indent=1))
