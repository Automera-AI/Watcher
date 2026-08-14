"""Generate SESSION-SUMMARY-2026-08-14.pdf without runtime dependencies."""

from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE = ROOT / "SESSION-SUMMARY-2026-08-14.md"
OUTPUT = ROOT / "SESSION-SUMMARY-2026-08-14.pdf"


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _plain_lines(markdown: str) -> list[tuple[str, bool]]:
    lines: list[tuple[str, bool]] = []
    for raw in markdown.splitlines():
        if not raw.strip():
            lines.append(("", False))
            continue
        heading = raw.startswith("#")
        text = raw.lstrip("# ").replace("**", "").replace("`", "")
        prefix = "- " if text.startswith("- ") else ""
        body = text[2:] if prefix else text
        width = 76 if heading else 92
        wrapped = textwrap.wrap(body, width=width) or [""]
        for index, part in enumerate(wrapped):
            lines.append(((prefix if index == 0 else "  ") + part, heading))
    return lines


def _content_stream(page: list[tuple[str, bool]]) -> bytes:
    commands = ["BT", "/F1 10 Tf", "50 790 Td"]
    for text, heading in page:
        commands.append("/F1 14 Tf" if heading else "/F1 10 Tf")
        commands.append(f"({_pdf_escape(text)}) Tj")
        commands.append("0 -16 Td" if heading else "0 -13 Td")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def build() -> None:
    lines = _plain_lines(SOURCE.read_text(encoding="utf-8"))
    pages = [lines[index : index + 52] for index in range(0, len(lines), 52)]
    objects: list[bytes] = []
    page_ids = [4 + index * 2 for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, page in enumerate(pages):
        page_id = page_ids[index]
        stream_id = page_id + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>"
            ).encode()
        )
        stream = _content_stream(page)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    OUTPUT.write_bytes(result)


if __name__ == "__main__":
    build()
