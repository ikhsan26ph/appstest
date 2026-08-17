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
                       jenis_armada_id: str) -> dict:
        sopir = self.sopir_by_wa(sopir_wa)
        su = self.subuser_by_wa(subuser_wa)
        unit = self.armada_berjenis(jenis_armada_id)
        res = self._req_method("POST", "/penugasan", {
            "orderId": order_id,
            "assignments": [{"urutan": 1, "sopirId": sopir["id"],
                             "armadaId": unit["id"], "subUserIds": [su["id"]]}],
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


if __name__ == "__main__":
    import sys
    jenis = (sys.argv[1] if len(sys.argv) > 1 else "ltl").lower()
    tgl = sys.argv[2] if len(sys.argv) > 2 else "2026-08-21T17:00:00.000Z"
    if jenis.startswith("ftl-"):
        hasil = seed_ftl(tgl, tipe_pengiriman=jenis.split("-", 1)[1].upper())
    else:
        hasil = {"ltl": seed_ltl, "ftl": seed_ftl}[jenis](tgl)
    print(json.dumps(hasil, ensure_ascii=False, indent=1))
