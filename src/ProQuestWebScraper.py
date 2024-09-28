#!/usr/bin/env python3

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

from Article import Article

known_publication_ids = {
    "The Economist": "41716",
    "MIT Technology Review": "35850",
}


class ProQuestWebScraper:

    def __init__(self, publication_id, artdir1, tocdir):
        self.publication_id = publication_id
        self.artdir1 = artdir1
        self.tocdir = tocdir
        self.downloaded_pages = set()

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

    def wait_element_to_be_visible(
        self,
        locator,
        timeout=5,
        starting_element=None,
    ):
        assert (len(locator) == 2)
        if not starting_element:
            starting_element = self.browser
        cond = EC.visibility_of_element_located(locator)
        try:
            el = WebDriverWait(starting_element, timeout).until(cond)
        except TimeoutException as e:
            msg = "Timed out waiting for element to be visible\n"
            msg += f"locator={locator}\n"
            msg += f"timeout={timeout}\n"
            msg += f"starting_element={starting_element}"
            logging.error(msg)
            raise e
        return el

    def wait_element_to_be_visible_css(
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
        return self.wait_element_to_be_visible(**args)

    # Specific

    def get_issue_date(self):
        issue_date_select = self.browser.find_element(
            By.CSS_SELECTOR, "select#issueSelected")
        issue_date_select = Select(issue_date_select)
        issue_date = issue_date_select.first_selected_option.text.strip()
        if self.publication_id == known_publication_ids["The Economist"]:
            issue_date = issue_date.split(";")[0].strip()
            issue_date = datetime.datetime.strptime(
                issue_date, "%b %d, %Y").date()
        elif self.publication_id == known_publication_ids["MIT Technology Review"]:
            month = datetime.datetime.strptime(
                issue_date[0:3], "%b").date().month
            year = int(issue_date.split()[1][:-1])
            issue_date = datetime.date(year=year, month=month, day=1)
        else:
            raise Exception(
                "We miss logic for issue date for this publication :(")
        return issue_date

    def download_article(self, sleep_time, article):

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

        # Add to list of downloaded pages
        # we do it now because even if we should skip the download later
        # because we already have the PDF file, if we have the PDF file
        # it means we already have the pages
        self.downloaded_pages.update(page_range)

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
            msg = (f"Skipping page(s) {article.pages} "
                   "because the file was already downloaded")
            logging.info(msg)
            return
        logging.info(f"Downloading pages {article.pages}")

        # Browse to article URL
        while True:
            self.browser.get(article.pdfurl)
            if self.check_captcha():
                continue
            else:
                break

        # Get URL of the PDF file
        css = "embed#embedded-pdf"
        el = self.wait_element_to_be_visible_css(css, timeout=30)
        pdf_file_url = el.get_attribute("src")

        # Download PDF file to local
        data = requests.get(pdf_file_url).content
        with open(pdffn, "wb") as fh:
            fh.write(data)

        # Sleep to prevent captcha
        time.sleep(random.uniform(*sleep_time))

    def download_articles(self, issue, sleep_time):
        for article in tqdm(issue.articles):
            self.download_article(sleep_time, article)

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

            # Extract URL of PDF files
            # if we don't have a PDF file, skip it
            try:
                locator = (By.CSS_SELECTOR, "a.format_pdf")
                pdfurl = result_item.find_element(
                    *locator).get_attribute("href")
            except NoSuchElementException:
                continue

            article = Article(title=title, pages=pages, pdfurl=pdfurl, is_toc=is_toc)
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

    def reject_cookies(self):
        # css = "button#onetrust-reject-all-handler"
        css = "button#onetrust-accept-btn-handler"
        el = self.wait_element_to_be_clickable_css(css)
        el.click()

    def get_browser(
        self,
        browser_app="firefox",
        headless_browser=False,
        geckodriver_path=None,
    ):
        self.browser = None
        if browser_app == "firefox":
            options = webdriver.FirefoxOptions()
            if headless_browser:
                options.add_argument("--headless")
            if geckodriver_path:
                service = webdriver.FirefoxService(
                    executable_path=str(geckodriver_path))
            else:
                service = None
            self.browser = webdriver.Firefox(options=options, service=service)
        elif browser_app == "chrome":
            options = webdriver.ChromeOptions()
            if headless_browser:
                options.add_argument("--headless")
            # executable_path param is not needed if you updated PATH
            self.browser = webdriver.Chrome(options=options)
        else:
            raise Exception(f"Unknown value for browser_app={browser_app}")
        self.browser.maximize_window()

    def check_captcha(self):
        try:
            self.browser.find_element(By.CSS_SELECTOR, "form#verifyCaptcha")
        except NoSuchElementException:
            return False
        input("Solve captcha and press key to continue...")
        return True

    def select_issue(self, journal_year, journal_month, journal_issue):
        # Select year
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR, "select#yearSelected")
        except NoSuchElementException:
            raise Exception("Cannot find year select box")
        select = Select(select_element)
        select.select_by_visible_text(str(journal_year))

        # Select month
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR, "select#monthSelected")
        except NoSuchElementException:
            raise Exception("Cannot find month select box")
        select = Select(select_element)
        month_date = datetime.date(year=1, month=journal_month, day=1)
        month_locale_full_name = month_date.strftime("%B")
        select.select_by_visible_text(month_locale_full_name)

        # Select issue
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR, "select#issueSelected")
        except NoSuchElementException:
            raise Exception("Cannot find issue select box")
        select = Select(select_element)
        select.select_by_index(journal_issue)

        # Click on "View Issue" button
        time.sleep(2)
        self.click_view_issue_btn()
        time.sleep(5)

    def click_view_issue_btn(self):
        view_issue_css = "input[value='Show issue contents']"
        view_issue = self.wait_element_to_be_clickable_css(
            view_issue_css,
            timeout=30,
        )
        view_issue.click()
