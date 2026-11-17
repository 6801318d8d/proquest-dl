# Functions

import itertools
import logging
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from natsort import natsorted
from pypdf import PdfReader, PdfWriter

from proquest_dl.ProQuestWebScraper import known_publication_ids


def run_in_parallel(func, jobs):
    """Run func(*job) for every job, several at a time (each job runs one qpdf process)."""
    with ThreadPoolExecutor() as ex:
        list(ex.map(lambda job: func(*job), jobs))


def insert_bookmarks(issue, infn, outfn):
    """One bookmark per article, on its first page (page N of the magazine
    is page N of the PDF)."""
    writer = PdfWriter(clone_from=infn)
    for article in issue.articles:
        if article.pages:
            writer.add_outline_item(title=article.title,
                                    page_number=article.pages[0] - 1)
    writer.write(outfn)


def write_blank_page(fp, page_size):
    writer = PdfWriter()
    writer.add_blank_page(width=page_size[0], height=page_size[1])
    writer.write(fp)


def merge_pdf(indir, outfp):
    cmd = ["qpdf", "--empty", "--pages"]
    cmd += [str(x) for x in natsorted(indir.iterdir())]
    cmd += ["--", str(outfp)]
    subprocess.run(cmd, check=True)


def insert_white_pages_in_issue(indir, outdir, whitepdffp):
    pages_we_have = [str(x) for x in indir.iterdir()]
    pages_we_have = [get_pages_from_file(x) for x in pages_we_have]
    pages_we_have = list(itertools.chain(*pages_we_have))
    pages_we_have = [int(x) for x in pages_we_have]
    pages_we_have.sort()
    logging.info(f"pages_we_have={pages_we_have}")
    for i in range(1, max(pages_we_have) + 1):
        if i not in pages_we_have:
            filename = "pages_" + str(i) + ".pdf"
            shutil.copy(whitepdffp, outdir / filename)


def extract_page(pagesfn, pagenr, outfn):
    cmd = ["qpdf", "--empty", "--pages", str(pagesfn), str(pagenr), "--", str(outfn)]
    subprocess.run(cmd, check=True)


def extract_single_pages_from_pdfs(indir, outdir):
    # When two files contain the same page, the later file (in natural order) wins
    jobs = {}
    for pagesfn in natsorted(list(indir.iterdir())):
        pages = sorted(get_pages_from_file(pagesfn), key=int)
        npages = get_number_of_pages(pagesfn)
        if npages != len(pages):
            logging.warning(f"{pagesfn.name} has {npages} pages instead of "
                            f"{len(pages)}: only the first {min(npages, len(pages))} "
                            "are used")
        for this_page_i, this_page in enumerate(pages[:npages]):
            outfn = outdir / ("pages_" + str(this_page) + ".pdf")
            jobs[outfn] = (pagesfn, this_page_i + 1, outfn)
    run_in_parallel(extract_page, jobs.values())


def generate_toc(issue, tocdir, outdir):
    """
    Generate TOC if we don't have one
    """
    if issue.toc:
        return
    logging.info("We don't have a Table Of Contents. Generating our own ...")
    tocmdfile = tocdir / "toc.md"
    tocpdffile = tocmdfile.parent / (tocmdfile.stem + ".pdf")
    lines = ["# Table of Contents", "", "| Title | Page |", "|---|---|"]
    for article in issue.articles:
        title = article.title.replace("|", "/")
        pages = ", ".join(str(x) for x in (article.pages or []))
        lines.append(f"| {title} | {pages} |")
    tocmdfile.write_text("\n".join(lines) + "\n", encoding="utf8")
    cmd = [
        "pandoc",
        "-f",
        "markdown",
        "-t",
        "pdf",
        "-V",
        "geometry:papersize={"
        + str(issue.page_size[0])
        + "pt, "
        + str(issue.page_size[1])
        + "pt}",
        str(tocmdfile),
        "-o",
        str(tocpdffile),
    ]
    logging.info(
        f"Command to convert self-generated TOC from Markdown to PDF: '{cmd}'")
    subprocess.run(cmd, check=True)
    # The TOC goes from page 2 on, over as many pages as pandoc made
    npages = get_number_of_pages(tocpdffile)
    page_range = ",".join(str(x) for x in range(2, 2 + npages))
    shutil.move(tocpdffile, outdir / f"pages_{page_range}.pdf")


