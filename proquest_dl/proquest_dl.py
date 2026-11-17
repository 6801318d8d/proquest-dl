#!/usr/bin/env python3

# %%
# Imports
import argparse
import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from proquest_dl.Issue import Issue
from proquest_dl.ProQuestWebScraper import (CaptchaRequired, ProQuestWebScraper,
                                            known_publication_ids)
from proquest_dl.misc import (economist_rename_toc, extract_single_pages_from_pdfs,
                              generate_toc, get_page_size, insert_bookmarks,
                              insert_white_pages_in_issue, merge_pdf,
                              remove_cropbox_from_articles,
                              remove_last_page_from_articles, write_blank_page)

# %%
# # Config

default_firefox_profile = Path.home() / ".proquest-dl" / "firefox-profile"
# Name that proquest-dl gives to the PDF of an issue (see Issue.build_final_fp)
ISSUE_FILE = re.compile(r".+-(\d{4}-\d{2}-\d{2})\.pdf")


def prune(outdir, keep_days, today):
    """Delete the issues (NAME-YYYY-MM-DD.pdf) in outdir dated more than
    keep_days days before today."""
    for fp in Path(outdir).glob("*.pdf"):
        found = ISSUE_FILE.fullmatch(fp.name)
        if found and (today - datetime.date.fromisoformat(found.group(1))).days > keep_days:
            fp.unlink()
            logging.info(f"Deleted {fp}")


def load_dotenv():
    """Put the KEY=VALUE lines of .env (in the current folder, then in the
    project folder) into the environment; variables already set win."""
    for fp in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if fp.is_file():
            for line in fp.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and not key.strip().startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip().strip('"'))


def get_credentials():
    """(username, password) for the institution's sign-in page, from
    PROQUEST_USERNAME and PROQUEST_PASSWORD, or from the JSON
    {"login": ..., "password": ...} that PROQUEST_CREDENTIALS_URL serves
    (e.g. a local password manager bridge). None: log in by hand."""
    if os.environ.get("PROQUEST_USERNAME") and os.environ.get("PROQUEST_PASSWORD"):
        return os.environ["PROQUEST_USERNAME"], os.environ["PROQUEST_PASSWORD"]
    url = os.environ.get("PROQUEST_CREDENTIALS_URL")
    if not url:
        return None
    # A command that starts the server of PROQUEST_CREDENTIALS_URL, run
    # only now that the sign-in page asks for the password
    command = os.environ.get("PROQUEST_CREDENTIALS_COMMAND")
    server = subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) if command else None
    deadline = time.monotonic() + (30 if server else 0)
    try:
        while True:
            try:
                data = requests.get(url, timeout=10).json()
                return data["login"], data["password"]
            except requests.ConnectionError:
                # the server is still starting
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
    except Exception as e:
        logging.warning(f"No credentials from PROQUEST_CREDENTIALS_URL "
                        f"({type(e).__name__}): log in by hand")
        return None
    finally:
        if server and server.poll() is None:
            server.terminate()


def add_cover(cover_url, downloaddir, pagesdir, page_size):
    """Download the cover and put it as page 1 (ProQuest has no covers)."""
    response = requests.get(cover_url, timeout=60)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not content_type.startswith("image/"):
        logging.warning(f"No cover at {cover_url} (HTTP {response.status_code}, "
                        f"{content_type}): page 1 stays as it is")
        return
    coverfp = downloaddir / ("cover." + cover_url.split(".")[-1])
    coverfp.write_bytes(response.content)
    # Stretch the image to the page and write it as a PDF page:
    # 2 pixels per point at 144 dpi give a page of exactly page_size points
    page1fn = pagesdir / "pages_1.pdf"
    size = f"{round(2 * page_size[0])}x{round(2 * page_size[1])}!"
    cmd = ["magick", str(coverfp), "-resize", size,
           "-units", "PixelsPerInch", "-density", "144", str(page1fn)]
    logging.info(f"Command to make the cover page: '{cmd}'")
    subprocess.run(cmd, check=True)


def compress_pdf(infp, outfp):
    """Same as 'ps2pdf -dPDFSETTINGS=/ebook', calling Ghostscript directly."""
    gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
    if gs is None:
        raise Exception("Ghostscript not found in PATH")
    cmd = [gs, "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
           "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook",
           f"-sOutputFile={outfp}", str(infp)]
    subprocess.run(cmd, check=True)


def setup():
    """Logging, Selenium settings and .env, before parsing the command line."""
    logging.basicConfig(
        force=True,
        level=logging.INFO,
        format=("[%(asctime)s] [%(filename)s:%(funcName)s] "
                "[%(levelname)s] %(message)s")
    )
    # No usage statistics from Selenium Manager
    os.environ.setdefault("SE_AVOID_STATS", "true")
    load_dotenv()

    # %%
    logging.info("Start")
    logging.info("Python version: " + sys.executable)


