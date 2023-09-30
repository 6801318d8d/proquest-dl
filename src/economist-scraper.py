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

# %%
# #!/usr/bin/env python

# %%
import sys
sys.executable

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
import time
import typing

from pathlib import Path
import pandas as pd
import requests
from borb.pdf import PDF, Document, Page, SingleColumnLayout, PageLayout, Image
from decimal import Decimal
from natsort import natsorted

from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException,
                                        TimeoutException,
                                        UnexpectedAlertPresentException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait
from tqdm.notebook import tqdm

import pdfbookmarker

# %% [markdown]
# # Config

# %%
# Publication ID of "The Economist"
known_publication_ids = {
    "The Economist": "41716",
    "MIT Technology Review": "35850"
}

publication_id = known_publication_ids["MIT Technology Review"]

proquest_url = f"https://www.proquest.com/publication/{publication_id}"

browser_app = "firefox"
geckodriver_path = Path("/usr/bin/geckodriver")
assert(geckodriver_path.is_file())
headless_browser = False

continue_download = True

datadir = Path("../data").resolve()
downloaddir = datadir/"download"
artdir = downloaddir/"articles"
pagesdir = downloaddir/"pages"
tocdir = downloaddir/"toc"

journal_latest = True
journal_year = None
journal_month = None
journal_issue = None

# only needed for "MIT Technology Review"
journal_cover_url = "https://wp.technologyreview.com/wp-content/uploads/2023/08/SO23-front_cover2.png"

sleep_time = (5, 15)

# %%
assert(type(journal_latest)==bool)
if not journal_latest:
    assert(journal_year>=1900)
    assert(journal_year<=2999)
    assert(journal_month>=1)
    assert(journal_month<=12)
    assert(journal_day>=1)
    assert(journal_day<=31)
    assert(journal_issue>=0)
    assert(journal_issue<=4)
else:
    assert(journal_year is None)
    assert(journal_month is None)
    assert(journal_issue is None)

# %%
assert(datadir.is_dir())
if downloaddir.is_dir() and (not continue_download):
    logging.info(f"Removing previous download directory: {downloaddir}")
    shutil.rmtree(downloaddir)
downloaddir.mkdir(parents=True, exist_ok=continue_download)
artdir.mkdir(parents=True, exist_ok=continue_download)
pagesdir.mkdir(parents=True, exist_ok=continue_download)
tocdir.mkdir(parents=True, exist_ok=continue_download)


# %% [markdown]
# # Functions

# %%
def get_browser(browser_app="firefox", headless_browser=False, geckodriver_path=None):
    browser = None
    if browser_app == "firefox":
        options = webdriver.FirefoxOptions()
        if headless_browser:
            options.add_argument('--headless')
        if geckodriver_path:
            service = webdriver.FirefoxService(executable_path=str(geckodriver_path))
        else:
            service = None
        browser = webdriver.Firefox(options=options, service=service)
    elif browser_app == "chrome":
        options = webdriver.ChromeOptions()
        if headless_browser:
            options.add_argument('--headless')
        # executable_path param is not needed if you updated PATH
        browser = webdriver.Chrome(options=options)
    else:
        raise Exception(f"Unknown value for browser_app={browser_app}")
    browser.maximize_window()
    return browser


# %%
def check_captcha(browser):
    try:
        browser.find_element(By.CSS_SELECTOR, "form#verifyCaptcha")
    except NoSuchElementException:
        return False
    input("Solve captcha and press key to continue...")
    return True


