"""Offline checks of the PDF post-processing (needs qpdf and magick in PATH)."""

import datetime
import subprocess
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from proquest_dl import proquest_dl
from proquest_dl.Article import Article
from proquest_dl.Issue import Issue
from proquest_dl.ProQuestWebScraper import (ProQuestWebScraper, article_from_item,
                                            back_on_proquest, parse_pages,
                                            pdf_link_from_html)
from proquest_dl.misc import (economist_rename_toc, extract_single_pages_from_pdfs,
                              get_number_of_pages, get_page_size,
                              get_text_from_top_right_corner, insert_bookmarks,
                              insert_white_pages_in_issue, merge_pdf,
                              remove_cropbox_from_articles,
                              remove_last_page_from_articles, write_blank_page)


def write_raw_pdf(path, objs):
    """Write the objects as a PDF; qpdf rebuilds the cross-reference table."""
    raw = path.with_suffix(".raw")
    raw.write_bytes(b"%PDF-1.4\n" + b"\n".join(objs)
                    + f"\ntrailer << /Root 1 0 R /Size {len(objs) + 1} >>\n%%EOF\n".encode())
    subprocess.run(["qpdf", "--warning-exit-0", str(raw), str(path)], check=True)
    raw.unlink()


def make_pdf(path, npages):
    """A PDF of npages pages, each with a CropBox smaller than its MediaBox.
    Page i (from 0) is 600 + i points wide, to tell the pages apart."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(npages))
    objs = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {npages} >> endobj".encode()]
    for i in range(npages):
        objs.append(f"{3 + i} 0 obj << /Type /Page /Parent 2 0 R "
                    f"/MediaBox [0 0 {600 + i} 800] /CropBox [10 10 500 700] >> endobj".encode())
    write_raw_pdf(path, objs)


def test_pdf_link_from_html():
    page = ('<a class="tool-option-link pdf-download " '
            'href="https://media.example.com/media/x?a=1&amp;b=2" '
            'id="downloadPDFLink_MSTAR_123">Download PDF</a>')
    assert pdf_link_from_html("<p>" + page + "</p>") == \
        "https://media.example.com/media/x?a=1&b=2"
    assert pdf_link_from_html('<a id="downloadPDFLink_1" href="/y">') == "/y"
    assert pdf_link_from_html("<form id='verifyCaptcha'></form>") is None


def test_back_on_proquest():
    assert back_on_proquest("https://www.proquest.com/?accountid=12345")
    # the query string may name the login method
    assert back_on_proquest("https://www.proquest.com/?accountid=1&type=shibboleth")
    assert not back_on_proquest("https://www.proquest.com/shibboleth?accountid=1")
    assert not back_on_proquest(
        "https://shibboleth-sp.prod.proquest.com/Shibboleth.sso/SAML2/POST")
    assert not back_on_proquest("https://idp.example.edu/idp/profile/SAML2/Redirect/SSO")


def test_table_of_contents_with_pages():
    ref = "The Economist; London Vol. 461, Iss. 9525,  (Nov 14, 2026)"
    item = {"title": "Table of Contents", "pdfurl": "https://p", "detailsurl": None}
    issue = Issue()
    with_pages = article_from_item({**item, "loc": ref + ": 5-6."}, issue)
    without_pages = article_from_item({**item, "loc": ref + "."}, issue)
    assert issue.toc
    assert (with_pages.pages, with_pages.is_toc) == ([5, 6], False)
    assert (without_pages.pages, without_pages.is_toc) == (None, True)


def test_parse_pages():
    ref = "The Economist; London Vol. 457, Iss. 9473,  (Nov 14, 2026): "
    assert parse_pages(ref + "7.") == [7]
    assert parse_pages(ref + "17-19.") == [17, 18, 19]
    assert parse_pages(ref + "9-10, 12.") == [9, 10, 12]
    with pytest.raises(Exception):
        parse_pages("The Economist; London Vol. 457, Iss. 9473,  (Nov 14, 2026).")


def test_pipeline(tmp_path):
    dirs = {name: tmp_path / name for name in ("a1", "a2", "a3", "pages")}
    for d in dirs.values():
        d.mkdir()
    # Article files as ProQuest sends them: their pages plus a copyright page
    make_pdf(dirs["a1"] / "pages_2,3.pdf", 3)
    make_pdf(dirs["a1"] / "pages_5.pdf", 2)
    make_pdf(dirs["a1"] / "pages_9,10.pdf", 3)

    remove_last_page_from_articles(dirs["a1"], dirs["a2"])
    assert get_number_of_pages(dirs["a2"] / "pages_2,3.pdf") == 2

    remove_cropbox_from_articles(dirs["a2"], dirs["a3"])
    for fp in dirs["a3"].iterdir():
        subprocess.run(["qpdf", "--check", str(fp)], check=True,
                       capture_output=True)
        assert b"CropBox" not in fp.read_bytes()
        assert "/CropBox" not in PdfReader(fp).pages[0]

    assert get_page_size(dirs["a3"] / "pages_5.pdf") == (600, 800)

    extract_single_pages_from_pdfs(dirs["a3"], dirs["pages"])
    # the first page of the file goes to page 9, the second to page 10
    assert get_page_size(dirs["pages"] / "pages_9.pdf")[0] == 600
    assert get_page_size(dirs["pages"] / "pages_10.pdf")[0] == 601

    write_blank_page(tmp_path / "blank.pdf", (600, 800))
    insert_white_pages_in_issue(dirs["a3"], dirs["pages"], tmp_path / "blank.pdf")
    assert sorted(p.name for p in dirs["pages"].iterdir()) == sorted(
        f"pages_{i}.pdf" for i in range(1, 11))

    merge_pdf(dirs["pages"], tmp_path / "out.pdf")
    issue = SimpleNamespace(articles=[
        SimpleNamespace(title="First article", pages=[2, 3]),
        SimpleNamespace(title='The "great" article', pages=[5]),
        SimpleNamespace(title="Table of Contents", pages=None),
        SimpleNamespace(title="Third article", pages=[9, 10]),
    ])
    insert_bookmarks(issue, tmp_path / "out.pdf", tmp_path / "out2.pdf")
    reader = PdfReader(tmp_path / "out2.pdf")
    assert len(reader.pages) == 10
    assert [(o.title, reader.get_destination_page_number(o))
            for o in reader.outline] == [("First article", 1),
                                         ('The "great" article', 4),
                                         ("Third article", 8)]


def text_pdf(path, text_ops):
    """A one-page PDF (600 x 800 points) with the given text operators."""
    stream = b"".join(b"BT /F1 12 Tf %d %d Td (%s) Tj ET\n" % op for op in text_ops)
    write_raw_pdf(path, [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj",
        b"4 0 obj << /Length %d >> stream\n" % len(stream) + stream + b"endstream endobj",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj"])


def test_text_from_top_right_corner(tmp_path):
    text_pdf(tmp_path / "toc.pdf", [(570, 770, b"7"), (100, 400, b"Body")])
    assert get_text_from_top_right_corner(tmp_path / "toc.pdf", 40, 50) == "7"


def test_economist_toc_without_page_number(tmp_path):
    # TOC page plus copyright page, with no number in the top right corner
    make_pdf(tmp_path / "pages_toc.pdf", 2)
    issue = SimpleNamespace(publication_id="41716", toc=True, page_size=(600, 800))
    economist_rename_toc(issue, tmp_path, tmp_path)
    assert not list(tmp_path.glob("pages_*,*.pdf"))


def test_add_cover(tmp_path, monkeypatch):
    png = tmp_path / "c.png"
    subprocess.run(["magick", "-size", "120x160", "xc:red", str(png)], check=True)
    responses = {
        "https://x/missing.jpg": SimpleNamespace(
            status_code=403, headers={"Content-Type": "text/plain"}, content=b""),
        "https://x/cover.png": SimpleNamespace(
            status_code=200, headers={"Content-Type": "image/png"},
            content=png.read_bytes()),
    }
    monkeypatch.setattr(proquest_dl.requests, "get",
                        lambda url, timeout: responses[url])
    proquest_dl.add_cover("https://x/missing.jpg", tmp_path, tmp_path, (600, 800))
    assert not (tmp_path / "pages_1.pdf").exists()
    proquest_dl.add_cover("https://x/cover.png", tmp_path, tmp_path, (600, 800))
    assert get_number_of_pages(tmp_path / "pages_1.pdf") == 1
    assert get_page_size(tmp_path / "pages_1.pdf") == (600, 800)


def test_issue_save_load(tmp_path):
    issue = Issue(publication_id="41716", publication_name="The Economist",
                  date=datetime.date(2026, 11, 14), toc=True,
                  articles=[Article("A", [2, 3], "https://p", "https://d", False),
                            Article("Table of Contents", None, None, None, True)])
    issue.save(tmp_path / "issue.json")
    assert Issue.load(tmp_path / "issue.json") == issue


def test_economist_issue_date():
    scraper = ProQuestWebScraper("41716", None, None)
    assert scraper.parse_issue_date(" Nov 14, 2026; Vol. 457 (9473) ") \
        == datetime.date(2026, 11, 14)
