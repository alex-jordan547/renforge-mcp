#!/usr/bin/env python3
"""Validate generated UI artifacts for safe static references.

Checks:
- Source static directory exists with index.html.
- HTML/JS/CSS in the static directory and distribution artifacts only reference
  local paths under /assets/ or /brand/.
- Every supported local path exists in the checked artifact.
"""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile
import re
import sys

SUPPORTED_PREFIXES = ("/assets/", "/brand/")
IGNORED_ROUTE_PREFIXES = ("/api/", "/ws", "/ws/")

HTML_ATTR_RE = re.compile(r"\b(?:href|src)\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
QUOTED_ROOT_REF_RE = re.compile(r"(?<![A-Za-z0-9_])(['\"])(/[^'\"\s]+)\1")


def _extract_html_refs(text: str) -> set[str]:
    return {value for _, value in HTML_ATTR_RE.findall(text)}


def _extract_css_refs(text: str) -> set[str]:
    refs: set[str] = {value for _, value in CSS_URL_RE.findall(text)}
    refs.update(value for _, value in QUOTED_ROOT_REF_RE.findall(text))
    return refs


def _extract_js_refs(text: str) -> set[str]:
    return {value for _, value in QUOTED_ROOT_REF_RE.findall(text)}


def _extract_refs(text: str, *, file_path: Path | str = "") -> set[str]:
    if str(file_path).endswith(".html"):
        return _extract_html_refs(text)

    if str(file_path).endswith(".css"):
        return _extract_css_refs(text)

    if str(file_path).endswith(".js"):
        return _extract_js_refs(text)

    return set()


def _is_local_ref(ref: str) -> bool:
    if not ref.startswith("/"):
        return False
    if ref.startswith("//"):
        return False

    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc:
        return False

    return True


def _is_traversal(path: str) -> bool:
    parsed = urlsplit(path)
    parts = [part for part in parsed.path.split("/") if part]
    return ".." in parts


def _is_supported(path: str) -> bool:
    return path.startswith(SUPPORTED_PREFIXES)


def _is_ignored_route(path: str) -> bool:
    return path.startswith(IGNORED_ROUTE_PREFIXES)


def _looks_like_asset_path(path: str) -> bool:
    return Path(path).suffix != ""


def _iter_texts_from_static(root: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        if suffix not in {".html", ".js", ".css"}:
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out.append((file_path, text))

    return out


def _validate_text_refs(
    source: str,
    refs: set[str],
    available: set[str],
    errors: list[str],
) -> None:
    for raw in sorted(refs):
        if not _is_local_ref(raw):
            continue

        if _is_traversal(raw):
            errors.append(f"{source}: path traversal in local reference '{raw}'")
            continue

        parsed = urlsplit(raw)
        path = parsed.path

        if _is_ignored_route(path):
            continue

        if not _is_supported(path) and _looks_like_asset_path(path):
            errors.append(
                f"{source}: unsupported local reference '{raw}' (only /assets and /brand are allowed)"
            )
            continue

        if not _is_supported(path):
            continue

        rel = path.lstrip("/")
        if rel not in available:
            errors.append(f"{source}: missing static file '{raw}'")


def validate_static_dir(static_dir: Path, errors: list[str]) -> bool:
    if not static_dir.exists():
        errors.append(f"static-dir not found: {static_dir}")
        return False

    index = static_dir / "index.html"
    if not index.exists():
        errors.append(f"{index}: index.html is missing")
        return False

    available = {path.relative_to(static_dir).as_posix() for path in static_dir.rglob("*") if path.is_file()}
    for file_path, text in _iter_texts_from_static(static_dir):
        refs = _extract_refs(text, file_path=file_path)
        _validate_text_refs(str(file_path), refs, available, errors)

    return True


def _read_text_from_zip_member(zf: ZipFile, name: str) -> str | None:
    try:
        raw = zf.read(name)
    except KeyError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def validate_wheel(artifact: Path, errors: list[str]) -> bool:
    valid = True
    with ZipFile(artifact) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        static_prefix = "renforge/ui/static/"
        static_names = [name for name in names if name.startswith(static_prefix)]
        if not static_names:
            errors.append(f"{artifact}: no bundled static dir at renforge/ui/static/")
            return False

        available = {name[len(static_prefix):] for name in static_names}
        if f"{static_prefix}index.html" not in static_names:
            errors.append(f"{artifact}: bundled static index.html is missing")
            valid = False

        source_members = [name for name in static_names if name.endswith((".html", ".js", ".css"))]
        for member in source_members:
            text = _read_text_from_zip_member(zf, member)
            if text is None:
                continue
            refs = _extract_refs(text, file_path=member)
            _validate_text_refs(f"{artifact}:{member}", refs, available, errors)

    return valid


def validate_sdist(artifact: Path, errors: list[str]) -> bool:
    valid = True
    with tarfile.open(artifact, "r:gz") as tf:
        names = [info.name.replace("\\", "/") for info in tf.getmembers() if info.isfile()]
        marker = "src/renforge/ui/static/index.html"
        roots = sorted({name[: -len(marker)] for name in names if name.endswith(marker)})

        if not roots:
            errors.append(f"{artifact}: bundled static index.html is missing")
            return False

        for root in roots:
            prefix = f"{root}src/renforge/ui/static/"
            names_for_root = [name for name in names if name.startswith(prefix)]
            available = {
                name[len(prefix):]
                for name in names_for_root
                if name != prefix and not name.endswith("/")
            }

            if f"{prefix}index.html" not in names_for_root:
                errors.append(f"{artifact}: bundled static index.html is missing in {prefix}")
                valid = False

            source_members = [name for name in names_for_root if name.endswith((".html", ".js", ".css"))]
            for member in source_members:
                member_info = tf.getmember(member)
                stream = tf.extractfile(member_info)
                if stream is None:
                    errors.append(f"{artifact}: unable to read {member}")
                    valid = False
                    continue

                with stream:
                    try:
                        text = stream.read().decode("utf-8")
                    except UnicodeDecodeError:
                        continue

                    refs = _extract_refs(text, file_path=member)
                    _validate_text_refs(f"{artifact}:{member}", refs, available, errors)

    return valid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate UI artifacts.")
    parser.add_argument("--static-dir", required=True, type=Path)
    parser.add_argument("--dist-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []

    validate_static_dir(args.static_dir, errors)

    if args.dist_dir is not None:
        if not args.dist_dir.exists():
            errors.append(f"dist-dir not found: {args.dist_dir}")
        else:
            for artifact in sorted(args.dist_dir.glob("*")):
                if artifact.suffix == ".whl":
                    validate_wheel(artifact, errors)
                elif artifact.suffixes[-2:] == [".tar", ".gz"]:
                    validate_sdist(artifact, errors)

    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"UI artifact validation found {len(errors)} issue(s).", file=sys.stderr)
        return 1

    print("UI artifact validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