# %%
def select_issue(journal_year, journal_month, journal_issue):
    # Select year
    try:
        select_element = browser.find_element(By.CSS_SELECTOR, "select#yearSelected")
    except NoSuchElementException:
        raise Exception(f"Cannot find year select box")
    select = Select(select_element)
    select.select_by_visible_text(str(journal_year))
    
    # Select month
    try:
        select_element = browser.find_element(By.CSS_SELECTOR, "select#monthSelected")
    except NoSuchElementException:
        raise Exception(f"Cannot find month select box")
    select = Select(select_element)
    select.select_by_visible_text(str(journal_month))
    
    # Select issue
    try:
        select_element = browser.find_element(By.CSS_SELECTOR, "select#issueSelected")
    except NoSuchElementException:
        raise Exception(f"Cannot find issue select box")
    select = Select(select_element)
    select.select_by_index(journal_issue)

    # Click on "View Issue"
    time.sleep(2)
    view_issue_css = "input[value='View issue']"
    view_issue = browser.find_element(By.CSS_SELECTOR, view_issue_css)
    view_issue.click()
    time.sleep(5)


# %% [markdown]
# # Main

# %%
logging.basicConfig(force=True, level=logging.INFO,
                   format="[%(asctime)s] [%(filename)s:%(funcName)s] [%(levelname)s] %(message)s")

# %%
logging.info("Start")

# %% [markdown]
# Open web browser

# %%
browser = get_browser(browser_app, headless_browser)

# %% [markdown]
# DELETE THIS CELL

# %%
from mylogin import mylogin
mylogin(browser, datadir)

# %% [markdown]
# Connect to ProQuest website

# %%
logging.info(f"Conneting to ProQuest URL={proquest_url}")
browser.get(proquest_url)
time.sleep(5)

# %% [markdown]
# Reject cookies

# %%
css = "button#onetrust-reject-all-handler"
try:
    el = browser.find_element(By.CSS_SELECTOR, css)
except NoSuchElementException:
    pass
else:
    el.click()
    time.sleep(3)

# %%
css = "div#pubContentSummaryFormZone > div.row > div.contentSummaryHeader > h1"
publication_name = browser.find_element(By.CSS_SELECTOR, css).text.strip()
logging.info(f"publication_name='{publication_name}'")

# %% [markdown]
# Select issue to download

# %%
if not journal_latest:
    select_issue(journal_year, journal_month, journal_issue)

# %% [markdown]
# Get number of articles to download

# %%
while True:
    max_len = len(list(browser.find_elements(By.CSS_SELECTOR, "li.resultItem.ltr")))
    logging.info(f"max_len={max_len}")
    if max_len == 0:
        browser.find_element(By.CSS_SELECTOR, view_issue_css).click()
        time.sleep(5)
        browser.refresh()
        time.sleep(5)
        continue
    else:
        break

# %% [markdown]
# Get date in which the issue was released

# %%
issue_date_select = browser.find_element(By.CSS_SELECTOR, "select#issueSelected")
issue_date_select = Select(issue_date_select)
issue_date = issue_date_select.first_selected_option.text.strip()
if publication_id == known_publication_ids["The Economist"]:
    issue_date = issue_date.split(";")[0].strip()
    issue_date = datetime.datetime.strptime(issue_date, '%b %d, %Y').date()
elif publication_id == known_publication_ids["MIT Technology Review"]:
    month = datetime.datetime.strptime(issue_date[0:3], "%b").date().month
    year = int(issue_date.split()[1][:-1])
    issue_date = datetime.date(year=year, month=month, day=1)
else:
    raise Exception("We miss logic for issue date for this publication :(")
logging.info(f"issue_date={issue_date}")

# %% [markdown]
# Build final file path and check we have not downloaded this issue yet

# %%
ts = issue_date.strftime("%Y-%m-%d")
pnamecl = publication_name.replace(" ","").strip()
finalfp = datadir/"final"/(pnamecl+"-"+ts+".pdf")
assert(not finalfp.is_file())

# %% [markdown]
# Download list of articles

# %%
all_titles = list()
all_pages = list()
all_pdfurls = list()
toc = False
result_items = list(browser.find_elements(By.CSS_SELECTOR, "li.resultItem.ltr"))

