"""Booting the API must not load the OCR vendors' SDKs.

Measured 26 Oct 2026 on the production requirements: a bare boot was 145 MB
resident, of which google-genai was 49 MB and the anthropic SDK (897 modules)
16 MB — both imported at the top of their provider modules, so every boot paid
for them whether or not that provider was in use (Gemini never is in
production). They are imported when a provider is constructed instead. The
instance has 512 MB; it has been restarted for memory before (see
test_memory_bounds.py).
"""
import subprocess
import sys


def test_booting_the_api_does_not_import_the_ocr_sdks():
    code = (
        "import sys, app.main, app.asgi; "
        "print(sorted(m for m in ('anthropic', 'google.genai') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


def test_the_diagnostics_snapshot_reports_the_process():
    from app.diagnostics import snapshot, trim_memory
    ps = snapshot()["process"]
    assert ps["rss_mb"] is None or ps["rss_mb"] > 10
    assert ps["objects"] > 1000 and ps["py_blocks"] > 1000 and ps["threads"] >= 1
    assert trim_memory() in (True, False)      # never raises, glibc or not