# %%
def economist_rename_toc(issue, tocdir, outdir):
    is_economist = (
        issue.publication_id
        ==
        known_publication_ids["The Economist"]
    )
    if (not is_economist) or (not issue.toc):
        return
    logging.info("Economist renaming TOC")
    tocpdf = tocdir / ("pages_toc.pdf")
    if not tocpdf.is_file():
        logging.info("Skipping renaming TOC because there is no TOC")
        return
    tocpdf2 = tocdir / ("pages_toc_2.pdf")
    # Remove last page with copyright notice
    remove_last_page(tocpdf, tocpdf2)
    assert tocpdf2.is_file()
    del tocpdf
    # Get first page of TOC from PDF file
    corner = get_text_from_top_right_corner(tocpdf2, 40, 50, issue.page_size)
    # The box can also catch the end of the header, e.g. "5TheEconomist"
    number = re.search(r"\d+", corner)
    # Get number of pages of TOC
    npages = get_number_of_pages(tocpdf2)
    if not (number and 0 < int(number.group()) <= 10 and 0 < npages <= 5):
        logging.warning(f"TOC left out: no page number in its top right corner "
                        f"(found '{corner}', {npages} pages)")
        return
    page_start = int(number.group())
    logging.info(f"TOC starting page: {page_start}")
    logging.info(f"TOC number of pages: {npages}")
    # Generate page range
    page_range = ",".join([str(x)
                          for x in range(page_start, page_start + npages)])
    logging.info(f"TOC page range: {page_range}")
    # Rename file with page range
    pdffn = outdir / (f"pages_{page_range}.pdf")
    shutil.copy(tocpdf2, pdffn)


# %%
def get_page_size(fp):
    # page size in points ("default user space units")
    box = PdfReader(fp).pages[0].mediabox
    return (float(box.width), float(box.height))


# %%
def get_number_of_pages(fp):
    return len(PdfReader(fp).pages)


# %%
def get_text_from_top_right_corner(fp, xoffset, yoffset, page_size=None):
    """Text of the first page in the box xoffset wide and yoffset high
    at the top right corner (where The Economist prints the page number)."""
    if not page_size:
        page_size = get_page_size(fp)
    width, height = (float(x) for x in page_size)
    found = []

    def visitor(text, cm, tm, font_dict, font_size):
        # position of the text = text matrix times current transformation matrix
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        if x >= width - xoffset and y >= height - yoffset and text.strip():
            found.append(text.strip())

    PdfReader(fp).pages[0].extract_text(visitor_text=visitor)
    return "".join(found)


# %%
def remove_last_page(infp, outfp):
    cmd = ["qpdf", "--empty", "--pages", str(infp), "1-r2", "--", str(outfp)]
    subprocess.run(cmd, check=True)


# %%
def remove_last_page_from_articles(indir, outdir):
    run_in_parallel(remove_last_page,
                    [(infp, outdir / infp.name) for infp in indir.iterdir()])


# %%
def remove_crop_box(infp, outfp):
    # https://stackoverflow.com/questions/6451859/rendering-the-whole-media-box-of-a-pdf-page-into-a-png-file-using-ghostscript
    # In QDF form every array element sits on its own line, so blanking the
    # CropBox lines with spaces (as sed '/CropBox/,/]/s#.# #g' does) keeps all
    # byte offsets valid; readers then show the whole MediaBox.
    subprocess.run(["qpdf", "--qdf", str(infp), str(outfp)], check=True)
    lines = outfp.read_bytes().split(b"\n")
    inside = False
    for i, line in enumerate(lines):
        if inside:
            lines[i] = b" " * len(line)
            inside = b"]" not in line
        elif b"CropBox" in line:
            lines[i] = b" " * len(line)
            inside = True
    outfp.write_bytes(b"\n".join(lines))


# %%
def remove_cropbox_from_articles(indir, outdir):
    run_in_parallel(remove_crop_box,
                    [(infp, outdir / infp.name) for infp in indir.iterdir()])


# %%
def get_pages_from_file(pagesfn):
    this_pages = re.search(r".*?pages_(.*)\.pdf", str(pagesfn)).group(1)
    return this_pages.split(",")