for result_item in tqdm(result_items):
    
    title = result_item.find_element(By.CSS_SELECTOR, "div.truncatedResultsTitle").text.strip()
    if title == "Table of Contents":
        toc = True
    # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023): 7."
    loc = result_item.find_element(By.CSS_SELECTOR, "span.jnlArticle").text

    # Try to extract pages from loc
    try:
        pages = re.match(".*:(.*)", loc).group(1).replace(".","")
        pages = re.sub(",\s+","-",pages).strip()
    except:
        if title == "Table of Contents":
            # Table of Contents has no page number
            # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023)."
            pages = "toc"
        else:
            pages = "na"

    # Extract URL of PDF files
    # if we don't have a PDF file, skip it
    try:
        pdfurl = result_item.find_element(By.CSS_SELECTOR, "a.format_pdf").get_attribute("href")
    except NoSuchElementException:
        continue
        
    all_titles.append(title)
    all_pages.append(pages)
    all_pdfurls.append(pdfurl)

# %% [markdown]
# Downloads single articles

# %%
for pdfi, pdfurl in tqdm(enumerate(all_pdfurls), total=len(all_pdfurls)):

    # Get page numbers
    this_pages = all_pages[pdfi]

    # Generate PDF file name
    if this_pages != "toc":
        pdffn = artdir/("pages_"+this_pages+".pdf")
    else:
        pdffn = tocdir/("pages_"+this_pages+".pdf")
    if pdffn.is_file():
        logging.info(f"Skipping page(s) {this_pages} because already downloaded")
        continue
    logging.info(f"Downloading pages {this_pages}")

    # Browse to article URL
    while True:
        browser.get(pdfurl)
        if check_captcha(browser):
            continue
        else:
            break

    # Get URL of the PDF file
    pdf_file_url = browser.find_element(By.CSS_SELECTOR, "embed#embedded-pdf").get_attribute("src")

    # Download PDF file to local
    data = requests.get(pdf_file_url).content
    with open(pdffn, "wb") as fh:
        fh.write(data)

    # Remove last page with copyright notice
    cmd = ["qpdf", str(pdffn), "--replace-input", "--pages", str(pdffn), "1-r2", "--"]
    res = subprocess.run(cmd)
    assert(res.returncode==0)

    # Sleep to prevent captcha
    time.sleep(random.uniform(*sleep_time))

# %% [markdown]
# Close web browser

# %%
browser.close()

# %% [markdown]
# Remove CropBox

# %%
pagesfns = natsorted(list(artdir.iterdir()))
for pagesfn in pagesfns:
    # Create text editor-friendly PDF file
    # https://qpdf.readthedocs.io/en/stable/cli.html#option-qdf
    cmd = ["qpdf", "--qdf", "--replace-input", str(pagesfn), "--"]
    res = subprocess.run(cmd)
    assert(res.returncode==0)
    # Remove cropbox
    # https://stackoverflow.com/questions/6451859/rendering-the-whole-media-box-of-a-pdf-page-into-a-png-file-using-ghostscript
    cmd = ["sed", "-i", "-e", "/CropBox/,/]/s#.# #g", str(pagesfn)]
    #logging.info(cmd)
    res = subprocess.run(cmd)
    assert(res.returncode==0)


# %% [markdown]
# Get size of a PDF page

# %%
def dpi_to_cm(dpi):
    dpi_to_inch = 1/72
    inch_to_cm = 2.54
    return float(dpi) * dpi_to_inch * inch_to_cm


# %%
# read PDF
fn = next(artdir.iterdir())
logging.info(f"Using fn='{fn}'")
doc: typing.Optional[Document] = None
with open(fn, "rb") as in_file_handle:
    doc = PDF.loads(in_file_handle)
# check whether we have read a Document
assert doc is not None
# get page size in "default user space units"
pdf_page_size_dpi = doc.get_page(0).get_page_info().get_size()
pdf_page_size_cm = [dpi_to_cm(x) for x in pdf_page_size_dpi]
logging.info(f"Page size dpi = {pdf_page_size_dpi}")
logging.info(f"Page size cm = {pdf_page_size_cm}")

# %% [markdown]
# If we have TOC, get page number and rename

