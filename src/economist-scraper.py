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

# %% language="sh"
# which python3

# %%
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException, NoSuchElementException
from selenium.webdriver.support.select import Select

# %%
from pathlib import Path
import subprocess
from natsort import natsorted
import requests
import pandas as pd
import logging
import itertools
import time
import json
import re
import os
import datetime
import shutil
import pdfbookmarker
import random
from tqdm.notebook import tqdm
import typing
from borb.pdf import Document
from borb.pdf import PDF

# %% [markdown]
# # Config

# %%
proquest_url = "https://www.proquest.com/publication/41716"

browser_app = "firefox"
geckodriver_path = Path(os.environ["HOME"])/".local"/"bin"/"geckodriver"
assert(geckodriver_path.is_file())
headless_browser = True

continue_download = False

datadir = Path("../data").resolve()
downloaddir = datadir/"download"
artdir = downloaddir/"articles"
pagesdir = downloaddir/"pages"
tocdir = downloaddir/"toc"

#journal_date = "2023-09-23"
journal_date = datetime.datetime.today().strftime("%Y-%m-%d")

sleep_time = (5, 15)

# %%
assert(datadir.is_dir())
if downloaddir.is_dir() and (not continue_download):
    shutil.rmtree(downloaddir)
downloaddir.mkdir(parents=True, exist_ok=continue_download)
artdir.mkdir(parents=True, exist_ok=True)
pagesdir.mkdir(parents=True, exist_ok=True)
tocdir.mkdir(parents=True, exist_ok=True)


# %% [markdown]
# # Functions

# %%
def get_journal_issue(journal_date):
    # Convert journal_date to datetime.datetime object
    journal_date = datetime.datetime.fromisoformat(journal_date)
    
    # # %w -> Weekday as a decimal number, where 0 is Sunday and 6 is Saturday.
    journal_date_weekday = int(journal_date.strftime("%w"))
    assert(journal_date_weekday==6)
    
    # Get journal year
    journal_year = int(journal_date.strftime("%Y"))
    assert(journal_year>=1900)
    assert(journal_year<=2999)
    
    # Get journal month
    journal_month = int(journal_date.strftime("%m"))
    assert(journal_month>=1)
    assert(journal_month<=12)
    
    # Get journal day
    journal_day = int(journal_date.strftime("%d"))
    assert(journal_day>=1)
    assert(journal_day<=31)
    
    # Get first day of month
    first_day_of_month = datetime.datetime(year=journal_year, month=journal_month, day=1)
    
    # Get first Saturday of month
    # # %w -> Weekday as a decimal number, where 0 is Sunday and 6 is Saturday.
    first_day_weekday = int(first_day_of_month.strftime("%w"))
    assert(first_day_weekday>=0)
    assert(first_day_weekday<=6)
    day_of_first_saturday = 1 + (6-first_day_weekday)
    assert(day_of_first_saturday>=1)
    assert(day_of_first_saturday<=31)
    
    # Get journal issue
    # 0-based index
    # lower numbers correspond to more recent issues
    journal_issue = (journal_day-day_of_first_saturday)/7
    assert(journal_issue.is_integer())
    journal_issue = int(nsat)
    assert(journal_issue>=0)
    assert(journal_issue<=4)

    return journal_year, journal_month, journal_issue


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

# %%
journal_year, journal_month, journal_issue = get_journal_issue(journal_date)
logging.info(f"Downloading year {journal_year}, month {journal_month}, issue n. {journal_issue}")

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
issue_date = browser.find_element(By.CSS_SELECTOR, "select#issueSelected")
issue_date = issue_date.text
issue_date = issue_date.split(";")[0].strip()
issue_date = datetime.datetime.strptime(issue_date, '%b %d, %Y').date()
logging.info(f"issue_date={issue_date}")

# %% [markdown]
# Build final file path and check we have not downloaded this issue yet

