"""
Oracle QA — generator nilai batas + penilai hasil.

Dipakai bersama Appium, tapi tidak bergantung padanya:
seluruh modul ini murni fungsi, jadi bisa diuji tanpa device.

    from oracle import Oracle
    o = Oracle("oracle_rules.yaml")

    for case in o.generate("ktp"):
        driver.find_element(...).send_keys(case.value)
        driver.find_element(...).click()
        accepted = not is_error_shown(driver)
        v = o.judge("ktp", case, accepted)
        if not v.ok:
            report(v)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator

import yaml


@dataclass
class Case:
    """Satu nilai uji beserta ekspektasi yang menyertainya."""
    field: str
    value: str
    expect_accept: bool
    reason: str


@dataclass
class Verdict:
    ok: bool
    field: str
    value: str
    message: str
    severity: str = "high"

    def __str__(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"[{mark}] {self.field}={self.value!r} — {self.message}"


class Oracle:
    def __init__(self, rules_path: str):
        with open(rules_path, encoding="utf-8") as f:
            self.rules: dict[str, Any] = yaml.safe_load(f)
        self.fields = self.rules.get("fields", {})
        self.implicit = self.rules.get("implicit", {})
        self.screens = self.rules.get("screens", {})

    # ---------------------------------------------------------------
    # GENERATOR — nilai batas per tipe field
    # ---------------------------------------------------------------
    def generate(self, field: str) -> Iterator[Case]:
        spec = self.fields[field]
        ftype = spec.get("type", "text")
        length = spec.get("length", {})

        def case(value: str, ok: bool, why: str) -> Case:
            return Case(field, value, ok, why)

        # --- batas panjang -------------------------------------------
        if "exact" in length:
            n = length["exact"]
            body = self._filler(ftype)
            yield case(body * n, True, f"panjang tepat {n}")
            yield case(body * (n - 1), False, f"kurang 1 dari {n}")
            yield case(body * (n + 1), False, f"lebih 1 dari {n}")
            yield case(body * (n + 3), False, f"jauh melebihi {n}")
        else:
            lo, hi = length.get("min"), length.get("max")
            body = self._filler(ftype)
            if lo:
                yield case(body * lo, True, f"panjang minimum {lo}")
                yield case(body * (lo - 1), False, f"di bawah minimum {lo}")
            if hi:
                yield case(body * hi, True, f"panjang maksimum {hi}")
                yield case(body * (hi + 1), False, f"melebihi maksimum {hi}")

        # --- kosong / whitespace -------------------------------------
        required = spec.get("required", False)
        yield case("", not required, "nilai kosong")
        yield case("   ", not required, "spasi saja")

        # --- pelanggaran charset -------------------------------------
        if ftype in ("numeric", "phone"):
            yield case("1234abcd5678", False, "huruf pada field numerik")
            yield case("1234-5678-90", False, "tanda baca pada field numerik")
        if ftype == "email":
            yield case("bukan-email", False, "tanpa @")
            yield case("a@b", False, "domain tidak lengkap")
            yield case("user@example.com", True, "email valid")

        # --- tanggal --------------------------------------------------
        if ftype == "date":
            fmt = spec.get("format", "%d-%m-%Y")
            today = datetime.now()
            future = (today + timedelta(days=365)).strftime(fmt)
            past = (today - timedelta(days=30)).strftime(fmt)
            must_future = spec.get("must_be_future", False)
            yield case(future, True, "tanggal masa depan")
            yield case(past, not must_future, "tanggal sudah lewat")
            yield case("32-13-2026", False, "tanggal mustahil")

        # --- injeksi / unicode ---------------------------------------
        yield case("' OR '1'='1", False, "percobaan SQL injection")
        yield case("<script>alert(1)</script>", False, "percobaan XSS")
        yield case("😀🔥", False, "emoji di luar charset")

        # --- prefix khusus -------------------------------------------
        for prefix in spec.get("prefix_allowed", []):
            n = length.get("min", 10)
            yield case(prefix + "1" * (n - len(prefix)), True,
                       f"prefix diizinkan {prefix}")
        if spec.get("prefix_allowed"):
            yield case("99" + "1" * 8, False, "prefix tidak diizinkan")

    @staticmethod
    def _filler(ftype: str) -> str:
        return "1" if ftype in ("numeric", "phone") else "a"

    # ---------------------------------------------------------------
    # ORACLE — menilai hasil
    # ---------------------------------------------------------------
    def judge(self, field: str, case: Case, accepted: bool) -> Verdict:
        """accepted = apakah aplikasi MENERIMA nilai tersebut."""
        if accepted == case.expect_accept:
            return Verdict(True, field, case.value, f"sesuai ({case.reason})")

        if accepted:
            msg = f"nilai tidak valid DITERIMA — {case.reason}"
            sev = "high"
        else:
            msg = f"nilai valid DITOLAK — {case.reason}"
            sev = "medium"
        return Verdict(False, field, case.value, msg, sev)

    # ---------------------------------------------------------------
    # ORACLE IMPLISIT — scanner logcat
    # ---------------------------------------------------------------
    def scan_logcat(self, lines: list[str]) -> list[Verdict]:
        found: list[Verdict] = []
        for rule in self.implicit.get("logcat_forbidden", []):
            pat = re.compile(rule["pattern"])
            for line in lines:
                if pat.search(line):
                    if self._allowlisted(line):
                        continue
                    found.append(Verdict(
                        False, "logcat", line.strip()[:120],
                        f"pola terlarang: {rule['id']}",
                        rule.get("severity", "high"),
                    ))
                    break
        return found

    def _allowlisted(self, line: str) -> bool:
        for entry in self.implicit.get("http_allowlist", []):
            if entry["endpoint"] in line and str(entry["status"]) in line:
                return True
        return False

    # ---------------------------------------------------------------
    # ORACLE EKSPLISIT — invariant layar
    # ---------------------------------------------------------------
    def check_screen(self, screen: str, observed: dict[str, list[str]]) -> list[Verdict]:
        """observed: {selector: [teks elemen yang ditemukan]}"""
        out: list[Verdict] = []
        for inv in self.screens.get(screen, {}).get("invariants", []):
            sel = inv.get("selector", "")
            items = observed.get(sel, [])
            kind = inv["type"]

            if kind == "unique_list":
                dupes = {x for x in items if items.count(x) > 1}
                if dupes:
                    out.append(Verdict(
                        False, inv["id"], ", ".join(sorted(dupes)),
                        f"nilai duplikat pada {sel}"))

            elif kind == "min_count":
                if len(items) < inv["value"]:
                    out.append(Verdict(
                        False, inv["id"], str(len(items)),
                        f"jumlah kurang dari {inv['value']}"))

            elif kind == "attribute_equals":
                actual = observed.get(f"{sel}@{inv['attribute']}", [])
                if actual and actual[0] != inv["value"]:
                    out.append(Verdict(
                        False, inv["id"], actual[0],
                        f"atribut {inv['attribute']} seharusnya {inv['value']}",
                        inv.get("severity", "high")))
        return out


if __name__ == "__main__":
    o = Oracle("oracle_rules.yaml")
    for f in o.fields:
        cases = list(o.generate(f))
        print(f"\n{f} — {len(cases)} kasus uji")
        for c in cases[:6]:
            tag = "terima" if c.expect_accept else "tolak "
            print(f"  [{tag}] {c.value[:28]!r:32} {c.reason}")
