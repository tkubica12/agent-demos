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
        parts = {}
        with ZipFile(file) as archive:
            required = ("word/document.xml", "[Content_Types].xml", "_rels/.rels")
            if not set(required).issubset(archive.namelist()):
                raise ValueError("Incomplete Word package.")
            for name in required:
                with archive.open(name) as document:
                    xml = document.read(4 * 1024 * 1024 + 1)
                if len(xml) > 4 * 1024 * 1024 or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                    raise ValueError("Invalid or oversized Word package part.")
                parts[name] = ElementTree.fromstring(xml)
        content_namespace = "{http://schemas.openxmlformats.org/package/2006/content-types}"
        relationship_namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        has_content_type = any(
            node.get("PartName") == "/word/document.xml"
            and node.get("ContentType") ==
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            for node in parts["[Content_Types].xml"].iter(f"{content_namespace}Override")
        )
        has_relationship = any(
            node.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
            and node.get("Target", "").lstrip("/") == "word/document.xml"
            and node.get("TargetMode") != "External"
            for node in parts["_rels/.rels"].iter(f"{relationship_namespace}Relationship")
        )
        if not has_content_type or not has_relationship:
            raise ValueError("Invalid Word package content type or document relationship.")
        root = parts["word/document.xml"]
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
