#!/usr/bin/env python3

# %%
# Imports
import argparse
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import typing
from decimal import Decimal
from pathlib import Path

import jsonpickle
import requests
from borb.pdf import PDF, Document, Image, MultiColumnLayout, Page, PageLayout

from proquest_dl.Issue import Issue
from proquest_dl.ProQuestWebScraper import (ProQuestWebScraper,
                                            known_publication_ids)
from proquest_dl.misc import (economist_rename_toc, extract_single_pages_from_pdfs,
                              generate_toc, get_page_size, insert_bookmarks,
                              insert_white_pages_in_issue, merge_pdf,
                              remove_cropbox_from_articles,
                              remove_last_page_from_articles)

# %%
# # Config

# only needed for "MIT Technology Review"
mit_cover_url = (
    "https://wp.technologyreview.com/"
    "wp-content/uploads/2023/08/SO23-front_cover2.png"
)

default_firefox_profile = Path.home() / ".proquest-dl" / "firefox-profile"

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


def add_cover(cover_url, downloaddir, pagesdir, page_size):
    """Download the cover and put it as page 1 (ProQuest has no covers)."""
    response = requests.get(cover_url, timeout=60)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not content_type.startswith("image/"):
        logging.warning(f"No cover at {cover_url} (HTTP {response.status_code}, "
                        f"{content_type}): page 1 stays as it is")
        return
    ext = cover_url.split(".")[-1]
    coverfp = downloaddir / ("cover." + ext)
    logging.info(f"coverfp={coverfp}")
    coverfp.write_bytes(response.content)
    # Remove the white page we have instead of the cover
    page1fn = pagesdir / "pages_1.pdf"
    if page1fn.is_file():
        page1fn.unlink()
    # Resize cover page image
    coverfp2 = coverfp.parent / \
        (coverfp.stem + "_resized" + coverfp.suffix)
    magick_page_size = str(page_size[0]) + "x" + str(page_size[1]) + "!"
    cmd = ["magick", str(coverfp), "-resize", magick_page_size, str(coverfp2)]
    logging.info(f"Command to resize cover page: '{cmd}'")
    subprocess.run(cmd, check=True)
    # Convert cover page form image to PDF
    doc: Document = Document()
    page: Page = Page(width=page_size[0], height=page_size[1])
    layout: PageLayout = FullPageLayout(page)
    # Reduce image size a little bit to avoid borb
    # throwing out an exception due to not having
    # enough space on the page
    width = int(float(page_size[0])*0.95)
    height = int(float(page_size[1])*0.95)
    layout.add(Image(coverfp, width=width, height=height))
    doc.add_page(page)
    with open(page1fn, "wb") as pdf_file_handle:
        PDF.dumps(pdf_file_handle, doc)


def compress_pdf(infp, outfp):
    """Same as 'ps2pdf -dPDFSETTINGS=/ebook', calling Ghostscript directly."""
    gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
    if gs is None:
        raise Exception("Ghostscript not found in PATH")
    cmd = [gs, "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
           "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook",
           f"-sOutputFile={outfp}", str(infp)]
    subprocess.run(cmd, check=True)


