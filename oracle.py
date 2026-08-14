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

Oracle implisit (v2) — dijalankan tiap langkah, tanpa deklarasi per layar:

    before = uitree.all_texts(page_source())
    tap(...)
    muncul = uitree.observe(page_source, before)   # toast hidup ~2 detik
    laporan  = o.scan_ui(muncul)                   # kanal utama: teks di layar
    laporan += o.scan_logcat(baris_logcat_app)     # cadangan: crash saja
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator

import yaml

# satu-satunya definisi normalisasi label; tinggal di pembaca UI karena di
# situlah bentuk teks layar diketahui. Diekspor ulang demi pemanggil lama.
from uitree import norm_label  # noqa: F401


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
    kind: str = "bug"          # bug | lingkungan | session | rahasia | crash

    def __str__(self) -> str:
        if self.ok:
            mark = "PASS"
        elif self.severity == "info":
            mark = "INFO"
        else:
            mark = "FAIL"
        return f"[{mark}] {self.field}={self.value!r} — {self.message}"


class Oracle:
    def __init__(self, rules_path: str):
        with open(rules_path, encoding="utf-8") as f:
            self.rules: dict[str, Any] = yaml.safe_load(f)
        self.fields = self.rules.get("fields", {})
        self.implicit = self.rules.get("implicit", {})
        self.screens = self.rules.get("screens", {})
        self.crawler = self.rules.get("crawler", {})
        # aturan yang mati karena env var-nya tidak diisi — dilaporkan apa
        # adanya supaya "tidak ada temuan" tidak tertukar dengan "tidak diperiksa"
        self.inactive: list[str] = []
        self._secret_pats = self._build_secret_patterns()

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
    # ORACLE IMPLISIT (utama) — pesan di layar
    #
    # Build release ini tidak menulis satu pun baris HTTP ke logcat, jadi
    # status server hanya terlihat sebagai toast ("Gagal: 403"). Karena itu
    # pemeriksa utama membaca teks UI, bukan log.
    # ---------------------------------------------------------------
    def scan_ui(self, texts: list[str]) -> list[Verdict]:
        """texts = pesan yang sempat muncul (uitree.observe) atau teks layar.

        Urutan: ui_expected menang lebih dulu, lalu ui_environment, baru
        ui_forbidden. Tanpa urutan itu tiap uji nilai batas akan dilaporkan
        sebagai cacat, padahal penolakan input justru hasil yang benar.
        """
        out: list[Verdict] = []
        for raw in texts:
            text = re.sub(r"\s+", " ", (raw or "")).strip()
            if not text:
                continue

            if self._match(self.implicit.get("ui_expected", []), text):
                continue

            rule = self._match(self.implicit.get("ui_environment", []), text)
            if rule:
                out.append(Verdict(
                    False, rule["id"], text[:120],
                    "kondisi lingkungan, bukan cacat app — langkah ini tidak sah, ulangi",
                    rule.get("severity", "info"), "lingkungan"))
                continue

            rule = self._match(self.implicit.get("ui_forbidden", []), text)
            if rule:
                out.append(Verdict(
                    False, rule["id"], text[:120],
                    f"pesan kegagalan tampil ke pengguna: {rule['id']}",
                    rule.get("severity", "high"), rule.get("kind", "bug")))
        return out

    @staticmethod
    def _match(rules: list[dict], text: str) -> dict | None:
        """Aturan pertama yang cocok. `pattern` tunggal atau `patterns` daftar."""
        for rule in rules:
            pats = rule.get("patterns") or ([rule["pattern"]] if rule.get("pattern") else [])
            for pat in pats:
                if re.search(pat, text):
                    return rule
        return None

    # ---------------------------------------------------------------
    # ORACLE IMPLISIT (cadangan) — logcat
    #
    # Perannya diturunkan jadi dua hal yang tidak mungkin salah baca: crash,
    # dan kebocoran kredensial. Aturan v1 (NullPointerException, SQLiteException,
    # HTTP 4xx/5xx) dibuang — di HP fisik logcat bercampur seluruh sistem,
    # ketiganya memanen baris milik app lain. Saring per PID sebelum memanggil.
    # ---------------------------------------------------------------
    def scan_logcat(self, lines: list[str]) -> list[Verdict]:
        found: list[Verdict] = []
        for rule in self.implicit.get("logcat_crash", []):
            pat = re.compile(rule["pattern"])
            for line in lines:
                if pat.search(line):
                    found.append(Verdict(
                        False, rule["id"], line.strip()[:120],
                        f"crash terdeteksi: {rule['id']}",
                        rule.get("severity", "critical"), "crash"))
                    break

        for rule_id, pat, sev in self._secret_pats:
            for line in lines:
                if pat.search(line):
                    found.append(Verdict(
                        False, rule_id, self._redact(line)[:120],
                        "kredensial bocor ke logcat", sev, "rahasia"))
                    break
        return found

    def _build_secret_patterns(self) -> list[tuple[str, re.Pattern, str]]:
        """Pola kredensial. Nomor asli datang dari env, tidak pernah dari repo."""
        pats: list[tuple[str, re.Pattern, str]] = []
        for rule in self.implicit.get("logcat_secret", []):
            sev = rule.get("severity", "critical")
            env = rule.get("from_env")
            if env:
                val = os.environ.get(env, "").strip()
                if not val:
                    self.inactive.append(f"{rule['id']} (env {env} kosong)")
                    continue
                # satu nomor bisa muncul 3 bentuk: 62…, +62…, 08…
                core = re.sub(r"^(\+?62|0)", "", re.sub(r"\D", "", val))
                pats.append((rule["id"],
                             re.compile(r"(?:\+?62|0)?" + re.escape(core)), sev))
            elif rule.get("pattern"):
                pats.append((rule["id"], re.compile(rule["pattern"]), sev))
        return pats

    @staticmethod
    def _redact(line: str) -> str:
        """Jangan menyalin rahasia dari logcat ke laporan yang ikut ter-commit."""
        return re.sub(r"\d{5,}", lambda m: m.group()[:3] + "…", line.strip())

    # ---------------------------------------------------------------
    # ORACLE EKSPLISIT — invariant layar
    # ---------------------------------------------------------------
    def identify(self, texts: set[str] | list[str]) -> list[str]:
        """Layar mana yang sedang tampak. Manifest hanya punya satu activity,
        jadi identitas layar hanya bisa datang dari teks yang terlihat."""
        seen = {norm_label(t) for t in texts}
        hits = []
        for name, spec in self.screens.items():
            marks = [norm_label(m) for m in spec.get("identified_by", [])]
            if marks and all(m in seen for m in marks):
                hits.append(name)
        return sorted(hits)

    def check_screen(self, screen: str, observed: dict[str, list[str]]) -> list[Verdict]:
        """observed berkunci LABEL, bukan resource-id.

        Kontrak ini berubah dari v1 atas keputusan 14 Agu: mayoritas elemen di
        app ini adalah android.view.View tanpa resource-id, dengan TextView
        label terpisah — selector berbasis id tidak punya apa pun untuk
        dipegang. Kunci dinormalkan lewat norm_label(), jadi 'Nomor KTP *'
        dari dump cocok dengan 'Nomor KTP' di YAML.
        """
        by_label: dict[str, list[str]] = {}
        for key, vals in observed.items():
            by_label.setdefault(norm_label(key), []).extend(vals)

        out: list[Verdict] = []
        for inv in self.screens.get(screen, {}).get("invariants", []):
            label = inv.get("label", "")
            items = by_label.get(norm_label(label), [])
            kind = inv["type"]

            if kind == "unique_list":
                dupes = {x for x in items if items.count(x) > 1}
                if dupes:
                    out.append(Verdict(
                        False, inv["id"], ", ".join(sorted(dupes)),
                        f"nilai duplikat di bawah label {label!r}",
                        inv.get("severity", "high")))

            elif kind == "unique_parts":
                # duplikat di app ini terjadi DI DALAM satu nilai, bukan
                # sebagai dua node terpisah: 'PT. X (IK), PT. X (IK)'.
                sep = inv.get("separator", ", ")
                for value in items:
                    parts = [p.strip() for p in value.split(sep) if p.strip()]
                    dupes = {p for p in parts if parts.count(p) > 1}
                    if dupes:
                        out.append(Verdict(
                            False, inv["id"], value[:120],
                            f"bagian berulang di dalam nilai {label!r}: "
                            + ", ".join(sorted(dupes)),
                            inv.get("severity", "high")))

            elif kind == "min_count":
                if len(items) < inv["value"]:
                    out.append(Verdict(
                        False, inv["id"], str(len(items)),
                        f"jumlah di bawah label {label!r} kurang dari {inv['value']}",
                        inv.get("severity", "high")))
        return out

    # ---------------------------------------------------------------
    # CRAWLER — pagar aksi merusak
    # ---------------------------------------------------------------
    def is_forbidden(self, text: str) -> bool:
        """True kalau elemen ini tidak boleh ditekan crawler."""
        t = norm_label(text)
        if not t:
            return False
        return any(norm_label(b) in t for b in self.crawler.get("forbidden_text", []))


if __name__ == "__main__":
    o = Oracle("oracle_rules.yaml")
    print(f"app: {o.rules['meta']['app']}  rules v{o.rules['meta']['version']}")
    for f in o.fields:
        cases = list(o.generate(f))
        print(f"\n{f} ({o.fields[f]['label']}) — {len(cases)} kasus uji")
        for c in cases[:4]:
            tag = "terima" if c.expect_accept else "tolak "
            print(f"  [{tag}] {c.value[:28]!r:32} {c.reason}")

    print("\noracle implisit atas contoh pesan:")
    for v in o.scan_ui(["Gagal: 403", "Nomor KTP harus 16 digit",
                        "Notifikasi terkirim!", "Tidak ada koneksi internet"]):
        print(" ", v)
    if o.inactive:
        print("\naturan tidak aktif:", ", ".join(o.inactive))
