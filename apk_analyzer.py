#!/usr/bin/env python3
"""
APK static analyzer -> QA test-plan seed.

App-agnostic: reads any APK's manifest + DEX and extracts the identity,
entry points (launcher + deep links), permissions, components, and the
app's own feature modules. Emits a machine-readable JSON and a human
draft test plan you (or an automated agent) can execute.

Usage:
    python apk_analyzer.py path/to/app.apk [--out analysis]

Outputs:
    <out>/<package>.json          machine-readable summary
    <out>/<package>_testplan.md   draft test plan (markdown)
"""
import argparse
import json
import os
import re
import sys
import zipfile

# Silence androguard's very verbose loguru debug logging.
try:
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()
except Exception:
    pass
import logging
logging.getLogger("androguard").setLevel(logging.ERROR)

try:
    from androguard.core.apk import APK
except ImportError:
    sys.exit("Missing dependency. Install with: pip install androguard")

NS = "{http://schemas.android.com/apk/res/android}"

# Permissions worth testing under both granted AND denied conditions.
SENSITIVE_PERMS = {
    "CAMERA": "Kamera",
    "ACCESS_FINE_LOCATION": "Lokasi presisi",
    "ACCESS_COARSE_LOCATION": "Lokasi kasar",
    "ACCESS_BACKGROUND_LOCATION": "Lokasi background",
    "POST_NOTIFICATIONS": "Notifikasi",
    "RECORD_AUDIO": "Mikrofon",
    "READ_CONTACTS": "Kontak",
    "READ_EXTERNAL_STORAGE": "Storage",
}

# Asset filename hints -> robustness scenarios the app clearly handles.
ASSET_HINTS = {
    "no_internet": "Tampilan offline / tanpa koneksi",
    "maintenance": "Mode maintenance server",
    "update_app": "Force update versi usang",
    "notification_setting": "Layar pengaturan notifikasi",
    "splash": "Splash screen saat cold start",
}

# Keyword -> (kapabilitas, skenario uji). Dicocokkan ke nama artefak library
# yang ter-bundle DAN ke path aset. Urutan penting: yang lebih spesifik dulu,
# supaya "barcode-scanning" tidak cuma jadi "ML Kit" generik.
CAPABILITY_HINTS = [
    ("barcode", "Pemindaian barcode/QR",
     "Buka pemindai, uji kode valid, kode rusak, dan izin kamera ditolak"),
    ("text-recognition", "OCR teks",
     "Uji foto teks jelas, buram, dan miring"),
    ("face", "Deteksi wajah",
     "Uji wajah terdeteksi, tidak ada wajah, dan cahaya rendah"),
    ("vision", "ML Kit vision (on-device)",
     "Uji inferensi berhasil, gagal, dan saat model belum terunduh"),
    ("camera", "Kamera / CameraX",
     "Uji ambil gambar, batal, dan izin kamera ditolak"),
    ("maps", "Peta",
     "Uji peta termuat, tanpa koneksi, dan lokasi tidak tersedia"),
    ("places", "Places / pencarian lokasi",
     "Uji hasil ditemukan, kosong, dan kuota API habis"),
    ("messaging", "Push notification (FCM)",
     "Uji terima notifikasi foreground, background, dan app mati"),
    ("crashlytics", "Pelaporan crash",
     "Pastikan crash terkirim ke dashboard"),
    ("analytics", "Analitik",
     "Verifikasi event kunci terkirim"),
    ("biometric", "Autentikasi biometrik",
     "Uji sidik jari cocok, gagal, dan fallback PIN"),
    ("webkit", "WebView",
     "Uji halaman termuat, offline, dan URL tidak valid"),
    ("exoplayer", "Pemutar media",
     "Uji play/pause, buffering, dan stream rusak"),
    ("room", "Database lokal",
     "Uji data tersimpan, migrasi skema, dan mode offline"),
    ("work", "Background job (WorkManager)",
     "Uji job jalan saat app mati dan saat baterai hemat"),
]

# Ekstensi file yang menandakan model/aset berat -> layak disorot.
MODEL_EXTS = (".tflite", ".onnx", ".pb", ".model", ".bin", ".dat")


def strip_build_suffix(package):
    parts = package.split(".")
    while parts and parts[-1] in (
        "stg", "staging", "dev", "debug", "prod",
        "release", "qa", "uat", "sandbox", "test",
    ):
        parts = parts[:-1]
    return ".".join(parts), "/".join(parts)