def build_parser(publication_id=None, prog="proquest-dl"):
    """The command line options. publication_id: set by wrappers for a
    single publication (see economist_dl.py), which then have no
    publication_id argument."""
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
                              "shows it: for The Economist the Saturday on "
                              "the cover, for monthly magazines the first "
                              "day of the month. Default: the latest issue."),
                        default=None)
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
                        help="Path to geckodriver; none: Selenium finds it",
                        default=None)
    parser.add_argument('--firefox-profile-path',
                        action='store',
                        help=("Firefox profile, used in place so that the "
                              "ProQuest login is kept between runs. "
                              "No other Firefox may be using it."),
                        default=str(default_firefox_profile))
    parser.add_argument('--new-issue', action='store_true',
                        help=("For a scheduled run: download the latest issue "
                              "unless it is already in --outdir, resume an "
                              "interrupted download in --tmpdir by itself, and "
                              "stop without an error when ProQuest asks for a "
                              "captcha (the next run resumes)"))
    parser.add_argument('--keep-days', type=int, metavar="N", default=None,
                        help=("First delete the issues in --outdir dated over "
                              "N days ago"))
    return parser


def main(argv=None, today=None):
    """Command line entry point of proquest-dl."""
    setup()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.keep_days is not None:
        prune(args.outdir, args.keep_days, today or datetime.date.today())
    if args.new_issue:
        if args.issue_date:
            parser.error("--new-issue downloads the latest issue")
        # Resume an interrupted download; drop a run that stopped before
        # the list of articles was saved
        tmpdir = Path(args.tmpdir)
        if (tmpdir / "issue.json").is_file():
            args.continue_download = True
        elif tmpdir.is_dir():
            shutil.rmtree(tmpdir)
    try:
        run(args)
    except CaptchaRequired:
        if not args.new_issue:
            raise
        logging.warning("ProQuest asks for a captcha: the next run resumes the "
                        "download (the PDF files already downloaded are kept)")


def run(args):
    """Download the issue that the parsed command line asks for."""
    args.exclude_section = args.exclude_section or []

    # Make variables
    proquest_url = f"https://www.proquest.com/publication/{
        args.publication_id}"

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

    # Issue saved by an interrupted run
    fp = args.tmpdir/"issue.json"
    issue = Issue.load(fp) if fp.is_file() else None
    if issue and args.issue_date and issue.date != args.issue_date:
        raise Exception(f"{args.tmpdir} holds the download of the issue of "
                        f"{issue.date}, not {args.issue_date}: finish it, "
                        "delete the folder or use another --tmpdir")

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
        scraper.wait_for_issue_page(
            proquest_url,
            institution_url=os.environ.get("PROQUEST_INSTITUTION_URL"),
            get_credentials=get_credentials)

        # %%
        if issue:
            logging.info(f"Resuming the download of the issue of {issue.date}")
        else:
            # Build issue from scratch
            logging.info("Building issue from scratch")
            issue = Issue()

            # Get publication name
            issue.publication_id = args.publication_id
            issue.publication_name = scraper.get_publication_name()
            logging.info(f"publication_name='{issue.publication_name}'")

            # Select issue to download
            if args.issue_date:
                scraper.select_issue(args.issue_date)

            # Get number of articles to download
            count = scraper.get_art_count()
            logging.info(f"Number of articles to download: {count}")

            # Get date in which the issue was released
            issue.date = scraper.get_issue_date()
            logging.info(f"Issue date of publication: {issue.date}")
            if args.issue_date is not None:
                assert issue.date == args.issue_date, issue.date
            done = Path(args.outdir) / issue.build_final_fp()
            if args.new_issue and done.is_file():
                logging.info(f"Already downloaded: {done} (the latest issue on ProQuest)")
                shutil.rmtree(args.tmpdir)
                return

            # Download list of articles
            scraper.retrieve_articles_list(issue)

            # Save issue, in case we will resume an interrupted download
            issue.save(fp)

        # %%
        # Build output PDF file path
        args.outdir = Path(args.outdir)
        pdf_fp = args.outdir/issue.build_final_fp()
        logging.info(f"Output file: {pdf_fp}")
        if pdf_fp.is_file():
            raise Exception(f"{pdf_fp} already exists")

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
    # Put white pages where needed
    logging.info("Inserting white pages")
    whitepdffp = downloaddir / "blank.pdf"
    write_blank_page(whitepdffp, issue.page_size)
    insert_white_pages_in_issue(artdir3, pagesdir, whitepdffp)

    # %%
    # Cover (only The Economist publishes its covers at a predictable address)
    if args.publication_id == known_publication_ids["The Economist"]:
        logging.info("Downloading cover")
        cover_url = ("https://www.economist.com/content-assets/images/"
                     f"{issue.date:%Y%m%d}_DE_EU.jpg")
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
    insert_bookmarks(issue, infp, outfp)

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
