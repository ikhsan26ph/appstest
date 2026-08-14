#!/usr/bin/env python3
"""Transport Appium seadanya — W3C di atas urllib, tanpa dependensi baru.

Sengaja tipis dan tanpa logika QA: yang tahu benar-salah adalah oracle.py,
yang tahu bentuk layar adalah uitree.py, modul ini cuma tangan.

Mengetik lewat endpoint `element/{id}/value` milik Appium, BUKAN
`adb shell input text`. Alasannya terukur: adbd mencatat perintah shell utuh
ke logcat —

    I adbd : adbd service requested 'shell,...,raw:input text 085155070869'

sehingga setiap nilai uji (dan tiap kredensial) ikut tercecer di log, lalu
tertangkap oleh pemindai kebocoran kita sendiri sebagai temuan palsu.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

W3C_ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


class AppiumError(RuntimeError):
    pass


class Appium:
    def __init__(self, udid: str, package: str, activity: str,
                 url: str = "http://127.0.0.1:4723"):
        self.url = url.rstrip("/")
        self.udid, self.package, self.activity = udid, package, activity
        self.session: str | None = None

    # ---------------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def _sess(self, method: str, path: str, body: dict | None = None):
        if not self.session:
            raise AppiumError("sesi belum dibuka — panggil start() dulu")
        res = self._req(method, f"/session/{self.session}{path}", body)
        val = res.get("value")
        if isinstance(val, dict) and "error" in val:
            raise AppiumError(f"{val['error']}: {str(val.get('message'))[:200]}")
        return val

    # ---------------------------------------------------------------
    def start(self) -> str:
        caps = {"capabilities": {"alwaysMatch": {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": self.udid,
            "appium:appPackage": self.package,
            "appium:appActivity": self.activity,
            "appium:noReset": True,
            "appium:newCommandTimeout": 900,
            # tiga nilai ini wajib di Galaxy A52: pasang server APK ~17 detik,
            # sedangkan bawaan Appium cuma 20 detik dan sesi pertama selalu gagal
            "appium:uiautomator2ServerInstallTimeout": 120000,
            "appium:uiautomator2ServerLaunchTimeout": 90000,
            "appium:adbExecTimeout": 120000,
        }, "firstMatch": [{}]}}
        val = self._req("POST", "/session", caps).get("value", {})
        if "sessionId" not in val:
            raise AppiumError(f"gagal membuka sesi: {json.dumps(val)[:300]}")
        self.session = val["sessionId"]
        return self.session

    def stop(self) -> None:
        if self.session:
            self._req("DELETE", f"/session/{self.session}")
            self.session = None

    # ---------------------------------------------------------------
    def source(self) -> str:
        return self._sess("GET", "/source")

    def back(self) -> None:
        self._sess("POST", "/back", {})

    def tap(self, x: int, y: int) -> None:
        self._sess("POST", "/actions", {"actions": [{
            "type": "pointer", "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 60},
                {"type": "pointerUp", "button": 0},
            ]}]})

    # ---------------------------------------------------------------
    def editable(self, nth: int) -> str:
        """Element id EditText ke-`nth` (0-based) menurut urutan dokumen.

        Field di app ini tidak punya resource-id, dan XPath atas urutan
        dokumen sama persis dengan urutan yang dikembalikan
        uitree.parse_elements() — jadi indeks dari sana bisa dipakai langsung.
        """
        val = self._sess("POST", "/element", {
            "using": "xpath", "value": f"(//android.widget.EditText)[{nth + 1}]"})
        return val[W3C_ELEMENT]

    def clear(self, element_id: str) -> None:
        self._sess("POST", f"/element/{element_id}/clear", {})

    def type_into(self, element_id: str, text: str) -> None:
        """Isi field. Nilai tidak pernah melewati shell, jadi tidak masuk logcat."""
        self._sess("POST", f"/element/{element_id}/value", {"text": text})

    def hide_keyboard(self) -> None:
        try:
            self._sess("POST", "/appium/device/hide_keyboard", {})
        except AppiumError:
            pass          # keyboard memang sedang tidak tampil

    # ---------------------------------------------------------------
    def __enter__(self):
        self.start()
        time.sleep(1.0)
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
