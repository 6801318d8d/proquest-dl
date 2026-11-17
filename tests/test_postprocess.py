"""Offline checks of the PDF post-processing (needs qpdf in PATH)."""

import datetime
import subprocess
from decimal import Decimal
from types import SimpleNamespace

from pypdf import PdfReader

from proquest_dl import proquest_dl
from proquest_dl.ProQuestWebScraper import ProQuestWebScraper
from proquest_dl.misc import (extract_single_pages_from_pdfs, get_number_of_pages,
                              get_page_size, get_text_from_top_right_corner,
                              insert_bookmarks, insert_white_pages_in_issue,
                              merge_pdf, remove_cropbox_from_articles,
                              remove_last_page_from_articles)


def make_pdf(path, npages):
    """A valid PDF of npages pages, each with a CropBox smaller than its MediaBox."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(npages))
    objs = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {npages} >> endobj".encode()]
    for i in range(npages):
        objs.append(f"{3 + i} 0 obj << /Type /Page /Parent 2 0 R "
                    "/MediaBox [0 0 600 800] /CropBox [10 10 500 700] >> endobj".encode())
    raw = path.with_suffix(".raw")
    raw.write_bytes(b"%PDF-1.4\n" + b"\n".join(objs)
                    + f"\ntrailer << /Root 1 0 R /Size {npages + 3} >>\n%%EOF\n".encode())
    # qpdf rebuilds the missing cross-reference table
    subprocess.run(["qpdf", "--warning-exit-0", str(raw), str(path)], check=True)
    raw.unlink()


def test_pipeline(tmp_path):
    dirs = {name: tmp_path / name for name in ("a1", "a2", "a3", "pages")}
    for d in dirs.values():
        d.mkdir()
    # Article files as ProQuest sends them: their pages plus a copyright page
    make_pdf(dirs["a1"] / "pages_2,3.pdf", 3)
    make_pdf(dirs["a1"] / "pages_5.pdf", 2)

    remove_last_page_from_articles(dirs["a1"], dirs["a2"])
    assert len(PdfReader(dirs["a2"] / "pages_2,3.pdf").pages) == 2

    remove_cropbox_from_articles(dirs["a2"], dirs["a3"])
    for fp in dirs["a3"].iterdir():
        subprocess.run(["qpdf", "--check", str(fp)], check=True,
                       capture_output=True)
        assert b"CropBox" not in fp.read_bytes()
        assert "/CropBox" not in PdfReader(fp).pages[0]

    assert [float(x) for x in get_page_size(dirs["a3"] / "pages_5.pdf")] == [600, 800]

    extract_single_pages_from_pdfs(dirs["a3"], dirs["pages"])
    make_pdf(tmp_path / "blank.pdf", 1)
    insert_white_pages_in_issue(dirs["a3"], dirs["pages"], tmp_path / "blank.pdf")
    assert sorted(p.name for p in dirs["pages"].iterdir()) == [
        f"pages_{i}.pdf" for i in range(1, 6)]

    merge_pdf(dirs["pages"], tmp_path / "out.pdf")
    issue = SimpleNamespace(articles=[
        SimpleNamespace(title="First article", pages=[2, 3]),
        SimpleNamespace(title="Second article", pages=[5]),
        SimpleNamespace(title="Table of Contents", pages=None),
    ])
    insert_bookmarks(tmp_path, issue, tmp_path / "out.pdf", tmp_path / "out2.pdf")
    reader = PdfReader(tmp_path / "out2.pdf")
    assert len(reader.pages) == 5
    assert [(o.title, reader.get_destination_page_number(o))
            for o in reader.outline] == [("First article", 1), ("Second article", 4)]


def test_text_from_top_right_corner(tmp_path):
    stream = (b"BT /F1 12 Tf 570 770 Td (7) Tj ET\n"
              b"BT /F1 12 Tf 100 400 Td (Body) Tj ET\n")
    objs = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj",
            b"4 0 obj << /Length %d >> stream\n" % len(stream) + stream
            + b"endstream endobj",
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj"]
    raw = tmp_path / "toc.raw"
    raw.write_bytes(b"%PDF-1.4\n" + b"\n".join(objs)
                    + b"\ntrailer << /Root 1 0 R /Size 6 >>\n%%EOF\n")
    subprocess.run(["qpdf", "--warning-exit-0", str(raw), str(tmp_path / "toc.pdf")],
                   check=True)
    assert get_text_from_top_right_corner(tmp_path / "toc.pdf", 40, 50) == "7"
    assert get_number_of_pages(tmp_path / "toc.pdf") == 1


def test_add_cover(tmp_path, monkeypatch):
    png = tmp_path / "c.png"
    subprocess.run(["magick", "-size", "120x160", "xc:red", str(png)], check=True)
    page_size = (Decimal(600), Decimal(800))
    responses = {
        "https://x/missing.jpg": SimpleNamespace(
            status_code=403, headers={"Content-Type": "text/plain"}, content=b""),
        "https://x/cover.png": SimpleNamespace(
            status_code=200, headers={"Content-Type": "image/png"},
            content=png.read_bytes()),
    }
    monkeypatch.setattr(proquest_dl.requests, "get",
                        lambda url, timeout: responses[url])
    proquest_dl.add_cover("https://x/missing.jpg", tmp_path, tmp_path, page_size)
    assert not (tmp_path / "pages_1.pdf").exists()
    proquest_dl.add_cover("https://x/cover.png", tmp_path, tmp_path, page_size)
    assert get_number_of_pages(tmp_path / "pages_1.pdf") == 1
    assert get_page_size(tmp_path / "pages_1.pdf") == page_size


def test_economist_issue_date():
    scraper = ProQuestWebScraper("41716", None, None)
    assert scraper.parse_issue_date(" Nov 14, 2026; Vol. 457 (9473) ") \
        == datetime.date(2026, 11, 14)