def scan_dex(apk_path, app_root):
    """Cheap raw-DEX scan for the app's own feature modules & domain hints."""
    features = set()
    domains = set()
    feat_pat = re.compile((app_root + r"/feature/([A-Za-z0-9_]+)").encode())
    dom_pat = re.compile(
        (app_root + r"/feature/[A-Za-z0-9_]+/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)").encode()
    )
    try:
        with zipfile.ZipFile(apk_path) as z:
            for name in z.namelist():
                if not name.endswith(".dex"):
                    continue
                data = z.read(name)
                for m in feat_pat.finditer(data):
                    features.add(m.group(1).decode())
                for m in dom_pat.finditer(data):
                    feat = None  # domain captured per-feature below via feat_pat
                    domains.add((m.group(1).decode(), m.group(2).decode()))
    except Exception as e:
        print(f"  [warn] DEX scan failed: {e}", file=sys.stderr)
    return sorted(features), sorted(domains)


def scan_assets(apk_path):
    hints = []
    try:
        with zipfile.ZipFile(apk_path) as z:
            names = "\n".join(z.namelist()).lower()
            for key, desc in ASSET_HINTS.items():
                if key in names:
                    hints.append((key, desc))
    except Exception:
        pass
    return hints


def scan_bundled_libs(apk_path):
    """Inventaris library yang ter-bundle, dari file penanda milik AAR.

    Tiap dependensi AAR meninggalkan jejak di APK: `<artefak>.properties` di
    root, dan/atau `META-INF/<grup>_<artefak>.version`. Ini cara paling murah
    dan app-agnostic untuk tahu SDK apa saja yang benar-benar ikut ter-bundle
    -- termasuk yang tak punya komponen di manifest, seperti ML Kit.
    """
    libs = {}
    try:
        with zipfile.ZipFile(apk_path) as z:
            for name in z.namelist():
                artifact = version = None
                if "/" not in name and name.endswith(".properties"):
                    artifact = name[: -len(".properties")]
                elif name.startswith("META-INF/") and name.endswith(".version"):
                    artifact = name[len("META-INF/"):-len(".version")]
                else:
                    continue
                try:
                    body = z.read(name).decode("utf-8", "replace")
                except Exception:
                    body = ""
                for line in body.splitlines():
                    line = line.strip()
                    if line.startswith("version="):
                        version = line.split("=", 1)[1].strip()
                    elif line and "=" not in line and version is None:
                        version = line  # file .version cuma berisi angka versi
                libs[artifact] = version
    except Exception as e:
        print(f"  [warn] pemindaian library gagal: {e}", file=sys.stderr)
    return [{"artifact": a, "version": v} for a, v in sorted(libs.items())]


def scan_asset_tree(apk_path):
    """Kelompokkan isi assets/ per folder teratas, sorot berkas model.

    Aset seperti `assets/mlkit_barcode_models/*.tflite` adalah bukti kuat
    sebuah fitur ikut ter-bundle, tapi tidak terlihat sama sekali dari
    manifest maupun daftar permission.
    """
    groups = {}
    try:
        with zipfile.ZipFile(apk_path) as z:
            for info in z.infolist():
                name = info.filename
                if not name.startswith("assets/") or name.endswith("/"):
                    continue
                rest = name[len("assets/"):]
                top = rest.split("/")[0] if "/" in rest else "(root)"
                g = groups.setdefault(top, {"files": 0, "bytes": 0, "models": []})
                g["files"] += 1
                g["bytes"] += info.file_size
                if name.lower().endswith(MODEL_EXTS):
                    g["models"].append(os.path.basename(name))
    except Exception as e:
        print(f"  [warn] pemindaian aset gagal: {e}", file=sys.stderr)
    out = []
    for top, g in sorted(groups.items(), key=lambda kv: -kv[1]["bytes"]):
        out.append({
            "dir": top, "files": g["files"], "bytes": g["bytes"],
            "models": sorted(g["models"])[:8],
        })
    return out


def _tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def matches_keyword(key, name):
    """Cocokkan per token, bukan substring.

    Substring polos menghasilkan false positive yang menyesatkan: "face"
    cocok dengan `exif-inter-face`, "work" cocok dengan `sqlite-frame-work`.
    Nama artefak selalu dipisah `-`, `_`, atau `.`, jadi cocokkan urutan
    token-nya saja.
    """
    kt, nt = _tokens(key), _tokens(name)
    if not kt:
        return False
    return any(nt[i:i + len(kt)] == kt for i in range(len(nt) - len(kt) + 1))