def main(argv=None, publication_id=None, prog="proquest-dl"):
    """Command line entry point.

    publication_id: set by wrappers for a single publication
    (see economist_dl.py); the command then has no publication_id argument.
    """

    logging.basicConfig(
        force=True,
        level=logging.INFO,
        format=("[%(asctime)s] [%(filename)s:%(funcName)s] "
                "[%(levelname)s] %(message)s")
    )
    # No usage statistics from Selenium Manager
    os.environ.setdefault("SE_AVOID_STATS", "true")

    # %%
    logging.info("Start")
    logging.info("Python version: " + sys.executable)

    # %%
    # argparse
    parser = argparse.ArgumentParser(
        prog=prog,
        description='Download journal issues from ProQuest',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    if publication_id is None:
        parser.add_argument('publication_id',
                            action='store',
                            help="The ID of the publication on ProQuest. "
                            "For example, 41716 for The Economist or "
                            "35850 for MIT Technology Review")
    parser.add_argument('--tmpdir',
                        action='store',
                        help="Path to temporary directory",
                        default="./proquest-dl-temp")
    parser.add_argument('--outdir',
                        action='store',
                        help="Path to output directory",
                        default=".")
    parser.add_argument('--headless',
                        action='store_true',
                        help=("Run browser in headless mode "
                              "(without showing UI)"),
                        default=False)
    parser.add_argument('--continue-download',
                        action='store_true',
                        help="Continue an already started download",
                        default=False)
    parser.add_argument('--issue-date',
                        type=datetime.date.fromisoformat,
                        metavar="YYYY-MM-DD",
                        help=("Date of the issue to download, as ProQuest "
                              "shows it (for The Economist, the Saturday "
                              "on the cover). Default: the latest issue."),
                        default=None)
    parser.add_argument('--publication-date',
                        nargs=3,
                        type=int,
                        metavar=("YEAR", "MONTH", "ISSUE"),
                        help=("Date of the publication to download, with "
                              "ISSUE the position of the issue in the month "
                              "(0 = the most recent). If everything is None, "
                              "it will download the latest issue."),
                        action='store', default=[None, None, None])
    parser.add_argument('--pause',
                        nargs=2,
                        type=float,
                        metavar=("MIN", "MAX"),
                        help=("Random pause, in seconds, after each page "
                              "load. It doubles every time ProQuest shows "
                              "a captcha."),
                        default=[2, 5])
    parser.add_argument('--exclude-section',
                        metavar=("SECTION"),
                        help=("Section to exclude (e.g. 'Special report'). "
                              "Repeat it to exclude more than one section. "
                              "It costs one more page load per article."),
                        action='append',
                        default=None)
    parser.add_argument('--browser-app',
                        action='store',
                        help="Browser to use",
                        default="firefox")
    parser.add_argument('--geckodriver-path',
                        action='store',
                        help=("Path to geckodriver "
                              "(default: found by Selenium)"),
                        default=None)
    parser.add_argument('--firefox-profile-path',
                        action='store',
                        help=("Firefox profile, used in place so that the "
                              "ProQuest login is kept between runs. "
                              "No other Firefox may be using it."),
                        default=str(default_firefox_profile))
    args = parser.parse_args(argv)
    if publication_id is not None:
        args.publication_id = publication_id

    args.exclude_section = args.exclude_section or []

    # Make variables
    proquest_url = f"https://www.proquest.com/publication/{
        args.publication_id}"

    # Date variables
    journal_year = args.publication_date[0]
    journal_month = args.publication_date[1]
    # Most recent issue has index 0
    # Increasing the issue leads to less recent issues
    journal_issue = args.publication_date[2]
    if args.issue_date is not None:
        if journal_year is not None:
            parser.error("give either --issue-date or --publication-date")
        journal_year = args.issue_date.year
        journal_month = args.issue_date.month
    journal_latest = (journal_year is None) and \
        (journal_month is None) and \
        (journal_issue is None)
    assert isinstance(journal_latest, bool)
    if not journal_latest:
        assert journal_year >= 1900
        assert journal_year <= 2999
        assert journal_month >= 1
        assert journal_month <= 12
        if args.issue_date is None:
            assert journal_issue >= 0
            assert journal_issue <= 4

    # Directories variables
    args.tmpdir = Path(args.tmpdir)
    if args.tmpdir.is_dir() and not args.continue_download:
        raise Exception("Error: temporary directory already exists. "
                        "Did you forget --continue-download?")
    downloaddir = args.tmpdir / "download"
    artdir1 = downloaddir / "1_articles"
    artdir2 = downloaddir / "2_articles"
    artdir3 = downloaddir / "3_articles"
    pagesdir = downloaddir / "4_pages"
    tocdir = downloaddir / "toc"

    if downloaddir.is_dir() and (not args.continue_download):
        logging.info(f"Removing previous download directory: {downloaddir}")
        shutil.rmtree(downloaddir)
    downloaddir.mkdir(parents=True, exist_ok=args.continue_download)
    artdir1.mkdir(parents=True, exist_ok=args.continue_download)
    # The later steps are fast and rerun from the downloaded files
    for d in (artdir2, artdir3, pagesdir):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    tocdir.mkdir(parents=True, exist_ok=args.continue_download)

    # %%
    # Open web browser
    scraper = ProQuestWebScraper(
        publication_id=args.publication_id,
        artdir1=artdir1,
        tocdir=tocdir,
        sleep_time=args.pause,
        exclude_sections=args.exclude_section,
    )
    scraper.get_browser(browser_app=args.browser_app,
                        headless_browser=args.headless,
                        firefox_profile_path=args.firefox_profile_path,
                        geckodriver_path=args.geckodriver_path
                        )
    try:
        # %%
        # Connect to ProQuest website
        logging.info(f"Connecting to ProQuest URL={proquest_url}")
        scraper.browser.get(proquest_url)
        scraper.wait_for_issue_page(proquest_url)

        # %%
        # Save issue to file
        fp = args.tmpdir/"issue.json"
        if fp.is_file():
            # Read issue from file
            logging.info("Reading issue from file")
            with open(fp, "r", encoding="utf-8") as fh:
                issue = jsonpickle.decode(fh.read())
        else:
            # Build issue from scratch
            logging.info("Building issue from scratch")
            issue = Issue()

            # Get publication name
            issue.publication_id = args.publication_id
            issue.publication_name = scraper.get_publication_name()
            logging.info(f"publication_name='{issue.publication_name}'")

            # Select issue to download
            if not journal_latest:
                scraper.select_issue(
                    journal_year,
                    journal_month,
                    journal_issue,
                    issue_date=args.issue_date,
                )

            # Get number of articles to download
            count = scraper.get_art_count()
            logging.info(f"Number of articles to download: {count}")

            # Get date in which the issue was released
            issue.date = scraper.get_issue_date()
            logging.info(f"Issue date of publication: {issue.date}")
            if args.issue_date is not None:
                assert issue.date == args.issue_date, issue.date

            # Download list of articles
            scraper.retrieve_articles_list(issue)

            # Save issue to JSON file
            # in case we will resume an interrupted download
            with open(fp, "w", encoding="utf-8") as fh:
                encoded = jsonpickle.encode(issue)
                decoded = json.loads(encoded)
                encoded = json.dumps(
                    decoded,
                    indent=4,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                fh.write(encoded)

        # %%
        # Build output PDF file path
        args.outdir = Path(args.outdir)
        pdf_fp = args.outdir/issue.build_final_fp()
        logging.info(f"Output file: {pdf_fp}")
        assert not pdf_fp.is_file()

        # %%
        # Download single articles
        scraper.download_articles(issue)
    finally:
        # %%
        # Close web browser (quit also stops geckodriver)
        scraper.browser.quit()

    # %%
    # Remove last page with copyright notice
    logging.info("Removing last pages with copyright notice")
    remove_last_page_from_articles(artdir1, artdir2)

    # %%
    # Remove CropBox from PDF files
    logging.info("Removing CropBox")
    remove_cropbox_from_articles(artdir2, artdir3)

    # %%
    # Get size of a PDF page
    logging.info("Getting size of a PDF page")
    fn = next(artdir3.iterdir())
    logging.info(f"Using fn='{fn}'")
    issue.page_size = get_page_size(fn)

    # %%
    # If we have TOC, get page number and rename
    # (only for The Economist, which doesn't provide page numbers for its TOC)
    economist_rename_toc(issue, tocdir, artdir3)

    # %%
    # Generate TOC if we don't have one
    generate_toc(issue, tocdir, artdir3)

    # %%
    # Extract single pages from original PDF files
    logging.info("Extracting single pages from PDFs")
    extract_single_pages_from_pdfs(artdir3, pagesdir)

    # %%
    # Generate white page
    logging.info("Generating white page")
    whitepdffp = downloaddir / "blank.pdf"
    doc: Document = Document()
    page: Page = Page(width=issue.page_size[0], height=issue.page_size[1])
    doc.add_page(page)
    with open(whitepdffp, "wb") as pdf_file_handle:
        PDF.dumps(pdf_file_handle, doc)

    # %%
    # Put white pages where needed
    logging.info("Inserting white pages")
    insert_white_pages_in_issue(artdir3, pagesdir, whitepdffp)

    # %%
    # Download cover
    logging.info("Downloading cover")
    cover_url = None
    if args.publication_id == known_publication_ids["The Economist"]:
        # Build URL for The Economist's cover
        ts = issue.date.strftime("%Y%m%d")
        cover_url = (
            f"https://www.economist.com/img/b/1280/1684/90"
            f"/media-assets/image/{ts}_DE_EU.jpg"
        )
    elif args.publication_id == known_publication_ids["MIT Technology Review"]:
        cover_url = mit_cover_url
    if cover_url:
        add_cover(cover_url, downloaddir, pagesdir, issue.page_size)

    # %%
    # Merge pages into final PDF file
    logging.info("Merging")
    outfp = downloaddir / "output.pdf"
    merge_pdf(pagesdir, outfp)

    # %%
    # Insert bookmarks
    logging.info("Inserting bookmarks")
    infp = outfp
    outfp = downloaddir / "output2.pdf"
    insert_bookmarks(downloaddir, issue, infp, outfp)

    # %%
    # Compress final PDF file
    logging.info("Compressing PDF")
    infp = outfp
    outfp = downloaddir / "output_bookmarked_compressed.pdf"
    compress_pdf(infp, outfp)

    # %%
    # Move PDF to "final" subfolder
    logging.info("Moving PDF into final destination")
    args.outdir.mkdir(parents=True, exist_ok=True)
    shutil.move(outfp, pdf_fp)
    shutil.rmtree(args.tmpdir)

    # %%
    logging.info(f"Ended: {pdf_fp}")


if __name__ == "__main__":
    main()
