# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.15.2
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Imports

# %%
import datetime
import itertools
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
import typing
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pandas as pd
from borb.pdf import (
    PDF,
    Document,
    Image,
    MultiColumnLayout,
    Page,
    PageLayout,
    SingleColumnLayout,
)
from borb.pdf.canvas.geometry.rectangle import Rectangle
from borb.toolkit import LocationFilter, SimpleTextExtraction
from natsort import natsorted

from tqdm.notebook import tqdm

import pdfbookmarker

from Issue import Issue
from ProQuestWebScraper import ProQuestWebScraper

# %% [markdown]
# # Config

# %%
# Publication ID of "The Economist"
known_publication_ids = {"The Economist": "41716", "MIT Technology Review": "35850"}

# publication_id = known_publication_ids["MIT Technology Review"]
publication_id = known_publication_ids["The Economist"]

proquest_url = f"https://www.proquest.com/publication/{publication_id}"

browser_app = "firefox"
geckodriver_path = Path("/usr/bin/geckodriver")
assert geckodriver_path.is_file()
headless_browser = False

datadir = Path("../data").resolve()
downloaddir = datadir / "download"
artdir1 = downloaddir / "1_articles"
artdir2 = downloaddir / "2_articles"
artdir3 = downloaddir / "3_articles"
pagesdir = downloaddir / "4_pages"
tocdir = downloaddir / "toc"

journal_latest = True
journal_year = None
journal_month = None
# Most recent issue has index 0
# Increasing the issue leads to less recent issues
journal_issue = None

# only needed for "MIT Technology Review"
journal_cover_url = (
    "https://wp.technologyreview.com/wp-content/uploads/2023/08/SO23-front_cover2.png"
)

sleep_time = (10, 20)

# %%
# If set to True, existing files will not be deleted
# before downloading the new ones
continue_download = False
delete_existing = True

# %%
assert type(journal_latest) == bool
if not journal_latest:
    assert journal_year >= 1900
    assert journal_year <= 2999
    assert journal_month >= 1
    assert journal_month <= 12
    assert journal_issue >= 0
    assert journal_issue <= 4
else:
    assert journal_year is None
    assert journal_month is None
    assert journal_issue is None

# %%
assert datadir.is_dir()
if downloaddir.is_dir() and (not continue_download) and delete_existing:
    logging.info(f"Removing previous download directory: {downloaddir}")
    shutil.rmtree(downloaddir)
downloaddir.mkdir(parents=True, exist_ok=continue_download)
artdir1.mkdir(parents=True, exist_ok=continue_download)
artdir2.mkdir(parents=True, exist_ok=continue_download)
artdir3.mkdir(parents=True, exist_ok=continue_download)
pagesdir.mkdir(parents=True, exist_ok=continue_download)
tocdir.mkdir(parents=True, exist_ok=continue_download)


# %% [markdown]
# # Classes

# %%
class FullPageLayout(MultiColumnLayout):
    """
    A borb's page layout with no margins
    """

    def __init__(self, page: "Page"):
        w: typing.Optional[Decimal] = page.get_page_info().get_width()
        h: typing.Optional[Decimal] = page.get_page_info().get_height()
        assert w is not None
        assert h is not None
        super().__init__(
            page=page,
            column_widths=[w],
            footer_paint_method=None,
            header_paint_method=None,
            inter_column_margins=[],
            margin_bottom=Decimal(0),
            margin_left=Decimal(0),
            margin_right=Decimal(0),
            margin_top=Decimal(0),
        )


# %% [markdown]
# # Functions

# %% [markdown]
# ## PDF

# %%
def insert_white_pages_in_issue(indir, outdir, whitepdffp):
    pages_we_have = [str(x) for x in indir.iterdir()]
    pages_we_have = [get_pages_from_file(x).split(",") for x in pages_we_have]
    pages_we_have = list(itertools.chain(*pages_we_have))
    pages_we_have = [int(x) for x in pages_we_have]
    pages_we_have.sort()
    logging.info(f"pages_we_have={pages_we_have}")
    for i in range(1, max(pages_we_have) + 1):
        if i not in pages_we_have:
            filename = "pages_" + str(i) + ".pdf"
            shutil.copy(whitepdffp, outdir / filename)


# %%
def extract_single_pages_from_pdfs(indir, outdir):
    pagesfns = natsorted(list(indir.iterdir()))
    for pagesfn in pagesfns:
        logging.info(f"pagesfn={pagesfn.stem}")
        this_pages = get_pages_from_file(pagesfn)
        logging.info(f"this_pages={this_pages}")
        # Split pages across commas ","
        for this_page_i, this_page in enumerate(this_pages.split(",")):
            # Extract a single page
            outfn = outdir / ("pages_" + this_page + ".pdf")
            cmd = [
                "qpdf",
                "--empty",
                "--pages",
                str(pagesfn),
                str(this_page_i + 1),
                "--",
                outfn,
            ]
            ret = subprocess.run(cmd)
            assert ret.returncode == 0