def infer_capabilities(libs, asset_groups):
    """Turunkan kapabilitas yang bisa diuji dari library & aset yang ada."""
    sumber = (
        [lib["artifact"] for lib in libs]
        + [g["dir"] for g in asset_groups]
        + [m for g in asset_groups for m in g["models"]]
    )
    caps, seen = [], set()
    for key, label, scenario in CAPABILITY_HINTS:
        evidence = sorted({s for s in sumber if matches_keyword(key, s)})
        if evidence and label not in seen:
            seen.add(label)
            caps.append({"key": key, "label": label, "scenario": scenario,
                         "evidence": evidence[:4]})
    return caps


def exported_components(apk):
    """Return exported components and their intent filters (deep links)."""
    out = []
    axml = apk.get_android_manifest_axml().get_xml_obj()
    for tag in ("activity", "activity-alias", "service", "receiver"):
        for el in axml.iter(tag):
            name = el.get(NS + "name")
            exported = el.get(NS + "exported")
            ifilters = el.findall("intent-filter")
            if exported != "true" and not ifilters:
                continue
            entry = {"type": tag, "name": name, "exported": exported, "deep_links": []}
            for f in ifilters:
                actions = [x.get(NS + "name", "").split(".")[-1] for x in f.iter("action")]
                for d in f.iter("data"):
                    parts = {k.split("}")[-1]: v for k, v in d.attrib.items()}
                    scheme, host = parts.get("scheme"), parts.get("host")
                    if scheme:
                        link = f"{scheme}://{host or ''}"
                        entry["deep_links"].append(link)
                entry.setdefault("actions", []).extend(actions)
            if exported == "true" or entry["deep_links"]:
                out.append(entry)
    return out


def analyze(apk_path):
    apk = APK(apk_path)
    package = apk.get_package()
    base_pkg, app_root = strip_build_suffix(package)
    features, _domains = scan_dex(apk_path, app_root)
    perms = sorted(p.split(".")[-1] for p in apk.get_permissions())
    bundled_libs = scan_bundled_libs(apk_path)
    asset_groups = scan_asset_tree(apk_path)
    capabilities = infer_capabilities(bundled_libs, asset_groups)

    data = {
        "package": package,
        "app_name": apk.get_app_name(),
        "version_name": apk.get_androidversion_name(),
        "version_code": apk.get_androidversion_code(),
        "min_sdk": apk.get_min_sdk_version(),
        "target_sdk": apk.get_target_sdk_version(),
        "main_activity": apk.get_main_activity(),
        "is_staging": any(s in package for s in ("stg", "staging", "dev", "debug", "uat")),
        "permissions": perms,
        "sensitive_permissions": [
            {"perm": p, "label": SENSITIVE_PERMS[p]} for p in perms if p in SENSITIVE_PERMS
        ],
        "feature_modules": features,
        "exported_components": exported_components(apk),
        "asset_hints": [{"key": k, "desc": d} for k, d in scan_assets(apk_path)],
        "capabilities": capabilities,
        "bundled_libs": bundled_libs,
        "asset_groups": asset_groups,
        "activities": apk.get_activities(),
        "services": [s for s in apk.get_services() if app_root in s],
        "receivers": [r for r in apk.get_receivers() if app_root in r],
    }
    data["deep_links"] = sorted({
        dl for c in data["exported_components"] for dl in c["deep_links"]
    })
    return data