# %%
if publication_id == known_publication_ids["The Economist"] and \
   toc:
    logging.info("The Economist doesn't provide " \
                 "page numbers for Table Of Contents. " \
                 "Getting them from PDF file ...")
    tocpdf = tocdir/("pages_toc.pdf")
    assert(tocpdf.is_file())
    cmd = ["pdftotext", str(tocpdf)]
    ret = subprocess.run(cmd)
    assert(ret.returncode==0)
    toctxt = tocdir/("pages_toc.txt")
    assert(toctxt.is_file())
    with open(toctxt, "r") as fh:
        toctext = fh.read()
    page_start = min([int(x) for x in re.findall("^\d$",toctext,re.MULTILINE)])
    logging.info(f"TOC starting page: {page_start}")
    assert(page_start>0)
    toctxt.unlink()
    pdfinfo = subprocess.run(["pdfinfo", str(tocpdf)], capture_output=True, text=True).stdout
    npages = re.search("^Pages:(.*)$", pdfinfo, re.M).group(1).strip()
    npages = int(npages)
    assert(npages>0)
    logging.info(f"TOC consists of {npages} pages")
    this_pages = str(page_start) + "-" + str(page_start+npages-1)
    logging.info(f"TOC cover pages {this_pages}")
    pdffn = artdir/("pages_"+this_pages+".pdf")
    shutil.copy(tocpdf, pdffn)

# %% [markdown]
# Generate TOC if we don't have one

# %%
if not toc:
    logging.info("We don't have a Table Of Contents. Generating our own ...")
    tocmdfile = tocdir/"toc.md"
    tocpdffile = tocmdfile.parent/(tocmdfile.stem+".pdf")
    fh = open(tocmdfile, "w", encoding="utf8")
    fh.write("# Table of Contents\n\n")
    df = pd.DataFrame({"Title":all_titles, "Page": all_pages})
    fh.write(df.to_markdown(index=False))
    fh.close()
    cmd = ["pandoc",
           "-f", "markdown",
           "-t", "pdf",
           "-V", "geometry:papersize={" + pdf_page_size[0] + "pt, " + pdf_page_size[1] + "pt}",
           str(tocmdfile),
           "-o", str(tocpdffile)]
    logging.info(f"Command to convert self-generated TOC from Markdown to PDF: '{cmd}'")
    ret = subprocess.run(cmd)
    assert(ret.returncode==0)
    shutil.move(tocpdffile, artdir/"pages_2-3.pdf")


# %% [markdown]
# Split single pages from original PDF files

# %%
def convert_page_range(this_pages):
    # Convert a-b into a,a+1,a+2,...,b-2,b-1,b
    while res := re.search("(\d+)-(\d+)", this_pages):
        #logging.info(f"{res.start()}, {res.end()}")
        #logging.info(f"{res.group(1)}, {res.group(2)}")
        start = int(res.group(1))
        end = int(res.group(2))
        seq = [str(x) for x in range(start,end+1)]
        this_pages = this_pages[:res.start()] + \
            ",".join(seq) + \
            this_pages[res.end():]
    return this_pages


# %%
def get_pages_from_file(pagesfn):
    this_pages = re.search(".*?pages_(.*)\.pdf", str(pagesfn)).group(1)
    this_pages = convert_page_range(this_pages)
    return this_pages


# %%
pagesfns = natsorted(list(artdir.iterdir()))
for pagesfn in pagesfns:
    logging.info(f"pagesfn={pagesfn.stem}")
    this_pages = get_pages_from_file(pagesfn)
    logging.info(f"this_pages={this_pages}")
    # Split pages across commas ","
    for this_page_i, this_page in enumerate(this_pages.split(",")):
        outfn = pagesdir/("pages_"+this_page+".pdf")
        cmd = ["qpdf", "--empty", 
               "--pages", str(pagesfn), str(this_page_i+1),
               "--", outfn]
        ret = subprocess.run(cmd)
        assert(ret.returncode==0)

# %% [markdown]
# Generate white page

