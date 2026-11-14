"""Generate bounded TXT and DOCX uploads without adding dependencies."""

import argparse
from pathlib import Path
import secrets
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def create_fixtures(directory: Path, nonce: str):
    directory.mkdir(parents=True, exist_ok=True)
    codes = {name: f"{nonce}-{secrets.token_hex(16)}" for name in ("acceptance.txt", "acceptance.docx")}
    text = f"Private acceptance document. Verification code: {codes['acceptance.txt']}."
    (directory / "acceptance.txt").write_text(text, encoding="utf-8")
    text = f"Private acceptance document. Verification code: {codes['acceptance.docx']}."
    with ZipFile(directory / "acceptance.docx", "w", ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml",
                     '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
        docx.writestr("_rels/.rels",
                     '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                     'Target="word/document.xml"/></Relationships>')
        docx.writestr("word/document.xml",
                     '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                     f'<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>')
    return codes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("nonce")
    args = parser.parse_args()
    create_fixtures(args.directory, args.nonce)
