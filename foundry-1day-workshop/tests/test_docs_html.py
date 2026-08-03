"""Validation for attendee-facing HTML: structure, policy and rendered behaviour."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_ROOTS = (REPO_ROOT / "docs", REPO_ROOT / "templates")

EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x1F000, 0x1F2FF),
    (0x2600, 0x27BF),
    (0xFE0F, 0xFE0F),
)

THIRD_PERSON_NARRATION = (
    "students will",
    "student will",
    "the attendee should",
    "attendees will",
    "participants will",
    "the student should",
)


def html_documents() -> list[Path]:
    documents: list[Path] = []
    for root in HTML_ROOTS:
        if root.is_dir():
            documents.extend(sorted(root.rglob("*.html")))
    return documents


DOCUMENTS = html_documents()


class DocumentIndex(HTMLParser):
    """Collect the identifiers, links and asset references of one document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.anchors: list[str] = []
        self.assets: list[str] = []
        self.lang: str | None = None
        self.has_viewport = False
        self.has_title = False
        self.images: list[dict[str, str]] = []
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}

        if "id" in attributes:
            self.ids.add(attributes["id"])

        if tag == "html":
            self.lang = attributes.get("lang")
        elif tag == "title":
            self._in_title = True
            self.has_title = True
        elif tag == "meta" and attributes.get("name") == "viewport":
            self.has_viewport = True
        elif tag == "a":
            href = attributes.get("href", "")
            if href.startswith("#") and len(href) > 1:
                self.anchors.append(href[1:])
        elif tag == "link" and "stylesheet" in attributes.get("rel", ""):
            self.assets.append(attributes.get("href", ""))
        elif tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"])
        elif tag == "img":
            self.images.append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def index_of(path: Path) -> DocumentIndex:
    parser = DocumentIndex()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def test_attendee_html_exists() -> None:
    assert DOCUMENTS, "no attendee-facing HTML was found under docs/ or templates/"


def test_shared_assets_exist() -> None:
    assets = REPO_ROOT / "docs" / "assets"
    assert (assets / "workshop.css").is_file()
    assert (assets / "workshop.js").is_file()


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_document_structure(document: Path) -> None:
    index = index_of(document)
    assert index.lang, f"{document.name} does not declare a document language"
    assert index.has_title and index.title.strip(), f"{document.name} has no title"
    assert index.has_viewport, f"{document.name} has no viewport meta element"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_internal_anchors_resolve(document: Path) -> None:
    index = index_of(document)
    missing = sorted({anchor for anchor in index.anchors if anchor not in index.ids})
    assert not missing, f"{document.name} links to missing anchors: {missing}"


def asset_base(document: Path) -> Path:
    """Templates declare paths for their destination, not for the templates directory."""
    if document.is_relative_to(REPO_ROOT / "templates"):
        return REPO_ROOT / "docs" / "guides"
    return document.parent


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_referenced_assets_resolve(document: Path) -> None:
    index = index_of(document)
    base = asset_base(document)
    missing = [
        asset
        for asset in index.assets
        if not asset.startswith(("http://", "https://", "//"))
        and not (base / asset).resolve().is_file()
    ]
    assert not missing, f"{document.name} references missing assets: {missing}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_no_external_runtime_dependencies(document: Path) -> None:
    index = index_of(document)
    external = [asset for asset in index.assets if asset.startswith(("http://", "https://", "//"))]
    assert not external, f"{document.name} loads runtime assets from the internet: {external}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_images_have_alternative_text(document: Path) -> None:
    index = index_of(document)
    missing = [image.get("src", "?") for image in index.images if "alt" not in image]
    assert not missing, f"{document.name} has images without alt text: {missing}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_no_emoji(document: Path) -> None:
    found = {
        character
        for character in document.read_text(encoding="utf-8")
        if any(low <= ord(character) <= high for low, high in EMOJI_RANGES)
    }
    assert not found, f"{document.name} contains emoji: {sorted(found)}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_no_third_person_narration(document: Path) -> None:
    text = document.read_text(encoding="utf-8").lower()
    found = [phrase for phrase in THIRD_PERSON_NARRATION if phrase in text]
    assert not found, f"{document.name} uses classroom narration: {found}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_no_editorial_residue(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    found = re.findall(r"\b(TODO|FIXME|TBD|PLACEHOLDER|XXX)\b", text)
    assert not found, f"{document.name} contains editorial residue: {sorted(set(found))}"
