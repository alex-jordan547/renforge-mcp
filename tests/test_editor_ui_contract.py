"""Contract tests for the editor overlay's UI sources.

Each test here exists because the failure it describes actually happened and no
existing test noticed. They check the seams between the region screens, the
translation catalogues, and the repository itself — the places where a panel can
be perfectly written and still never reach a user.
"""

import json
import pathlib
import re
import subprocess

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
BRIDGE_DIR = BASE_DIR / "src" / "renforge" / "bridge"
SCREENS_DIR = BRIDGE_DIR / "screens"
EDITOR_RPY = BRIDGE_DIR / "editor.rpy"
LOCALES_DIR = BRIDGE_DIR / "editor_assets" / "locales"
EN_JSON = LOCALES_DIR / "en.json"
ZH_CN_JSON = LOCALES_DIR / "zh-CN.json"

# Built by the screens as _renforge_editor_t("lock.%s" % level), so no literal
# scan will ever see them.
DYNAMIC_KEYS = {"lock.locked", "lock.blocked", "lock.refused"}

_USED_KEY = re.compile(r'_renforge_editor_t\(\s*["\']([^"\']+)["\']\s*\)')


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=BASE_DIR, capture_output=True, text=True
    )


def _builtin_keys(editor_content: str) -> set[str]:
    """Keys of the _RF_UI_STRINGS fallback dict living inside editor.rpy."""
    start = editor_content.find("_RF_UI_STRINGS = {")
    assert start != -1, f"_RF_UI_STRINGS not found in {EDITOR_RPY}"
    depth = 0
    for index in range(start, len(editor_content)):
        if editor_content[index] == "{":
            depth += 1
        elif editor_content[index] == "}":
            depth -= 1
            if depth == 0:
                body = editor_content[start : index + 1]
                return set(re.findall(r'["\']([^"\']+)["\']\s*:', body))
    raise AssertionError("unterminated _RF_UI_STRINGS literal")


def test_screens_directory_is_not_ignored():
    """A bare `screens` rule in .gitignore once hid every panel from the repository.

    The panels existed on one disk and nowhere else; a fresh clone would have
    built its payload from editor.rpy alone and crashed on the first `use`.

    The probe path matters: git never applies ignore rules to files it already
    tracks, so asking about the committed panels would answer "not ignored"
    even with the bad rule restored. Only an untracked path tells the truth.
    """
    assert SCREENS_DIR.is_dir(), f"{SCREENS_DIR} does not exist"

    probe = SCREENS_DIR / "__ignore_probe__.rpy"
    assert not probe.exists(), "probe path unexpectedly exists on disk"
    ignored = _git("check-ignore", "-v", str(probe))
    assert ignored.returncode != 0, (
        "new .rpy files under src/renforge/bridge/screens/ would be ignored by git: "
        f"{ignored.stdout.strip()}"
    )


def test_every_panel_file_is_tracked():
    """A panel that is not committed ships to nobody."""
    panels = sorted(SCREENS_DIR.glob("*.rpy"))
    assert panels, f"no .rpy files found in {SCREENS_DIR}"

    untracked = [
        path.name
        for path in panels
        if not _git("ls-files", "--error-unmatch", str(path)).returncode == 0
    ]
    assert not untracked, f"panel files not tracked by git: {untracked}"


def test_every_used_translation_key_exists_everywhere():
    """A missing key renders as the key itself, which reads fine in English only.

    _renforge_editor_t falls back to the key, so an absent entry is invisible in
    the language we develop in and shows raw identifiers in every other one.
    """
    editor_content = EDITOR_RPY.read_text(encoding="utf-8")

    used = set(DYNAMIC_KEYS)
    used |= set(_USED_KEY.findall(editor_content))
    for path in SCREENS_DIR.glob("*.rpy"):
        used |= set(_USED_KEY.findall(path.read_text(encoding="utf-8")))

    catalogues = {
        EN_JSON.name: json.loads(EN_JSON.read_text(encoding="utf-8")),
        ZH_CN_JSON.name: json.loads(ZH_CN_JSON.read_text(encoding="utf-8")),
        "_RF_UI_STRINGS": _builtin_keys(editor_content),
    }

    missing = [
        f"{key!r} missing from {where}"
        for key in sorted(used)
        for where, catalogue in catalogues.items()
        if key not in catalogue
    ]
    assert not missing, "translation keys missing:\n" + "\n".join(missing)


def test_every_region_screen_is_used():
    """A screen nobody invokes is a file that renders nothing."""
    panels = sorted(SCREENS_DIR.glob("*.rpy"))
    assert panels, f"no .rpy files found in {SCREENS_DIR}"

    sources = [EDITOR_RPY.read_text(encoding="utf-8")]
    defined: list[tuple[str, str]] = []
    for path in panels:
        content = path.read_text(encoding="utf-8")
        sources.append(content)
        defined += [
            (name, path.name)
            for name in re.findall(r"^screen\s+(_rf_\w+)\s*\(", content, re.M)
        ]
    assert defined, f"no `screen _rf_...` definitions found in {SCREENS_DIR}"

    combined = "\n".join(sources)
    unwired = [
        f"{name!r} defined in {file_name} is never reached by a `use`"
        for name, file_name in defined
        if not re.search(r"\buse\s+" + re.escape(name) + r"\b", combined)
    ]
    assert not unwired, "unwired screens:\n" + "\n".join(unwired)


def test_catalogues_agree_on_their_keys():
    """Parity is what stops one language quietly drifting behind the other."""
    english = json.loads(EN_JSON.read_text(encoding="utf-8"))
    chinese = json.loads(ZH_CN_JSON.read_text(encoding="utf-8"))

    problems = []
    only_english = sorted(set(english) - set(chinese))
    only_chinese = sorted(set(chinese) - set(english))
    if only_english:
        problems.append(f"in {EN_JSON.name} but not {ZH_CN_JSON.name}: {only_english}")
    if only_chinese:
        problems.append(f"in {ZH_CN_JSON.name} but not {EN_JSON.name}: {only_chinese}")

    for name, catalogue in ((EN_JSON.name, english), (ZH_CN_JSON.name, chinese)):
        for key, value in catalogue.items():
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{key!r} in {name} is empty: {value!r}")

    assert not problems, "catalogue mismatch:\n" + "\n".join(problems)