# %%
ts = issue_date.strftime("%Y-%m-%d")
finalfp = datadir/"final"/("TheEconomist-"+ts+".pdf")
assert(not finalfp.is_file())

# %% [markdown]
# Download list of articles

# %%
all_titles = list()
all_pages = list()
all_pdfurls = list()
toc = False
result_items = list(browser.find_elements(By.CSS_SELECTOR, "li.resultItem.ltr"))

# %%
for result_item in tqdm(result_items):
    
    title = result_item.find_element(By.CSS_SELECTOR, "div.truncatedResultsTitle").text.strip()
    
    if title != "Table of Contents":
        # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023): 7."
        loc = result_item.find_element(By.CSS_SELECTOR, "span.jnlArticle").text
        pages = re.match(".*:(.*)", loc).group(1).replace(".","")
        pages = re.sub(",\s+","-",pages).strip()
    else:    
        # Table of Contents has no page number
        # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023)."
        pages = "toc"
        toc = True
    
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
# Get size of a PDF page

# %%
# read PDF
fn = next(artdir.iterdir())
logging.info(f"Using fn='{fn}'")
doc: typing.Optional[Document] = None
with open(fn, "rb") as in_file_handle:
    doc = PDF.loads(in_file_handle)
# check whether we have read a Document
assert doc is not None
# get the width/height
w = doc.get_page(0).get_page_info().get_width()
h = doc.get_page(0).get_page_info().get_height()
pdf_page_size = [w,h]

# %%
logging.info(f"Page width={pdf_page_size[0]}; Page height={pdf_page_size[1]}")
logging.info(f"Page width={type(pdf_page_size[0])}; Page height={type(pdf_page_size[1])}")
logging.info(f"Page width={str(pdf_page_size[0])}; Page height={str(pdf_page_size[1])}")

# %% [markdown]
# If we have TOC, get page number and rename

# %%
if toc:
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
# Split pages

# %%
pagesfns = natsorted(list(artdir.iterdir()))
for pagesfn in pagesfns:
    this_pages = re.search(".*?pages_(.*)\.pdf", str(pagesfn)).group(1)
    for this_page_i, this_page in enumerate(this_pages.split("-")):
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
cmd = ["convert",
       list(artdir.iterdir())[1],
       "-fill", "white", "-colorize", "100",
       str(whitepdf)]
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %% [markdown]
# Put white pages where needed

# %%
pages_we_have = [str(x) for x in artdir.iterdir()]
pages_we_have = [re.search(".*?pages_(.*)\.pdf$",x).group(1).split("-") for x in pages_we_have]
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
ts = issue_date.strftime("%Y%m%d")
url = f"https://www.economist.com/img/b/1280/1684/90/media-assets/image/{ts}_DE_EU.jpg"

data = requests.get(url).content
with open(downloaddir/"cover.jpg","wb") as fh:
    fh.write(data)

# %%
#pt_to_inch = 0.0138888889
#pdf_page_size_pix = list()
#for m in pdf_page_size:
#    m2 = float(m)*pt_to_inch*72
#    pdf_page_size_pix.append(m2)
#pages_we_have(pdf_page_size)
#pages_we_have(pdf_page_size_pix)

# %% [markdown]
# Convert cover from JPG to PDF

# %%
page1fn = (pagesdir/"pages_1.pdf")
if page1fn.is_file():
    page1fn.unlink()

cmd = ["magick", "convert", downloaddir/"cover.jpg", "-page", str(w)+"x"+str(h)+"!", str(page1fn)]
logging.info(f"Command to convert cover from JPG to PDF: '{cmd}'")
ret = subprocess.run(cmd)
assert(ret.returncode==0)

# %% [markdown]
# Merge pages

# %%
cmd = ["qpdf", "--empty", "--pages"]
cmd += [str(x) for x in natsorted(pagesdir.iterdir())]
cmd += ["--", str(downloaddir/"output.pdf")]
logging.info(f"Command to merge pages: '{cmd}'")
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
