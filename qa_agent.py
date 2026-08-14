#!/usr/bin/env python3
"""
Autonomous QA exploration agent: Appium (hands) + Claude (brain).

Loop: capture screen state -> Claude picks next action -> Appium executes
-> observe -> repeat, while watching logcat for crashes/ANRs. Writes a
JSON report of screens visited, actions taken, and any crashes found.

Prasyarat (lihat README):
  - Emulator/device Android tersambung (`adb devices` menampilkan 1 device)
  - Appium 2.x berjalan di http://127.0.0.1:4723  (appium driver: uiautomator2)
  - Env var ANTHROPIC_API_KEY di-set
  - pip install Appium-Python-Client anthropic

Contoh:
  python qa_agent.py --apk app.apk --goal "Uji alur login PIN" --max-steps 25
  python qa_agent.py --package com.phbid_darat.supir.stg \
      --activity com.phbid_darat.supir.MainActivity --max-steps 30
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

try:
    from appium import webdriver
    from appium.options.android import UiAutomator2Options
    from appium.webdriver.common.appiumby import AppiumBy
except ImportError:
    sys.exit("Missing Appium client. Install: pip install Appium-Python-Client")

try:
    import anthropic
except ImportError:
    sys.exit("Missing anthropic SDK. Install: pip install anthropic")

APPIUM_URL = os.environ.get("APPIUM_URL", "http://127.0.0.1:4723")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
TOAST_POLL = 0.35   # interval polling; toast validasi hidup ~2 detik saja
TOAST_WINDOW = 3.0  # lama memantau setelah tiap aksi

SYSTEM_PROMPT = """\
You are an autonomous QA tester exploring an Android app. Each turn you get a
list of interactable UI elements on the current screen. Choose ONE action that
makes progress toward the goal and explores the app, while behaving like a
careful human tester. Never take destructive actions (delete account, logout)
unless the goal explicitly asks.

Each element may carry a label= field, derived from the text rendered just
above it -- that is the form field's real name (e.g. label="Nomor KTP *"),
because this app keeps labels in separate nodes instead of hint attributes.
Use it to decide what to type; "*" marks a required field.

RECENT ACTIONS may include "PESAN APP: ..." -- a message the app flashed for
about two seconds after the action, usually validation feedback such as
"Foto wajib untuk tahap ini". Treat it as the app's response to what you just
did: it tells you why a submit was rejected and what to fill in next. Do not
retry the identical action that just produced one.

Respond with ONLY a JSON object, no prose, in this exact schema:
{"reasoning": "<one short sentence>",
 "action": "tap" | "input" | "back" | "scroll" | "stop",
 "target": <element index, required for tap/input>,
 "text": "<text to type, required for input>"}
Use "stop" when the goal is achieved or the screen offers no useful progress.
"""


def build_driver(args):
    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.automation_name = "UiAutomator2"
    opts.new_command_timeout = 300
    opts.auto_grant_permissions = True  # grant runtime perms to reduce noise
    # Default Appium (20s) terlalu mepet: install uiautomator2-server (~16 MB)
    # makan ~17s di HP fisik, jadi sesi pertama sering gagal "adbExec timed out".
    opts.set_capability("appium:uiautomator2ServerInstallTimeout", 120000)
    opts.set_capability("appium:uiautomator2ServerLaunchTimeout", 90000)
    opts.set_capability("appium:adbExecTimeout", 120000)
    if args.apk:
        opts.app = os.path.abspath(args.apk)
    if args.package:
        opts.app_package = args.package
    if args.activity:
        opts.app_activity = args.activity
    if args.device:
        opts.device_name = args.device
    return webdriver.Remote(APPIUM_URL, options=opts)


MAX_LABEL_GAP = 260  # px vertikal maksimum antara label dan field-nya


def rect(bounds):
    m = BOUNDS_RE.search(bounds or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))  # x1, y1, x2, y2


def center(bounds):
    r = rect(bounds)
    return None if r is None else ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)


def node_text(a):
    return (a.get("text") or a.get("content-desc") or "").strip()


def _inner_label(r, texts):
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


def _label_above(r, texts):
    """Label form biasanya TextView tepat di atas field-nya, bukan atribut
    `hint` -- tanpa ini semua EditText terlihat anonim bagi agen."""
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


def parse_elements(page_source):
    """Ekstrak elemen interaktif, lengkap dengan label yang diturunkan dari
    node teks di sekitarnya.

    Driver Hub (dan app sejenis) merender tombol sebagai View kosong berisi
    TextView, dan placeholder form sebagai TextView sibling -- bukan atribut
    `hint`. Tanpa asosiasi ini agen cuma melihat deretan field tanpa nama dan
    tidak bisa membedakan mana Nomor KTP dan mana Nomor SIM.
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
        # Hanya field yang butuh label-di-atas; tombol sudah bernama lewat
        # caption-nya sendiri, jadi label di atasnya cuma noise.
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
        })
    return els


def all_texts(page_source):
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return set()
    return {node_text(n.attrib) for n in root.iter() if node_text(n.attrib)}