def render_testplan(d):
    L = []
    w = L.append
    w(f"# {d['app_name']} — Seed Test Plan (QA Fungsional)\n")
    w("> Digenerate otomatis dari analisis statis APK.\n")

    w("## Identitas Build\n")
    w("| Field | Nilai |")
    w("|---|---|")
    w(f"| Package | `{d['package']}` |")
    w(f"| Version | {d['version_name']} (code {d['version_code']}) |")
    w(f"| Min / Target SDK | {d['min_sdk']} / {d['target_sdk']} |")
    w(f"| Main activity | `{d['main_activity']}` |")
    env = "**Staging** — aman untuk diuji end-to-end" if d["is_staging"] else "Periksa: mungkin build produksi"
    w(f"| Environment | {env} |\n")

    w("## Entry Point\n")
    w(f"- Launcher: `{d['main_activity']}`")
    for dl in d["deep_links"]:
        w(f"- Deep link: `{dl}`")
        w(f"  - Uji: `adb shell am start -a android.intent.action.VIEW -d \"{dl}\"`")
    w("")

    if d["sensitive_permissions"]:
        w("## Permission Sensitif (uji granted vs denied)\n")
        for p in d["sensitive_permissions"]:
            w(f"- **{p['label']}** (`{p['perm']}`) — skenario izin diberikan & ditolak")
        w("")

    if d["feature_modules"]:
        w("## Suite per Modul Fitur\n")
        w("Modul fitur terdeteksi dari kode app. Tiap suite minimal mencakup: "
          "buka layar, happy path, error path.\n")
        for i, mod in enumerate(d["feature_modules"], 1):
            tag = mod.upper()[:4]
            w(f"### {i}. {mod.capitalize()}\n")
            w("| ID | Skenario | Ekspektasi |")
            w("|---|---|---|")
            w(f"| {tag}-01 | Buka layar {mod} | Termuat tanpa crash |")
            w(f"| {tag}-02 | Happy path {mod} | Alur utama sukses |")
            w(f"| {tag}-03 | Error path {mod} | Error ditangani & ada recovery |")
            w("")

    if d["capabilities"]:
        w("## Kapabilitas Terdeteksi (dari library & aset ter-bundle)\n")
        w("Fitur berikut ikut ter-bundle di APK meski belum tentu punya komponen "
          "di manifest — sering luput kalau hanya membaca permission.\n")
        w("| ID | Kapabilitas | Skenario | Bukti |")
        w("|---|---|---|---|")
        for i, c in enumerate(d["capabilities"], 1):
            bukti = ", ".join(f"`{e}`" for e in c["evidence"]) or "—"
            w(f"| CAP-{i:02d} | {c['label']} | {c['scenario']} | {bukti} |")
        w("")

    berat = [g for g in d["asset_groups"] if g["models"]]
    if berat:
        w("### Aset model on-device\n")
        for g in berat:
            mb = g["bytes"] / 1_048_576
            w(f"- `assets/{g['dir']}/` — {g['files']} berkas, {mb:.1f} MB "
              f"(model: {', '.join(g['models'][:3])})")
        w("\nUji juga saat model gagal dimuat / storage penuh.\n")

    if d["asset_hints"]:
        w("## Suite Robustness (dari aset app)\n")
        w("| ID | Skenario | Ekspektasi |")
        w("|---|---|---|")
        for i, a in enumerate(d["asset_hints"], 1):
            w(f"| ROB-{i:02d} | {a['desc']} | Ditangani dengan tampilan yang benar |")
        w("")

    w("## Catatan untuk agen otomatis\n")
    w("- Deteksi crash/ANR: pantau `logcat` untuk `FATAL EXCEPTION` & `ANR in`.")
    w("- Titik masuk cepat: launcher + deep link di atas.")
    w(f"- Total {len(d['feature_modules'])} modul fitur, "
      f"{len(d['deep_links'])} deep link, {len(d['permissions'])} permission, "
      f"{len(d['capabilities'])} kapabilitas, "
      f"{len(d['bundled_libs'])} library ter-bundle.\n")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="APK static analyzer -> QA test-plan seed")
    ap.add_argument("apk", help="path ke file .apk")
    ap.add_argument("--out", default="analysis", help="folder output (default: analysis)")
    args = ap.parse_args()

    if not os.path.isfile(args.apk):
        sys.exit(f"File tidak ditemukan: {args.apk}")

    print(f"[*] Menganalisis {args.apk} ...")
    d = analyze(args.apk)
    os.makedirs(args.out, exist_ok=True)

    json_path = os.path.join(args.out, f"{d['package']}.json")
    md_path = os.path.join(args.out, f"{d['package']}_testplan.md")
    with open(json_path, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    with open(md_path, "w") as f:
        f.write(render_testplan(d))

    print(f"[+] {d['app_name']} v{d['version_name']} ({d['package']})")
    print(f"[+] {len(d['feature_modules'])} modul, {len(d['deep_links'])} deep link, "
          f"{len(d['permissions'])} permission")
    print(f"[+] {len(d['bundled_libs'])} library ter-bundle, "
          f"{len(d['capabilities'])} kapabilitas terdeteksi")
    for c in d["capabilities"]:
        print(f"      - {c['label']}")
    print(f"[+] JSON      -> {json_path}")
    print(f"[+] Test plan -> {md_path}")


if __name__ == "__main__":
    main()
