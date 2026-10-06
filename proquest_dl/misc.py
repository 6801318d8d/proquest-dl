# Functions

import subprocess
from natsort import natsorted
import itertools
import re
import pdfbookmarker
import logging
import shutil
import pandas as pd

def insert_bookmarks(downloaddir, issue, infn, outfn):
    # Save bookmark into txt file
    txt = ""
    for article in issue.articles:
        if not article.pages:
            continue
        txt += '+"' + article.title + '"|' + str(article.pages[0]) + "\n"
    bookmarkfn = downloaddir / "bookmark.txt"
    with open(bookmarkfn, "w") as fh:
        fh.write(txt)
    # Put bookmark into PDF
    pdfbookmarker.run_script(str(infn), str(bookmarkfn), str(outfn))

def merge_pdf(indir, outfp):
    cmd = ["qpdf", "--empty", "--pages"]
    cmd += [str(x) for x in natsorted(indir.iterdir())]
    cmd += ["--", str(outfp)]
    # logging.info(f"Command to merge pages: '{cmd}'")
    ret = subprocess.run(cmd)
    assert ret.returncode == 0

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

def extract_single_pages_from_pdfs(indir, outdir):
    pagesfns = natsorted(list(indir.iterdir()))
    for pagesfn in pagesfns:
        # logging.debug(f"pagesfn={pagesfn.stem}")
        pages = sorted(get_pages_from_file(pagesfn))
        # logging.debug(f"pages={pages}")
        for this_page_i, this_page in enumerate(pages):
            # Extract a single page
            outfn = outdir / ("pages_" + str(this_page) + ".pdf")
            pagenr = str(this_page_i + 1)
            logging.debug(
                f"Extracting from {pagesfn.name} "
                f"page nr. {pagenr} into {outfn.name}")
            cmd = [
                "qpdf",
                "--empty",
                "--pages",
                str(pagesfn),
                str(pagenr),
                "--",
                outfn,
            ]
            ret = subprocess.run(cmd)
            assert ret.returncode == 0

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
    logging.info(
        f"Command to convert self-generated TOC from Markdown to PDF: '{cmd}'")
    ret = subprocess.run(cmd)
    assert ret.returncode == 0
    shutil.move(tocpdffile, outdir / "pages_2-3.pdf")


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
    page_start = get_text_from_top_right_corner(
        tocpdf2, 40, 50, issue.page_size)
    page_start = int(page_start)
    assert 0 < page_start <= 10
    logging.info(f"TOC starting page: {page_start}")
    # Get number of pages of TOC
    npages = get_number_of_pages(tocpdf2)
    assert 0 < npages <= 5
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
def get_pages_from_file(pagesfn):
    this_pages = re.search(r".*?pages_(.*)\.pdf", str(pagesfn)).group(1)
    return this_pages.split(",")


# %%
# # Main