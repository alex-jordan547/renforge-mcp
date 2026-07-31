from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest


def _run_validator(static_dir: Path, dist_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "scripts/validate_ui_artifacts.py",
        "--static-dir",
        str(static_dir),
    ]
    if dist_dir is not None:
        command.extend(["--dist-dir", str(dist_dir)])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).lstrip("\n"), encoding="utf-8")


def _to_bytes(data: str | bytes) -> bytes:
    return data if isinstance(data, bytes) else data.encode("utf-8")


def _write_wheel(
    path: Path,
    *,
    files: Sequence[tuple[str, str | bytes]],
    name: str = "renforge",
    version: str = "0.7.0",
) -> Path:
    wheel_path = path / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(f"{name.replace('-', '_')}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        zipf.writestr(f"{name.replace('-', '_')}.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n")
        for relpath, contents in files:
            zipf.writestr(f"renforge/ui/static/{relpath}", _to_bytes(contents))
    return wheel_path


def _write_sdist(
    path: Path,
    *,
    files: Sequence[tuple[str, str | bytes]],
    name: str = "renforge",
    version: str = "0.7.0",
) -> Path:
    sdist_path = path / f"{name}-{version}.tar.gz"
    prefix = f"{name.replace('-', '_')}-{version}/src/renforge/ui/static/"
    with tarfile.open(sdist_path, mode="w:gz") as tarf:
        for relpath, contents in files:
            data = _to_bytes(contents)
            name_in_tar = (prefix + relpath).replace("\\", "/")
            info = tarfile.TarInfo(name=name_in_tar)
            info.size = len(data)
            tarf.addfile(info, io.BytesIO(data))
    return sdist_path


def _valid_static(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write_file(
        root / "index.html",
        """
        <!doctype html>
        <html>
          <head>
            <meta charset=\"utf-8\" />
            <script type=\"module\" src=\"/assets/index.js\"></script>
            <link rel=\"stylesheet\" href=\"/assets/styles.css\" />
          </head>
          <body>
            <img src=\"/brand/renforge-mark.png\" />
            <img src=\"/brand/renforge-mascot.png\" />
          </body>
        </html>
        """,
    )
    _write_file(root / "assets" / "index.js", "console.log('/brand/renforge-mark.png');\n")
    _write_file(root / "assets" / "styles.css", "body { background-image: url('/brand/renforge-mascot.png'); }\n")
    _write_file(root / "brand" / "renforge-mark.png", "png-bytes")
    _write_file(root / "brand" / "renforge-mascot.png", "png-bytes")
    return root


def test_validate_ui_artifacts_passes_for_valid_static_and_dist_artifacts(tmp_path: Path) -> None:
    static_dir = _valid_static(tmp_path / "static")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    files = [
        ("index.html", static_dir.joinpath("index.html").read_text(encoding="utf-8")),
        ("assets/index.js", static_dir.joinpath("assets/index.js").read_text(encoding="utf-8")),
        ("assets/styles.css", static_dir.joinpath("assets/styles.css").read_text(encoding="utf-8")),
        ("brand/renforge-mark.png", static_dir.joinpath("brand/renforge-mark.png").read_text(encoding="utf-8")),
        ("brand/renforge-mascot.png", static_dir.joinpath("brand/renforge-mascot.png").read_text(encoding="utf-8")),
    ]
    _write_wheel(dist_dir, files=files)
    _write_sdist(dist_dir, files=files)

    result = _run_validator(static_dir, dist_dir)

    assert result.returncode == 0
    assert "UI artifact validation passed" in result.stdout


def test_validate_ui_artifacts_fails_for_missing_index_html(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    _write_file(static_dir / "assets" / "index.js", "console.log('ok');")

    result = _run_validator(static_dir)

    assert result.returncode == 1
    assert "index.html is missing" in result.stderr


def test_validate_ui_artifacts_validates_references_in_html_js_and_css(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(static_dir / "index.html", '<script src="/assets/index.js"></script>')
    _write_file(static_dir / "assets" / "index.js", "const logo='/brand/missing-brand.png';")
    _write_file(static_dir / "assets" / "styles.css", "body{background-image:url('/assets/missing-style.css');}")
    _write_file(static_dir / "brand" / "renforge-mark.png", "png-bytes")

    result = _run_validator(static_dir)

    assert result.returncode == 1
    assert "/assets/missing-style.css" in result.stderr
    assert "/brand/missing-brand.png" in result.stderr
    assert "ERROR" in result.stderr


def test_validate_ui_artifacts_ignores_api_ws_route_and_minified_js_strings(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(
        static_dir / "index.html",
        '<script src="/assets/index.js"></script>\n'
        '<link rel="stylesheet" href="/assets/styles.css" />',
    )
    _write_file(
        static_dir / "assets" / "index.js",
        """
        const api = '/api/project';
        const live = '/api/live/state';
        const ws = '/ws';
        const wsLookalike = '/ws-lookalike.js';
        const minified = '/$';
        const apiModule = '/api/project.js';
        const brand = '/brand/renforge-mark.png';
        const route = '/assets/fail.js';
        """,
    )
    _write_file(
        static_dir / "assets" / "styles.css",
        "body{background-image:url('/brand/renforge-mascot.png')}",
    )
    _write_file(static_dir / "brand" / "renforge-mark.png", "png-bytes")
    _write_file(static_dir / "brand" / "renforge-mascot.png", "png-bytes")
    _write_file(static_dir / "assets" / "bundle.js", "console.log(1)")

    result = _run_validator(static_dir)

    assert result.returncode == 1
    assert "/assets/fail.js" in result.stderr
    assert "/ws-lookalike.js" in result.stderr
    assert "unsupported local reference" in result.stderr
    assert "/api/project" not in result.stderr
    assert "/api/live/state" not in result.stderr
    assert "unsupported local reference '/ws'" not in result.stderr
    assert "/$" not in result.stderr


def test_validate_ui_artifacts_rejects_traversal_and_unsupported_local_refs(tmp_path: Path) -> None:

    static_dir = tmp_path / "static"
    _write_file(
        static_dir / "index.html",
        '<a href="/assets/../secrets.txt">secrets</a><img src="/favicon.ico">',
    )

    result = _run_validator(static_dir)

    assert result.returncode == 1
    assert "path traversal" in result.stderr
    assert "unsupported local reference" in result.stderr


def test_validate_ui_artifacts_sdist_and_wheel_fail_if_references_are_broken(tmp_path: Path) -> None:
    static_dir = _valid_static(tmp_path / "static")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    files = [
        ("index.html", static_dir.joinpath("index.html").read_text(encoding="utf-8")),
        ("assets/index.js", "console.log('ok');"),
        ("assets/styles.css", static_dir.joinpath("assets/styles.css").read_text(encoding="utf-8")),
        ("brand/renforge-mark.png", static_dir.joinpath("brand/renforge-mark.png").read_text(encoding="utf-8")),
        ("brand/renforge-mascot.png", static_dir.joinpath("brand/renforge-mascot.png").read_text(encoding="utf-8")),
    ]
    _write_wheel(dist_dir, files=files)
    _write_sdist(dist_dir, files=[
        ("index.html", "<html><body><img src='/assets/index.js'></body></html>"),
        ("assets/index.js", "console.log('/assets/missing.css')"),
    ])

    result = _run_validator(static_dir, dist_dir)

    assert result.returncode == 1
    assert "renforge-0.7.0.tar.gz" in result.stderr
    assert "/assets/missing.css" in result.stderr


def test_validate_ui_artifacts_ignores_external_data_and_query_hash_urls(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(
        static_dir / "index.html",
        '<script src="/assets/index.js?v=2#main"></script>\n'
        '<img src="https://example.com/logo.png" />\n'
        '<img src="data:image/png;base64,AA.." />\n',
    )
    _write_file(static_dir / "assets" / "index.js", "console.log('/brand/renforge-mascot.png');")
    _write_file(static_dir / "brand" / "renforge-mascot.png", "png-bytes")

    result = _run_validator(static_dir)

    assert result.returncode == 0
    assert "UI artifact validation passed" in result.stdout
    assert "external" not in result.stderr.lower()


def test_validate_ui_artifacts_diagnostic_output_is_deterministic(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(
        static_dir / "index.html",
        '<a href="/unsupported/local.svg"></a><img src="/assets/../traversal.js">',
    )

    result = _run_validator(static_dir)

    assert result.returncode == 1
    errors = [line for line in result.stderr.splitlines() if line.startswith("ERROR")]
    assert errors == sorted(errors)
    assert len(errors) >= 2


def test_validate_ui_artifacts_rejects_duplicate_wheel_static_members(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(static_dir / "index.html", '<script src="/assets/index.js"></script>')
    _write_file(static_dir / "assets" / "index.js", "console.log('ok')")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    with pytest.warns(UserWarning, match="Duplicate name"):
        _write_wheel(
            dist_dir,
            files=[
                ("index.html", '<script src="/assets/index.js"></script>'),
                ("assets/index.js", "console.log('first')"),
                ("assets/index.js", "console.log('duplicate')"),
            ],
        )

    result = _run_validator(static_dir, dist_dir)

    assert result.returncode == 1
    assert "duplicate static member 'renforge/ui/static/assets/index.js'" in result.stderr


def test_validate_ui_artifacts_rejects_duplicate_sdist_static_members(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(static_dir / "index.html", '<script src="/assets/index.js"></script>')
    _write_file(static_dir / "assets" / "index.js", "console.log('ok')")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    _write_sdist(
        dist_dir,
        files=[
            ("index.html", '<script src="/assets/index.js"></script>'),
            ("assets/index.js", "console.log('first')"),
            ("assets/index.js", "console.log('duplicate')"),
        ],
    )

    result = _run_validator(static_dir, dist_dir)

    assert result.returncode == 1
    assert "duplicate static member" in result.stderr
    assert "src/renforge/ui/static/assets/index.js" in result.stderr


def test_validate_ui_artifacts_rejects_empty_dist_directory(tmp_path: Path) -> None:
    static_dir = _valid_static(tmp_path / "static")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    result = _run_validator(static_dir, dist_dir)

    assert result.returncode == 1
    assert "no wheel or sdist artifacts found" in result.stderr


def test_validate_ui_artifacts_rejects_extensionless_root_html_reference(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    _write_file(static_dir / "index.html", '<img src="/missing-image">')

    result = _run_validator(static_dir)

    assert result.returncode == 1
    assert "unsupported local reference '/missing-image'" in result.stderr
