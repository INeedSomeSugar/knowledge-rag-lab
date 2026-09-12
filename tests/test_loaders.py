import sys
from types import SimpleNamespace

from app.loaders import load_bytes


def test_pdf_loader_preserves_page_boundaries(monkeypatch) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class FakeReader:
        def __init__(self, _content) -> None:
            self.pages = [FakePage("第一页"), FakePage("第二页")]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))

    assert load_bytes("manual.pdf", b"fake-pdf") == "第一页\n\f\n第二页"
