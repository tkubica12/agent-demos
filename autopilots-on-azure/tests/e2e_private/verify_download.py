"""Validate the actual browser-downloaded Word/TXT content, not just API bytes."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


def verify_download(file: Path, nonce: str):
    if not nonce:
        raise ValueError("A fresh nonempty verification nonce is required.")
    if not 0 < file.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("Acceptance downloads must be nonempty and at most 16 MiB.")
    kind = file.suffix.lower()
    if kind == ".txt":
        text = file.read_text(encoding="utf-8")
    elif kind == ".docx":
        with ZipFile(file) as archive:
            with archive.open("word/document.xml") as document:
                xml = document.read(4 * 1024 * 1024 + 1)
        if len(xml) > 4 * 1024 * 1024 or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("Invalid or oversized Word document part.")
        root = ElementTree.fromstring(xml)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        if root.tag != f"{namespace}document":
            raise ValueError("The downloaded file is not a Word document.")
        text = "".join(node.text or "" for node in root.iter(f"{namespace}t"))
    else:
        raise ValueError("Expected an actual DOCX or UTF-8 TXT download.")
    if nonce not in text:
        raise ValueError("The fresh verification nonce is absent from document text.")
    data = file.read_bytes()
    return {"kind": kind[1:], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    print(json.dumps(verify_download(args.file, args.nonce)))
