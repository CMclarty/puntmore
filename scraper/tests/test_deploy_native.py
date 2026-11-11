"""The native deploy files, read as text (deploy/native/): the Scraper's Chrome
runs on its own virtual screen, puntmore-screen.service (a headless sway with
its own Xwayland), never on Callum's desktop. Nothing here starts a unit."""
import re
import subprocess
from pathlib import Path

NATIVE = Path(__file__).resolve().parent.parent / "deploy/native"
ENV_FILE = "puntmore-screen.env"


def _unit(name):
    """{key: [every value it is given]}, comments and section headers left out."""
    keys = {}
    for line in (NATIVE / name).read_text().splitlines():
        line = line.strip()
        if not line or line[0] in "#;[" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def _words(unit, key):
    return [w for v in unit.get(key, []) for w in v.split()]


def _conf_lines():
    return [l.strip() for l in (NATIVE / "screen.conf").read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def test_the_scraper_runs_on_the_screen_not_the_desktop():
    unit = _unit("puntmore-scraper.service")
    for key in ("After", "PartOf", "Requires"):
        assert "puntmore-screen.service" in _words(unit, key), key
    assert f"%t/{ENV_FILE}" in unit["EnvironmentFile"]
    assert "/opt/puntmore-scraper/.env" in unit["EnvironmentFile"]    # still there
    assert set(_words(unit, "UnsetEnvironment")) >= {"WAYLAND_DISPLAY", "XAUTHORITY"}
    assert "DISPLAY" not in _words(unit, "UnsetEnvironment")         # the screen's, from the file
    assert unit["WantedBy"] == ["default.target"]
    assert "graphical-session" not in (NATIVE / "puntmore-scraper.service").read_text()


def test_the_screen_unit_hides_the_desktop_and_waits_for_its_display():
    unit = _unit("puntmore-screen.service")
    assert unit["Type"] == ["simple"]
    assert set(_words(unit, "Environment")) >= {"WLR_BACKENDS=headless", "WLR_LIBINPUT_NO_DEVICES=1"}
    assert set(_words(unit, "UnsetEnvironment")) >= {"WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "SWAYSOCK"}
    assert unit["ExecStartPre"] == [f"/bin/rm -f %t/{ENV_FILE}"]
    assert unit["ExecStart"] == ["/usr/bin/sway -c %h/Projects/puntmore/scraper/deploy/native/screen.conf"]
    (post,) = unit["ExecStartPost"]
    assert f"-s %t/{ENV_FILE}" in post and "exit 1" in post           # waits, then gives up
    assert "$" not in post.replace("$$", "")                          # systemd would expand it
    assert unit["Restart"] == ["always"] and unit["RestartSec"] == ["5"]
    assert unit["WantedBy"] == ["default.target"]


def test_the_wait_gives_up_in_about_fifteen_seconds(tmp_path):
    """ExecStartPost run as sh would run it, with %t pointing at a temp dir."""
    (post,) = _unit("puntmore-screen.service")["ExecStartPost"]
    cmd = post.replace("%t", str(tmp_path)).replace("$$", "$")
    m = re.fullmatch(r"/bin/sh -c '(.*)'", cmd)
    assert m, cmd
    script = m.group(1)
    assert script.count("sleep 1") == 1 and len(re.findall(r"\b\d+\b", script.split(";")[0])) == 15
    (tmp_path / ENV_FILE).write_text("DISPLAY=:2\n")
    assert subprocess.run(["sh", "-c", script], timeout=5).returncode == 0


def test_screen_conf_is_the_spikes_screen():
    lines = _conf_lines()
    for want in ("output HEADLESS-1 resolution 1920x1080 position 0 0", "xwayland enable",
                 "default_border none", "focus_follows_mouse no"):
        assert want in lines


def test_screen_conf_writes_display_to_the_units_file():
    lines = _conf_lines()
    assert ("exec sh -c 'printf \"DISPLAY=%s\\n\" \"$DISPLAY\" > "
            f"\"$XDG_RUNTIME_DIR/{ENV_FILE}\"'") in lines


def test_vnc_listens_on_this_machine_only():
    """No password on it: any address but 127.0.0.1 would hand the screen out."""
    vnc = [l for l in _conf_lines() if "wayvnc" in l]
    assert vnc == ["exec wayvnc 127.0.0.1 5910"]
    for line in vnc:
        args = line.split()[2:]
        assert args and args[0] == "127.0.0.1", line              # not a bare port, not 0.0.0.0
        assert "0.0.0.0" not in line and "::" not in line


def test_install_sh_parses_and_installs_both_units():
    sh = NATIVE / "install.sh"
    assert subprocess.run(["sh", "-n", str(sh)]).returncode == 0
    text = sh.read_text()
    assert "puntmore-screen.service" in text and "puntmore-scraper.service" in text
    assert "command -v sway" in text and "command -v wayvnc" in text
    assert "sudo apt install sway wayvnc" in text
    assert "remmina -c vnc://127.0.0.1:5910" in text
    assert "dconf reset /org/gnome/shell/extensions/auto-move-windows/application-list" in text
    assert "systemctl --user disable" in text                # the old graphical-session link
    assert text.index("systemctl --user disable") < text.index("systemctl --user enable")
    assert "cp \"$HERE/puntmore-scraper.desktop\"" not in text
    assert "rm -f \"$APPS/puntmore-scraper.desktop\"" in text
    assert not (NATIVE / "puntmore-scraper.desktop").exists()