# %%
whitepdf = downloaddir/"blank.pdf"
guineafp = natsorted(list(artdir.iterdir()))[0]
logging.info(f"Using {guineafp}")
cmd = ["magick", "convert",
       str(guineafp),
       "-fill", "white", "-colorize", "100",
       str(whitepdf)]
logging.info(f"Running {cmd}")
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %% [markdown]
# Put white pages where needed

# %%
pages_we_have = [str(x) for x in artdir.iterdir()]
pages_we_have = [get_pages_from_file(x).split(",") for x in pages_we_have]
pages_we_have = list(itertools.chain(*pages_we_have))
pages_we_have = [int(x) for x in pages_we_have]
pages_we_have.sort()
logging.info(f"pages_we_have={pages_we_have}")
for i in range(1,max(pages_we_have)+1):
    if i not in pages_we_have:
        filename = ("pages_"+str(i)+".pdf")
        shutil.copy(whitepdf, pagesdir/filename)

# %% [markdown]
# Download cover

# %%
if publication_id==known_publication_ids["The Economist"]:
    # Download cover from the web
    ts = issue_date.strftime("%Y%m%d")
    journal_cover_url = f"https://www.economist.com/img/b/1280/1684/90" \
          f"/media-assets/image/{ts}_DE_EU.jpg"
data = requests.get(journal_cover_url).content
ext = journal_cover_url.split(".")[-1]
logging.info(f"ext={ext}")
coverfp = downloaddir/("cover."+ext)
logging.info(f"coverfp={coverfp}")
with open(coverfp,"wb") as fh:
    fh.write(data)

# %% [markdown]
# Remove the white page we have instead of the cover

# %%
page1fn = (pagesdir/"pages_1.pdf")
if page1fn.is_file():
    page1fn.unlink()

# %%
# Resize cover
coverfp2 = coverfp.parent / (coverfp.stem + "_resized" + coverfp.suffix)
magick_page_size = str(pdf_page_size_dpi[0]) + "x" + str(pdf_page_size_dpi[1]) + "!"
cmd = ["magick", "convert",
       str(coverfp),
       "-resize", magick_page_size,
       str(coverfp2)]
logging.info(f"Command to convert cover from JPG to PDF: '{cmd}'")
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %%
# create Document
doc: Document = Document()

# create Page
page: Page = Page(width=pdf_page_size_dpi[0], height=pdf_page_size_dpi[1])

# add Page to Document
doc.add_page(page)

# set a PageLayout
layout: PageLayout = SingleColumnLayout(page)

# add an Image
layout.add(
    Image(
        coverfp,
        width=page.get_page_info().get_width() / 2,
        height=page.get_page_info().get_height() / 2
    )
)

# store
with open(page1fn, "wb") as pdf_file_handle:
    PDF.dumps(pdf_file_handle, doc)

# %% [markdown]
# Merge pages

# %%
cmd = ["qpdf", "--empty", "--pages"]
cmd += [str(x) for x in natsorted(pagesdir.iterdir())]
cmd += ["--", str(downloaddir/"output.pdf")]
#logging.info(f"Command to merge pages: '{cmd}'")
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %% [markdown]
# Insert bookmarks

# %%
bookmarkfn = downloaddir/"bookmark.txt"
with open(bookmarkfn, "w") as fh:
    for titlei, title in enumerate(all_titles):
        fh.write("+\""+title+"\"|"+all_pages[titlei].split("-")[0]+"\n")

# %%
# def run_script(pdf_in_filename, bookmarks_filename, pdf_out_filename=None):
infn = downloaddir/"output.pdf"
outfn = downloaddir/"output_bookmarked.pdf"
pdfbookmarker.run_script(str(infn), str(bookmarkfn), str(outfn))
infn.unlink()

# %% [markdown]
# Compress

# %%
infn = outfn
outfn = downloaddir/"output_bookmarked_compressed.pdf"
cmd = ["ps2pdf", "-dPDFSETTINGS=/ebook", str(infn), str(outfn)]
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %% [markdown]
# Move PDF to "final" subfolder

# %%
shutil.move(outfn, finalfp)

# %%
logging.info("Ended")

# %%
