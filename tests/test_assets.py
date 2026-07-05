from pathlib import Path

from renforge.assets import analyze_assets


def _make_project(tmp_path: Path) -> Path:
    game = tmp_path / "proj" / "game"
    images = game / "images"
    audio = game / "audio"
    images.mkdir(parents=True)
    audio.mkdir(parents=True)

    # A shown image (referenced via `scene`), an orphan, and used audio.
    (images / "bg room.png").write_bytes(b"\x89PNG")
    (images / "orphan.png").write_bytes(b"\x89PNG")
    (audio / "theme.ogg").write_bytes(b"OggS")

    (game / "script.rpy").write_text(
        "\n".join(
            [
                "label start:",
                "    scene bg room",
                '    play music "theme.ogg"',
                '    play sound "missing.ogg"',
                "    show hero happy",
                "    return",
            ]
        )
    )
    return tmp_path / "proj"


def test_analyze_assets_finds_orphans_missing_and_undefined(tmp_path: Path) -> None:
    result = analyze_assets(_make_project(tmp_path))

    assert "images/orphan.png" in result["orphans"]
    assert "images/bg room.png" not in result["orphans"]  # referenced via `scene`
    assert "audio/theme.ogg" not in result["orphans"]      # referenced via `play music`

    assert "missing.ogg" in result["missing_files"]
    assert "hero happy" in result["undefined_images"]

    summary = result["summary"]
    assert summary["asset_count"] == 3
    assert summary["orphan_count"] == len(result["orphans"])


def test_analyze_assets_handles_missing_game_dir(tmp_path: Path) -> None:
    result = analyze_assets(tmp_path / "nope")
    assert "error" in result
