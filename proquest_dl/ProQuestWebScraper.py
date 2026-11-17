#!/usr/bin/env python3

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait
from tqdm import tqdm
import requests
import time
import logging
import datetime
import random
import re

from proquest_dl.Article import Article

known_publication_ids = {
    "The Economist": "41716",
    "MIT Technology Review": "35850",
}

CAPTCHA = (By.CSS_SELECTOR, "form#verifyCaptcha")
ISSUE_SELECT = (By.CSS_SELECTOR, "select#issueSelected")


class ProQuestWebScraper:

    def __init__(self, publication_id, artdir1, tocdir, sleep_time=(2, 5),
                 exclude_sections=()):
        self.publication_id = publication_id
        self.artdir1 = artdir1
        self.tocdir = tocdir
        self.downloaded_pages = set()
        self.sleep_time = tuple(sleep_time)
        self.exclude_sections = set(exclude_sections)
        self.user_agent = None
        self.downloads = []

    # Generic

    def wait_element_to_be_clickable(
        self,
        locator,
        timeout=5,
        starting_element=None,
    ):
        assert (len(locator) == 2)
        if not starting_element:
            starting_element = self.browser
        cond = EC.element_to_be_clickable(locator)
        try:
            el = WebDriverWait(starting_element, timeout).until(cond)
        except TimeoutException as e:
            msg = "Timed out waiting for element to be clickable\n"
            msg += f"locator={locator}\n"
            msg += f"timeout={timeout}\n"
            msg += f"starting_element={starting_element}"
            logging.error(msg)
            raise e
        return el

    def wait_element_to_be_clickable_css(
        self,
        css,
        timeout=5,
        starting_element=None,
    ):
        locator = (By.CSS_SELECTOR, css)
        args = {
            "locator": locator,
            "timeout": timeout,
            "starting_element": starting_element,
        }
        return self.wait_element_to_be_clickable(**args)

    # Specific

    def get_section(self):
        try:
            locator = (By.XPATH, '//div[contains(text(),"Section")]')
            el = self.browser.find_element(*locator)
        except NoSuchElementException:
            return None
        locator = (By.XPATH, 'following-sibling::*[1]')
        el2 = el.find_element(*locator)
        return el2.text.strip()

    def parse_issue_date(self, text):
        """Date of an issue from its label in the issue select box."""
        text = text.strip()
        if self.publication_id == known_publication_ids["The Economist"]:
            # e.g. "Nov 14, 2026; Vol. 457 (9473)"
            return datetime.datetime.strptime(
                text.split(";")[0].strip(), "%b %d, %Y").date()
        elif self.publication_id == known_publication_ids["MIT Technology Review"]:
            month = datetime.datetime.strptime(
                text[0:3], "%b").date().month
            year = int(text.split()[1][:-1])
            return datetime.date(year=year, month=month, day=1)
        else:
            raise Exception(
                "We miss logic for issue date for this publication :(")

    def get_issue_date(self):
        select = Select(self.browser.find_element(*ISSUE_SELECT))
        return self.parse_issue_date(select.first_selected_option.text)

    def go_to_page(self, url):
        while True:
            self.browser.get(url)
            if not self.check_captcha():
                break
            # ProQuest thinks we are too fast: slow down for the rest of the run
            self.sleep_time = tuple(min(2 * x, 60) for x in self.sleep_time)
            logging.warning(
                f"Pauses between pages raised to {self.sleep_time} seconds")
        # Sleep to prevent captcha
        time.sleep(random.uniform(*self.sleep_time))

    def download_article(self, article):

        # Check whether we already have all the pages of this article
        if article.is_toc:
            page_range = {"toc"}
        else:
            page_range = set(range(min(article.pages), max(article.pages)+1))
        missing_pages = page_range.difference(self.downloaded_pages)
        if not missing_pages:
            msg = (f"Skipping page(s) {article.pages} "
                   "because we already have all the pages")
            logging.info(msg)
            return

        if not article.pdfurl:
            msg = (f"Skipping page(s) {article.pages} "
                   "because there is no associated PDF")
            logging.info(msg)
            return

        # Generate PDF file name
        if article.is_toc:
            filen = "pages_toc.pdf"
            pdffn = self.tocdir / filen
        else:
            pages_str = ",".join([str(x) for x in sorted(article.pages)])
            filen = "pages_" + pages_str + ".pdf"
            pdffn = self.artdir1 / filen

        # Check if we have already downloaded this PDF file
        if pdffn.is_file():
            self.downloaded_pages.update(page_range)
            msg = (f"Skipping page(s) {article.pages} "
                   "because the file was already downloaded")
            logging.info(msg)
            return

        # The section is only on the details page: visit it only when needed
        if self.exclude_sections and article.detailsurl:
            self.go_to_page(article.detailsurl)
            section = self.get_section()
            logging.info(f"Section={section}")
            if section in self.exclude_sections:
                logging.info(f"Skipping page(s) {article.pages} "
                             f"because section '{section}' is excluded")
                return

        self.downloaded_pages.update(page_range)
        logging.info(f"Downloading pages {article.pages}")

        # Browse to article URL
        self.go_to_page(article.pdfurl)

        # Get URL of the PDF file
        locator = (By.CSS_SELECTOR, "embed#embedded-pdf")
        el = WebDriverWait(self.browser, 30).until(
            EC.presence_of_element_located(locator))
        pdf_file_url = el.get_attribute("src")

        # Download the PDF file in the background while the browser goes on
        cookies = {c["name"]: c["value"] for c in self.browser.get_cookies()}
        self.downloads.append(self.executor.submit(
            self.save_pdf, pdf_file_url, cookies, pdffn))

    def save_pdf(self, url, cookies, pdffn):
        headers = {"User-Agent": self.user_agent} if self.user_agent else {}
        r = requests.get(url, cookies=cookies, headers=headers, timeout=120)
        r.raise_for_status()
        if b"%PDF" not in r.content[:1024]:
            raise Exception(f"{pdffn.name}: the server did not send a PDF "
                            f"(Content-Type {r.headers.get('Content-Type')})")
        # Write under another name first: an interrupted run leaves no
        # truncated file that --continue-download would take as complete
        tmpfn = pdffn.with_suffix(".part")
        tmpfn.write_bytes(r.content)
        tmpfn.replace(pdffn)

    def download_articles(self, issue):
        self.downloads = []
        with ThreadPoolExecutor(max_workers=4) as self.executor:
            for article in tqdm(issue.articles, desc="Articles"):
                self.download_article(article)
            for future in tqdm(self.downloads, desc="PDF files"):
                future.result()

    def retrieve_articles_list(self, issue):
        """
        Download list of articles
        """
        result_items = self.browser.find_elements(
            By.CSS_SELECTOR, "li.resultItem.ltr")

        for result_item in tqdm(result_items):

            # Retrieve article title
            locator = (By.CSS_SELECTOR, "div.truncatedResultsTitle")
            title = result_item.find_element(*locator).text.strip()
            # whether this article is the Table of Contents
            is_toc = False
            if title == "Table of Contents":
                issue.toc = True
                is_toc = True

            # Retrieve article reference
            # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023): 7."
            locator = (By.CSS_SELECTOR, "span.jnlArticle")
            loc = result_item.find_element(*locator).text

            # Try to extract pages from reference
            # * -> Causes the resulting RE to match
            #      0 or more repetitions of the preceding RE
            try:
                pages_str = re.match(".*:(.*)", loc).group(1).replace(".", "")
                pages_str = re.sub(r"\s+", "", pages_str).strip()
                pages = list()
                for page_part in pages_str.split(","):
                    page_part = page_part.strip()
                    for page in page_part.split("-"):
                        page = int(page.strip())
                        pages.append(page)
            except Exception:
                if title == "Table of Contents":
                    # Table of Contents has no page number
                    # loc = "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023)."
                    pages = None
                else:
                    # pages = "na"
                    msg = "Error extracting pages\n"
                    msg += f"title={title}\n"
                    msg += f"loc={loc}"
                    raise Exception(msg)

            # Extract URL of abstract/details page
            try:
                # Locator for "Abstract/Details"
                locator = (By.CSS_SELECTOR,
                           "a.addFlashPageParameterformat_abstract")
                detailsurl = result_item.find_element(*locator) \
                    .get_attribute("href")
            except NoSuchElementException:
                try:
                    # Locator for "Details"
                    locator = (By.CSS_SELECTOR,
                               "a.addFlashPageParameterformat_citation")
                    detailsurl = result_item.find_element(*locator) \
                        .get_attribute("href")
                except NoSuchElementException:
                    logging.info(f"We can't find details URL "
                                 f" for {title}")
                    detailsurl = None

            # Extract URL of PDF files
            try:
                locator = (By.CSS_SELECTOR, "a.format_pdf")
                pdfurl = result_item.find_element(*locator) \
                                    .get_attribute("href")
            except NoSuchElementException:
                logging.info(f"We can't find PDF URL "
                             f" for {title}")
                pdfurl = None

            article = Article(title=title, pages=pages,
                              pdfurl=pdfurl, detailsurl=detailsurl,
                              is_toc=is_toc)
            issue.articles.append(article)

    def get_art_count(self):
        """
        Get number of articles to download
        """
        for i in range(3):
            count = len(list(self.browser.find_elements(
                By.CSS_SELECTOR, "li.resultItem.ltr")))
            if count == 0:
                self.click_view_issue_btn()
                time.sleep(5)
                self.browser.refresh()
                time.sleep(5)
                continue
            else:
                break
        if count == 0:
            raise Exception("Cannot get number of articles")
        return count

    def get_publication_name(self):
        css = "div#pubContentSummaryFormZone > div.row > div.contentSummaryHeader > h1"
        publication_name = self.browser.find_element(
            By.CSS_SELECTOR, css).text.strip()
        return publication_name

    def get_browser(
        self,
        browser_app="firefox",
        headless_browser=False,
        geckodriver_path=None,
        firefox_profile_path=None
    ):
        self.browser = None
        if browser_app == "firefox":
            options = webdriver.FirefoxOptions()
            # get() returns once the HTML is parsed, without waiting
            # for images, ads and trackers
            options.page_load_strategy = "eager"
            if headless_browser:
                options.add_argument("--headless")
            if firefox_profile_path:
                # Use the profile in place, not a temporary copy, so the
                # ProQuest login is kept from one run to the next
                Path(firefox_profile_path).mkdir(parents=True, exist_ok=True)
                options.add_argument("-profile")
                options.add_argument(str(firefox_profile_path))
            service = None
            if geckodriver_path:
                service = webdriver.FirefoxService(
                    executable_path=str(geckodriver_path)
                )
            self.browser = webdriver.Firefox(options=options, service=service)
        elif browser_app == "chrome":
            options = webdriver.ChromeOptions()
            options.page_load_strategy = "eager"
            if headless_browser:
                options.add_argument("--headless")
            # executable_path param is not needed if you updated PATH
            self.browser = webdriver.Chrome(options=options)
        else:
            raise Exception(f"Unknown value for browser_app={browser_app}")
        self.browser.maximize_window()
        self.user_agent = self.browser.execute_script(
            "return navigator.userAgent")

    def check_captcha(self, timeout=1800):
        """If ProQuest shows a captcha, wait until it is solved in the browser."""
        if not self.browser.find_elements(*CAPTCHA):
            return False
        logging.warning("Captcha: solve it in the browser window, "
                        "the download goes on by itself")
        WebDriverWait(self.browser, timeout, poll_frequency=2).until_not(
            EC.presence_of_element_located(CAPTCHA))
        return True

    def wait_for_issue_page(self, url, timeout=1800):
        """Wait until the browser shows the issue page of the publication.

        The first time, log in to ProQuest in the browser window; then open
        the publication page again, in the first tab, if ProQuest does not
        go back to it.
        """
        deadline = time.monotonic() + timeout
        last_message = 0
        while True:
            if self.browser.find_elements(*ISSUE_SELECT):
                return
            self.check_captcha()
            now = time.monotonic()
            if now > deadline:
                raise TimeoutException("Timed out waiting for the issue page")
            if now - last_message > 60:
                logging.info("Waiting for the issue page: log in to ProQuest "
                             f"in the browser window, then open {url}")
                last_message = now
            time.sleep(2)

    def select_issue(self, journal_year, journal_month, journal_issue=None,
                     issue_date=None):
        """Select an issue by its index in the month, or by its date."""
        # Select year
        logging.info(f"Selecting year: {journal_year}")
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR,
                "select#yearSelected")
        except NoSuchElementException:
            raise Exception("Cannot find year select box")
        select = Select(select_element)
        select.select_by_visible_text(str(journal_year))
        time.sleep(2)

        # Select month
        month_date = datetime.date(year=1, month=journal_month, day=1)
        month_locale_full_name = month_date.strftime("%B")
        logging.info(f"Selecting month: {month_locale_full_name}")
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR,
                "select#monthSelected")
        except NoSuchElementException:
            raise Exception("Cannot find month select box")
        select = Select(select_element)
        select.select_by_visible_text(month_locale_full_name)
        time.sleep(2)

        # Select issue
        try:
            select_element = self.browser.find_element(*ISSUE_SELECT)
        except NoSuchElementException:
            raise Exception("Cannot find issue select box")
        select = Select(select_element)
        if issue_date is None:
            logging.info(f"Selecting issue: {journal_issue}")
            select.select_by_index(journal_issue)
        else:
            logging.info(f"Selecting issue of {issue_date}")
            labels = [option.text.strip() for option in select.options]
            for i, label in enumerate(labels):
                try:
                    if self.parse_issue_date(label) == issue_date:
                        break
                except ValueError:
                    continue
            else:
                raise Exception(f"No issue dated {issue_date}. "
                                f"Issues of that month: {labels}")
            select.select_by_index(i)

        # Click on "View Issue" button
        time.sleep(4)
        self.click_view_issue_btn()
        time.sleep(5)

    def click_view_issue_btn(self):
        # Click "Show issue contents" button
        view_issue_css = "input[value='Show issue contents']"
        view_issue = self.wait_element_to_be_clickable_css(
            view_issue_css,
            timeout=30,
        )
        view_issue.click()