# %%
def generate_toc(issue, tocdir, outdir):
    """
    Generate TOC if we don't have one
    """
    if issue.toc:
        return
    logging.info("We don't have a Table Of Contents. Generating our own ...")
    tocmdfile = tocdir / "toc.md"
    tocpdffile = tocmdfile.parent / (tocmdfile.stem + ".pdf")
    fh = open(tocmdfile, "w", encoding="utf8")
    fh.write("# Table of Contents\n\n")
    all_titles = [article.title for article in issue.articles]
    all_pages = [article.pages for article in issue.articles]
    df = pd.DataFrame({"Title": all_titles, "Page": all_pages})
    fh.write(df.to_markdown(index=False))
    fh.close()
    cmd = [
        "pandoc",
        "-f",
        "markdown",
        "-t",
        "pdf",
        "-V",
        "geometry:papersize={"
        + issue.page_size[0]
        + "pt, "
        + issue.page_size[1]
        + "pt}",
        str(tocmdfile),
        "-o",
        str(tocpdffile),
    ]
    logging.info(f"Command to convert self-generated TOC from Markdown to PDF: '{cmd}'")
    ret = subprocess.run(cmd)
    assert ret.returncode == 0
    shutil.move(tocpdffile, outdir / "pages_2-3.pdf")


# %%
def economist_rename_toc(issue, tocdir, outdir):
    is_economist = issue.publication_id == known_publication_ids["The Economist"]
    if is_economist and issue.toc:
        logging.info("Getting page number from PDF")
        tocpdf = tocdir / ("pages_toc.pdf")
        page_start = get_text_from_top_right_corner(tocpdf, 40, 50, issue.page_size)
        page_start = int(page_start)
        assert 0 < page_start <= 10
        logging.info(f"TOC starting page: {page_start}")
        npages = get_number_of_pages(tocpdf)
        assert 0 < npages <= 5
        logging.info(f"TOC number of pages: {npages}")
        page_range = ",".join([str(x) for x in range(page_start, page_start + npages)])
        logging.info(f"TOC page range: {page_range}")
        pdffn = outdir / (f"pages_{page_range}.pdf")
        shutil.copy(tocpdf, pdffn)


# %%
def get_page_size(fp):
    doc: typing.Optional[Document] = None
    with open(fp, "rb") as in_file_handle:
        doc = PDF.loads(in_file_handle)
    # check whether we have read a Document
    assert doc is not None
    # get page size in "default user space units"
    page_size = doc.get_page(0).get_page_info().get_size()
    return page_size


# %%
def get_number_of_pages(fp):
    with open(fp, "rb") as in_file_handle:
        doc = PDF.loads(in_file_handle)
    return int(doc.get_document_info().get_number_of_pages())


# %%
def get_text_from_top_right_corner(fp, xoffset, yoffset, page_size=None):
    if not page_size:
        page_size = get_page_size(fp)

    page_size = [float(x) for x in page_size]

    x = page_size[0] - xoffset
    y = page_size[1] - yoffset
    width = page_size[0] - x
    height = page_size[1] - y

    # define the Rectangle of interest
    r: Rectangle = Rectangle(x, y, width, height)

    # define SimpleTextExtraction
    l0: SimpleTextExtraction = SimpleTextExtraction()

    # apply a LocationFilter on top of SimpleTextExtraction
    l1: LocationFilter = LocationFilter(r)
    l1.add_listener(l0)

    # read the Document
    doc: typing.Optional[Document] = None
    with open(fp, "rb") as in_file_handle:
        doc = PDF.loads(in_file_handle, [l1])

    # check whether we have read a Document
    assert doc is not None

    # print the text inside the Rectangle of interest
    return l0.get_text()[0]


# %%
def remove_last_page_borb(infp, outfp):
    doc: typing.Optional[Document] = None
    with open(infp, "rb") as pdf_file_handle:
        doc = PDF.loads(pdf_file_handle)
    assert doc is not None
    npages = int(doc.get_document_info().get_number_of_pages())
    doc.pop_page(npages - 1)
    with open(outfp, "wb") as pdf_file_handle:
        PDF.dumps(pdf_file_handle, doc)


# %%
def remove_last_page_qpdf(infp, outfp):
    cmd = ["qpdf", "--empty", "--pages", str(infp), "1-r2", "--", str(outfp)]
    res = subprocess.run(cmd)
    assert res.returncode == 0


# %%
def remove_last_page(infp, outfp):
    remove_last_page_qpdf(infp, outfp)