def observe(driver, before, window=TOAST_WINDOW, interval=TOAST_POLL):
    """Tangkap pesan yang sempat muncul lalu hilang lagi setelah sebuah aksi.

    Validasi Driver Hub tampil sebagai toast yang hanya bertahan ~2 detik.
    Pola "sleep lalu dump sekali" melewatkannya sepenuhnya -- layar terlihat
    tidak bereaksi dan agen salah menyimpulkan tombolnya rusak. Jadi kita
    polling rapat, lalu ambil selisih terhadap snapshot terakhir.
    """
    appeared, seen = set(), set(before)
    last = set(before)
    deadline = time.time() + window
    while time.time() < deadline:
        time.sleep(interval)
        try:
            last = all_texts(driver.page_source)
        except Exception:
            break
        appeared |= last - seen
        seen |= last
    return sorted(appeared - last)  # muncul lalu hilang = toast/snackbar


def ask_claude(client, goal, elements, history):
    lines = []
    for e in elements:
        parts = [f'{e["index"]}: "{e["text"]}"']
        if e["label"]:
            parts.append(f'label="{e["label"]}"')
        if e["id"]:
            parts.append(f'id={e["id"]}')
        kind = e["class"] + (", editable" if e["editable"] else "")
        parts.append(f"({kind})")
        lines.append(" ".join(parts))
    listing = "\n".join(lines) or "(no interactable elements found)"
    recent = "\n".join(history[-8:]) or "(none yet)"
    user = (
        f"GOAL: {goal}\n\nRECENT ACTIONS:\n{recent}\n\n"
        f"INTERACTABLE ELEMENTS:\n{listing}\n\n"
        "Pick the next action as JSON."
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


def execute(driver, action, elements):
    kind = action.get("action")
    if kind == "back":
        driver.back()
        return "back"
    if kind == "stop":
        return "stop"
    idx = action.get("target")
    if idx is None or idx >= len(elements):
        return "noop (bad target)"
    x, y = elements[idx]["xy"]
    if kind == "tap":
        driver.tap([(x, y)])
        return f'tap "{elements[idx]["text"]}"'
    if kind == "input":
        try:
            driver.tap([(x, y)])
            time.sleep(0.4)
            el = driver.switch_to.active_element
            el.send_keys(action.get("text", ""))
        except Exception:
            pass
        return f'input "{action.get("text","")}"'
    if kind == "scroll":
        size = driver.get_window_size()
        driver.swipe(size["width"] // 2, int(size["height"] * 0.7),
                     size["width"] // 2, int(size["height"] * 0.3), 400)
        return "scroll"
    return "noop"


def scan_crash(driver):
    """Return crash/ANR lines newly present in logcat, if any."""
    hits = []
    try:
        for entry in driver.get_log("logcat"):
            msg = entry.get("message", "")
            if "FATAL EXCEPTION" in msg or "ANR in" in msg:
                hits.append(msg[:200])
    except Exception:
        pass
    return hits


def main():
    ap = argparse.ArgumentParser(description="Autonomous QA agent (Appium + Claude)")
    ap.add_argument("--apk", help="path APK untuk diinstal")
    ap.add_argument("--package", help="appPackage jika app sudah terinstal")
    ap.add_argument("--activity", help="appActivity (launcher)")
    ap.add_argument("--device", help="nama device/emulator (opsional)")
    ap.add_argument("--goal", default="Jelajahi app, temukan crash & bug UI.",
                    help="tujuan eksplorasi (mengarahkan agen)")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--out", default="qa_report.json")
    args = ap.parse_args()

    if not args.apk and not args.package:
        sys.exit("Wajib salah satu: --apk atau --package")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY dulu.")

    client = anthropic.Anthropic()
    print(f"[*] Menyambung Appium di {APPIUM_URL} (model: {MODEL})")
    driver = build_driver(args)
    report = {
        "goal": args.goal, "model": MODEL,
        "started": datetime.now().isoformat(),
        "steps": [], "crashes": [], "messages": [], "screens_seen": 0,
    }
    seen_screens = set()
    history = []

    try:
        for step in range(1, args.max_steps + 1):
            time.sleep(0.5)  # observe() sudah menunggu ~3s setelah aksi sebelumnya
            page = driver.page_source
            elements = parse_elements(page)
            fingerprint = hash(tuple(sorted(e["text"] + e["id"] for e in elements)))
            seen_screens.add(fingerprint)

            crashes = scan_crash(driver)
            if crashes:
                report["crashes"].extend(crashes)
                print(f"[!] CRASH/ANR terdeteksi di step {step}")

            try:
                action = ask_claude(client, args.goal, elements, history)
            except Exception as e:
                print(f"[warn] Claude/JSON error: {e}; stop.")
                break

            before_texts = all_texts(page)
            result = execute(driver, action, elements)
            transient = observe(driver, before_texts)

            line = f'step {step}: {action.get("action")} -> {result} | {action.get("reasoning","")}'
            if transient:
                line += " | PESAN APP: " + "; ".join(transient)
                report["messages"].extend(transient)
                print(f"[!] Pesan transien: {transient}")
            print("[>]", line)
            history.append(line)
            report["steps"].append({
                "step": step, "action": action, "result": result,
                "n_elements": len(elements),
                "transient_messages": transient,
            })
            if action.get("action") == "stop":
                print("[*] Agen memutuskan berhenti.")
                break

        report["screens_seen"] = len(seen_screens)
    finally:
        report["ended"] = datetime.now().isoformat()
        try:
            driver.quit()
        except Exception:
            pass
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n[+] Selesai. {len(report['steps'])} langkah, "
              f"{report['screens_seen']} layar unik, "
              f"{len(report['messages'])} pesan app, "
              f"{len(report['crashes'])} crash/ANR.")
        print(f"[+] Laporan -> {args.out}")


if __name__ == "__main__":
    main()
