#!/usr/bin/env python3
"""Jembatan web TMS → data resi, untuk mengisi form ber-resi di Driver Hub.

Kenapa modul ini ada: tahap muat/bongkar order sub-tipe LTL dan AIR_FREIGHT
menuntut "No. Resi" yang DIVALIDASI server terhadap data order — nilai karangan
ditolak ("Resi tidak dikenal: …"), jadi satu-satunya sumber resi yang sah
adalah TMS web. UI web-nya Next.js, tapi backend-nya REST biasa di
https://apitms-staging.prahu-hub.com/api — modul ini bicara langsung ke API
(jauh lebih stabil daripada menyetir browser).

Struktur data (dipetakan 16 Agu 2026):
    order (orderCode TANPA prefix brand: app "LKL-LTL538…" = web "LTL538…")
      └─ orderShipments[] ─ shipment (shipmentCode "SHP…")
                              └─ resis[] ─ nomorResi  ← yang diketik ke app

Kredensial lewat env var QA_TMS_USER / QA_TMS_PASS — tidak pernah di repo,
mengikuti pola QA_PHONE di oracle_rules.yaml.

Catatan Cloudflare: User-Agent bawaan urllib DIBLOKIR (403); selalu kirim UA
browser. Token akses berumur ~15 menit — cukup untuk satu run; login ulang
saja per pemanggilan skrip, jangan repot refresh.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

BASE = "https://apitms-staging.prahu-hub.com/api"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class TmsError(RuntimeError):
    pass


class TmsWeb:
    def __init__(self, email: str | None = None, password: str | None = None):
        self.email = email or os.environ.get("QA_TMS_USER", "")
        self.password = password or os.environ.get("QA_TMS_PASS", "")
        if not (self.email and self.password):
            raise TmsError("set QA_TMS_USER dan QA_TMS_PASS dulu")
        self.token: str | None = None

    # ---------------------------------------------------------------
    def _req(self, path: str, body: dict | None = None) -> dict:
        headers = {"User-Agent": UA, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(BASE + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise TmsError(f"{path}: HTTP {e.code} {e.read()[:200]!r}") from e
        if not res.get("success", True):
            raise TmsError(f"{path}: {res.get('message')}")
        return res

    def login(self) -> None:
        res = self._req("/auth/login", {"email": self.email,
                                        "password": self.password})
        self.token = res["data"]["accessToken"]

    # ---------------------------------------------------------------
    def order(self, order_code: str) -> dict:
        """Order menurut kode-nya. Prefix brand dari app ("LKL-", "TSH-")
        dibuang: web menyimpan "LTL538…", app menampilkan "LKL-LTL538…"."""
        kode = re.sub(r"^[A-Z]{2,4}-", "", order_code.strip())
        if not self.token:
            self.login()
        res = self._req(f"/orders?search={urllib.parse.quote(kode)}")
        hits = [o for o in res.get("data", []) if o.get("orderCode") == kode]
        if not hits:
            raise TmsError(f"order {kode!r} tidak ada di TMS")
        return hits[0]

    def resi_untuk(self, order_code: str) -> list[str]:
        """Seluruh nomorResi milik sebuah order, urut shipment.

        Semua resi dikembalikan, bukan yang pertama saja: satu order bisa
        punya beberapa shipment dan tiap shipment beberapa barang — app
        menuntut SEMUA resi diisi (dipisah koma) saat tahap muat/bongkar.
        """
        o = self.order(order_code)
        resi: list[str] = []
        for os_ in o.get("orderShipments", []):
            sid = (os_.get("shipment") or {}).get("id") or os_.get("shipmentId")
            if not sid:
                continue
            det = self._req(f"/shipments/{sid}")
            d = det.get("data", det)
            for r in d.get("resis", []):
                n = r.get("nomorResi")
                if n and n not in resi:
                    resi.append(n)
        if not resi:
            raise TmsError(f"order {order_code}: tidak ada resi di shipment-nya")
        return resi


if __name__ == "__main__":
    import sys
    t = TmsWeb()
    for kode in sys.argv[1:] or ["LKL-LTL5377813262"]:
        print(kode, "->", ", ".join(t.resi_untuk(kode)))