# %%
def remove_last_page_from_articles(indir, outdir):
    for infp in indir.iterdir():
        outfp = outdir / (infp.name)
        remove_last_page(infp, outfp)


# %%
def remove_crop_box_borb(infp, outfp):
    npages = get_number_of_pages(infp)
    doc: typing.Optional[Document] = None
    with open(infp, "rb") as pdf_file_handle:
        doc = PDF.loads(pdf_file_handle)
    assert doc is not None
    for pagen in range(1, npages + 1):
        page_size = doc.get_page(pagen).get_page_info().get_size()
        cb = [Decimal(0), Decimal(0), page_size[0], page_size[1]]
        doc["XRef"]["Trailer"]["Root"]["Pages"]["Kids"][pagen]["CropBox"] = cb
    # store Document
    with open(outfp, "wb") as pdf_file_handle:
        PDF.dumps(pdf_file_handle, doc)


# %%
def remove_crop_box_qpdf(infp, outfp):
    # https://stackoverflow.com/questions/6451859/rendering-the-whole-media-box-of-a-pdf-page-into-a-png-file-using-ghostscript
    # qpdf --qdf input.pdf output.pdf
    cmd = ["qpdf", "--qdf", str(infp), str(outfp)]
    res = subprocess.run(cmd)
    assert res.returncode == 0
    cmd = ["sed", "-i.bak", "-e", "/CropBox/,/]/s#.# #g", str(outfp)]
    res = subprocess.run(cmd)
    assert res.returncode == 0
    bakfp = Path(str(outfp.resolve()) + ".bak")
    bakfp.unlink()


# %%
def remove_crop_box(infp, outfp):
    remove_crop_box_qpdf(infp, outfp)


# %%
def remove_cropbox_from_articles(indir, outdir):
    for infp in indir.iterdir():
        outfp = outdir / (infp.name)
        remove_crop_box(infp, outfp)


# %%
def convert_page_range(this_pages):
    # Convert a-b into a,a+1,a+2,...,b-2,b-1,b
    while res := re.search("(\d+)-(\d+)", this_pages):
        # logging.info(f"{res.start()}, {res.end()}")
        # logging.info(f"{res.group(1)}, {res.group(2)}")
        start = int(res.group(1))
        end = int(res.group(2))
        seq = [str(x) for x in range(start, end + 1)]
        this_pages = this_pages[: res.start()] + ",".join(seq) + this_pages[res.end() :]
    return this_pages


# %%
def get_pages_from_file(pagesfn):
    this_pages = re.search(".*?pages_(.*)\.pdf", str(pagesfn)).group(1)
    this_pages = convert_page_range(this_pages)
    return this_pages


# %% [markdown]
# # Main

# %%
logging.basicConfig(
    force=True,
    level=logging.INFO,
    format="[%(asctime)s] [%(filename)s:%(funcName)s] [%(levelname)s] %(message)s",
)

# %%
logging.info("Start")

# %%
logging.info("Python version: " + sys.executable)

# %% [markdown]
# Open web browser

# %%
scraper = ProQuestWebScraper(publication_id=publication_id, artdir1=artdir1, tocdir=tocdir)
scraper.get_browser(browser_app, headless_browser)

# %% [markdown]
# DELETE THIS CELL

# %%
from mylogin import mylogin

mylogin(scraper.browser, datadir)

# %% [markdown]
# Connect to ProQuest website

# %%
logging.info(f"Connecting to ProQuest URL={proquest_url}")
scraper.browser.get(proquest_url)

# %% [markdown]
# Reject cookies

# %%
scraper.reject_cookies()

# %%
# Wait for black background to go away
# Otherwise we cannot click on buttons
# Despite waiting for the buttons to be clickable
time.sleep(2)

# %% [markdown]
# Get publication name

# %%
issue = Issue()
issue.publication_id = publication_id
issue.publication_name = scraper.get_publication_name()
logging.info(f"publication_name='{issue.publication_name}'")

# %% [markdown]
# Select issue to download

# %%
if not journal_latest:
    select_issue(journal_year, journal_month, journal_issue)

# %%
# Get number of articles to download
count = scraper.get_art_count()
logging.info(f"Number of articles to download: {count}")

# %% [markdown]
# Get date in which the issue was released

# %%
issue.date = scraper.get_issue_date()
logging.info(f"Issue date of publication: {issue.date}")

# %%
issue.build_final_fp(datadir)

# %% [markdown]
# Download list of articles

# %%
scraper.retrieve_articles_list(issue)

# %% [markdown]
# Downloads single articles

# %%
scraper.download_articles(issue, sleep_time)

# %% [markdown]
# Close web browser

# %%
scraper.browser.close()

# %% [markdown]
# Remove last page with copyright notice

