"""Check local Markdown links in researcher docs and their companion root pages.

The root README is maintained separately. Release kernel links can resolve against
a read-only sibling checkout until the aerokernel subtree is included.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT_PAGES = ("INSTALL.md", "ASSETS.md", "CONTRIBUTING.md", "CHANGELOG.md", "README_CN.md")
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)", re.MULTILINE)
REFERENCE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
HTML_LINK = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']")


def prose(source: str) -> str:
    """Keep line positions while excluding fenced code examples."""
    lines = []
    fence = ""
    for line in source.splitlines(keepends=True):
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if marker and (not fence or marker[1][0] == fence):
            fence = "" if fence else marker[1][0]
            lines.append("\n")
        else:
            lines.append("\n" if fence else line)
    return "".join(lines)


def anchors(path: Path) -> set[str]:
    content = prose(path.read_text(encoding="utf-8"))
    result = set(re.findall(r"\bid=[\"']([^\"']+)[\"']", content))
    counts: dict[str, int] = {}
    for line in content.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not heading:
            continue
        label = re.sub(r"<[^>]+>", "", heading[1])
        label = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", label)
        slug = re.sub(r"[^\w\-\s]", "", html.unescape(label).lower())
        slug = re.sub(r"\s", "-", slug)
        number = counts.get(slug, 0)
        result.add(slug if number == 0 else f"{slug}-{number}")
        counts[slug] = number + 1
    return result


def check(root: Path, files: list[Path]) -> int:
    failures = []
    links = 0
    kernel_fallback = False
    for source in files:
        content = prose(source.read_text(encoding="utf-8"))
        definitions = {m[1].casefold(): m[2] for m in DEFINITION.finditer(content)}
        candidates = [(m.start(), m[1]) for m in LINK.finditer(content)]
        candidates += [(m.start(), m[2]) for m in DEFINITION.finditer(content)]
        candidates += [(m.start(), m[1]) for m in HTML_LINK.finditer(content)]
        for match in REFERENCE.finditer(content):
            key = (match[2] or match[1]).casefold()
            if key not in definitions:
                failures.append(f"{source.relative_to(root)}: undefined reference [{key}]")
        for offset, target in candidates:
            target = html.unescape(target.strip("<>"))
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            links += 1
            path = (
                (root / unquote(url.path).lstrip("/"))
                if url.path.startswith("/")
                else (source.parent / unquote(url.path)) if url.path else source
            ).resolve()
            kernel = root / "aerokernel"
            if not path.exists() and path.is_relative_to(kernel):
                alternate = root.parent / "aerokernel" / path.relative_to(kernel)
                if alternate.exists():
                    path = alternate
                    kernel_fallback = True
            reason = ""
            if not path.exists():
                reason = "missing target"
            elif (
                url.fragment
                and path.suffix.lower() == ".md"
                and unquote(url.fragment) not in anchors(path)
            ):
                reason = "missing heading"
            if reason:
                line = content[:offset].count("\n") + 1
                failures.append(f"{source.relative_to(root)}:{line}: {reason}: {target}")
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        print(f"{len(failures)} broken links", file=sys.stderr)
        return 1
    print(f"Checked {links} local links in {len(files)} Markdown files: clean.")
    if kernel_fallback:
        print("Release kernel links checked against the sibling aerokernel checkout.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve()
    files = sorted((root / "docs").rglob("*.md"))
    files += [root / name for name in ROOT_PAGES if (root / name).is_file()]
    return check(root, files)


if __name__ == "__main__":
    raise SystemExit(main())