# %%
remove_last_page_from_articles(artdir1, artdir2)

# %% [markdown]
# Remove CropBox from PDF files

# %%
remove_cropbox_from_articles(artdir2, artdir3)

# %% [markdown]
# Get size of a PDF page

# %%
fn = next(artdir3.iterdir())
logging.info(f"Using fn='{fn}'")
issue.page_size = get_page_size(fn)

# %% [markdown]
# If we have TOC, get page number and rename (only for The Economist, which doesn't provide page numbers for its TOC)

# %%
economist_rename_toc(issue, tocdir, artdir3)

# %% [markdown]
# Generate TOC if we don't have one

# %%
generate_toc(issue, tocdir, artdir3)

# %% [markdown]
# Extract single pages from original PDF files

# %%
extract_single_pages_from_pdfs(artdir3, pagesdir)

# %% [markdown]
# Generate white page

# %%
whitepdffp = downloaddir / "blank.pdf"
doc: Document = Document()
page: Page = Page(width=issue.page_size[0], height=issue.page_size[1])
doc.add_page(page)
with open(whitepdffp, "wb") as pdf_file_handle:
    PDF.dumps(pdf_file_handle, doc)

# %% [markdown]
# Put white pages where needed

# %%
insert_white_pages_in_issue(artdir3, pagesdir, whitepdffp)

# %% [markdown]
# Download cover

# %%
if publication_id == known_publication_ids["The Economist"]:
    # Build URL for The Economist's cover
    ts = issue.date.strftime("%Y%m%d")
    journal_cover_url = (
        f"https://www.economist.com/img/b/1280/1684/90"
        f"/media-assets/image/{ts}_DE_EU.jpg"
    )
if journal_cover_url:
    # Download cover from web
    data = requests.get(journal_cover_url).content
    ext = journal_cover_url.split(".")[-1]
    logging.info(f"ext={ext}")
    coverfp = downloaddir / ("cover." + ext)
    logging.info(f"coverfp={coverfp}")
    with open(coverfp, "wb") as fh:
        fh.write(data)
    # Remove the white page we have instead of the cover
    page1fn = pagesdir / "pages_1.pdf"
    if page1fn.is_file():
        page1fn.unlink()
    # Resize cover page size
    coverfp2 = coverfp.parent / (coverfp.stem + "_resized" + coverfp.suffix)
    magick_page_size = str(issue.page_size[0]) + "x" + str(issue.page_size[1]) + "!"
    cmd = [
        "magick",
        "convert",
        str(coverfp),
        "-resize",
        magick_page_size,
        str(coverfp2),
    ]
    logging.info(f"Command to resize cover page: '{cmd}'")
    ret = subprocess.run(cmd)
    assert ret.returncode == 0
    # Convert cover page to PDF
    doc: Document = Document()
    page: Page = Page(width=issue.page_size[0], height=issue.page_size[1])
    layout: PageLayout = FullPageLayout(page)
    layout.add(Image(coverfp, width=issue.page_size[0], height=issue.page_size[1]))
    doc.add_page(page)
    with open(page1fn, "wb") as pdf_file_handle:
        PDF.dumps(pdf_file_handle, doc)

# %% [markdown]
# Merge pages into final PDF file

# %%
cmd = ["qpdf", "--empty", "--pages"]
cmd += [str(x) for x in natsorted(pagesdir.iterdir())]
cmd += ["--", str(downloaddir / "output.pdf")]
# logging.info(f"Command to merge pages: '{cmd}'")
ret = subprocess.run(cmd)
assert ret.returncode == 0

# %% [markdown]
# Insert bookmarks

# %%
bookmarkfn = downloaddir / "bookmark.txt"
with open(bookmarkfn, "w") as fh:
    all_titles = [article.title for article in issue.articles]
    all_pages = [article.pages for article in issue.articles]
    for titlei, title in enumerate(all_titles):
        fh.write('+"' + title + '"|' + all_pages[titlei].split("-")[0] + "\n")

# %%
# def run_script(pdf_in_filename, bookmarks_filename, pdf_out_filename=None):
infn = downloaddir / "output.pdf"
outfn = downloaddir / "output_bookmarked.pdf"
pdfbookmarker.run_script(str(infn), str(bookmarkfn), str(outfn))
infn.unlink()

# %% [markdown]
# Compress final PDF file

# %%
infn = outfn
outfn = downloaddir / "output_bookmarked_compressed.pdf"
cmd = ["ps2pdf", "-dPDFSETTINGS=/ebook", str(infn), str(outfn)]
ret = subprocess.run(cmd)
assert ret.returncode == 0

# %% [markdown]
# Move PDF to "final" subfolder

# %%
shutil.move(outfn, issue.finalfp)

# %%
logging.info("Ended")
