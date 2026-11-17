#!/usr/bin/env python3

import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
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
COOKIES_REJECT = (By.CSS_SELECTOR, "#onetrust-reject-all-handler")
# "Download PDF" link of an article's PDF page
PDF_LINK = (By.CSS_SELECTOR, "a[id^=downloadPDFLink]")
# "Proceed" button of Shibboleth identity provider pages (e.g. attribute consent)
PROCEED = (By.CSS_SELECTOR, "button[name=_eventId_proceed], input[name=_eventId_proceed]")


def back_on_proquest(url):
    """On a ProQuest page that is not one of the login redirects
    (www.proquest.com/shibboleth..., shibboleth-sp.prod.proquest.com).
    The query string is not looked at: it can name the login method."""
    parts = urlparse(url)
    host = parts.hostname or ""
    return ((host == "proquest.com" or host.endswith(".proquest.com"))
            and not host.startswith("shibboleth")
            and not parts.path.lower().startswith("/shibboleth"))

# One call to the browser returns the whole list of articles of the issue
ARTICLES_JS = """
return Array.from(document.querySelectorAll("li.resultItem.ltr")).map(li => {
    const q = css => li.querySelector(css);
    const title = q("div.truncatedResultsTitle");
    const ref = q("span.jnlArticle");
    const details = q("a.addFlashPageParameterformat_abstract")
        || q("a.addFlashPageParameterformat_citation");
    const pdf = q("a.format_pdf");
    return {
        title: title ? title.innerText.trim() : null,
        loc: ref ? ref.innerText : "",
        detailsurl: details ? details.href : null,
        pdfurl: pdf ? pdf.href : null,
    };
});
"""


def pdf_link_from_html(page_html):
    """Address of the "Download PDF" link of an article's PDF page, or None."""
    tag = re.search(r'<a\b[^>]*\bid="downloadPDFLink[^"]*"[^>]*>', page_html)
    href = tag and re.search(r'\bhref="([^"]+)"', tag.group(0))
    return html.unescape(href.group(1)) if href else None


def parse_pages(loc):
    """Pages of an article from its ProQuest reference.

    'The Economist; London Vol. 457, Iss. 9473,  (Nov 14, 2026): 17-19, 22.'
    gives [17, 18, 19, 22]. Raises an exception when there are no pages.
    """
    pages_str = re.match(".*:(.*)", loc).group(1).replace(".", "")
    pages_str = re.sub(r"\s+", "", pages_str)
    pages = []
    for part in pages_str.split(","):
        first, _, last = part.partition("-")
        pages.extend(range(int(first), int(last or first) + 1))
    return pages


def article_from_item(item, issue):
    """Article from one item of the issue's list (see ARTICLES_JS)."""
    title = item["title"]
    if title is None:
        raise Exception(f"Article without a title: {item}")
    is_toc = title == "Table of Contents"
    if is_toc:
        issue.toc = True

    # Pages from the reference, e.g.
    # "The Economist; London Vol. 448, Iss. 9362,  (Sep 9, 2023): 7."
    try:
        pages = parse_pages(item["loc"])
    except Exception:
        if is_toc:
            # Table of Contents has no page number
            pages = None
        else:
            raise Exception("Error extracting pages\n"
                            f"title={title}\nloc={item['loc']}")

    if not item["detailsurl"]:
        logging.info(f"We can't find details URL for {title}")
    if not item["pdfurl"]:
        logging.info(f"We can't find PDF URL for {title}")

    # A Table of Contents with its pages is placed like any article;
    # only one without pages needs economist_rename_toc
    return Article(title=title, pages=pages, pdfurl=item["pdfurl"],
                   detailsurl=item["detailsurl"],
                   is_toc=is_toc and pages is None)


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
            # monthly: the first day of the month
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
        """The PDF file to write for the article, or None to skip it."""

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
        return pdffn

    def session_cookies(self):
        """The browser's cookies. The jar keeps each cookie's domain, so
        requests sends them only to the site they belong to."""
        cookies = requests.cookies.RequestsCookieJar()
        for c in self.browser.get_cookies():
            cookies.set(c["name"], c["value"],
                        domain=c.get("domain", ""), path=c.get("path", "/"))
        return cookies

    def headers(self):
        return {"User-Agent": self.user_agent} if self.user_agent else {}

    def fetch_pdf(self, article, pdffn, cookies):
        """Read the PDF link from the article's PDF page over HTTP and save
        the PDF. False: the page needs the browser (captcha, or no link)."""
        try:
            r = requests.get(article.pdfurl, cookies=cookies,
                             headers=self.headers(), timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            logging.warning(f"Pages {article.pages}: {e}")
            return False
        link = pdf_link_from_html(r.text)
        # Pause to prevent captcha
        time.sleep(random.uniform(*self.sleep_time))
        if not link:
            return False
        self.save_pdf(link, cookies, pdffn)
        return True

    def fetch_pdf_with_browser(self, article, pdffn):
        """Open the PDF page in the browser, where a captcha can be solved."""
        logging.info(f"Opening pages {article.pages} in the browser")
        self.go_to_page(article.pdfurl)
        link = WebDriverWait(self.browser, 30).until(
            EC.presence_of_element_located(PDF_LINK)).get_attribute("href")
        self.save_pdf(link, self.session_cookies(), pdffn)

    def save_pdf(self, url, cookies, pdffn):
        r = requests.get(url, cookies=cookies, headers=self.headers(),
                         timeout=120)
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
        """Download the PDF files without opening the articles in the
        browser: their PDF pages are read over HTTP with the browser's
        cookies, 4 at a time; the browser takes over where that fails."""
        jobs = [(article, pdffn) for article in issue.articles
                if (pdffn := self.download_article(article))]
        logging.info(f"PDF files to download: {len(jobs)}")
        cookies = self.session_cookies()
        need_browser = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(self.fetch_pdf, article, pdffn, cookies):
                       (article, pdffn) for article, pdffn in jobs}
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc="PDF files"):
                if not future.result():
                    need_browser.append(futures[future])
        # Usually a captcha: the browser shows it once, then HTTP goes on
        # with the cookies that solving it gave
        for article, pdffn in need_browser:
            if not self.fetch_pdf(article, pdffn, self.session_cookies()):
                self.fetch_pdf_with_browser(article, pdffn)

    def retrieve_articles_list(self, issue):
        """
        Download list of articles
        """
        for item in self.browser.execute_script(ARTICLES_JS):
            issue.articles.append(article_from_item(item, issue))

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
                # Never offer to save the institution's password
                (Path(firefox_profile_path) / "user.js").write_text(
                    'user_pref("signon.rememberSignons", false);\n')
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

    def reject_cookies(self):
        """Click "Reject all" on ProQuest's cookie banner, if it is shown."""
        for button in self.browser.find_elements(*COOKIES_REJECT):
            if button.is_displayed():
                button.click()
                logging.info("Cookies rejected")

    def log_in(self, url, institution_url, get_credentials):
        """Log in through the institution, then open url again.

        institution_url: the address that ProQuest's "Find your institution"
        gives for the institution. If its sign-in page has a password field,
        get_credentials() gives (username, password); the form is sent once
        only, so a wrong password cannot lock the account.
        """
        logging.info(f"Logging in through {institution_url}")
        self.browser.get(institution_url)
        password = (By.CSS_SELECTOR, "input[type=password]")
        WebDriverWait(self.browser, 60).until(
            lambda b: b.find_elements(*password)
            or back_on_proquest(b.current_url))
        if not back_on_proquest(self.browser.current_url):
            credentials = get_credentials()
            if credentials is None:
                logging.info("No credentials configured: "
                             "log in in the browser window")
                return
            field = self.browser.find_element(*password)
            user = field.find_elements(
                By.XPATH, "preceding::input[@type='text' or @type='email'][1]")
            if user:
                user[0].clear()
                user[0].send_keys(credentials[0])
            # Enter, not submit(): the form must send its button's name
            field.send_keys(credentials[1] + Keys.RETURN)
            # Pages after the password (e.g. the consent to release the
            # attributes) are accepted with their "proceed" button
            deadline = time.monotonic() + 120
            while not back_on_proquest(self.browser.current_url):
                if time.monotonic() > deadline:
                    logging.warning(
                        "The login did not get back to ProQuest "
                        f"(the browser is at {self.browser.current_url}): "
                        "finish it in the browser window")
                    break
                proceed = self.browser.find_elements(*PROCEED)
                if proceed and not self.browser.find_elements(*password):
                    proceed[0].click()
                time.sleep(2)
            else:
                logging.info("Logged in")
        # Back to the publication, also when the login ended elsewhere
        self.browser.get(url)

    def wait_for_issue_page(self, url, timeout=1800, institution_url=None,
                            get_credentials=lambda: None):
        """Wait until the browser shows the issue page of the publication.

        With institution_url, log in automatically (once); otherwise, or if
        that fails, log in to ProQuest in the browser window, then open the
        publication page again, in the first tab, if ProQuest does not go
        back to it.
        """
        deadline = time.monotonic() + timeout
        # first message after 10 seconds, then every minute
        last_message = time.monotonic() - 50
        while True:
            self.reject_cookies()
            if self.browser.find_elements(*ISSUE_SELECT):
                return
            self.check_captcha()
            if institution_url:
                self.log_in(url, institution_url, get_credentials)
                institution_url = None
                continue
            now = time.monotonic()
            if now > deadline:
                raise TimeoutException("Timed out waiting for the issue page")
            if now - last_message > 60:
                logging.info("Waiting for the issue page: log in to ProQuest "
                             f"in the browser window, then open {url}")
                last_message = now
            time.sleep(2)

    def select_issue(self, issue_date):
        """Select the issue of the given date and show its contents."""
        # Select year
        logging.info(f"Selecting year: {issue_date.year}")
        try:
            select_element = self.browser.find_element(
                By.CSS_SELECTOR,
                "select#yearSelected")
        except NoSuchElementException:
            raise Exception("Cannot find year select box")
        select = Select(select_element)
        select.select_by_visible_text(str(issue_date.year))
        time.sleep(2)

        # Select month
        month_locale_full_name = issue_date.strftime("%B")
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
        logging.info(f"Selecting issue of {issue_date}")
        try:
            select = Select(self.browser.find_element(*ISSUE_SELECT))
        except NoSuchElementException:
            raise Exception("Cannot find issue select box")
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
        locator = (By.CSS_SELECTOR, "input[value='Show issue contents']")
        WebDriverWait(self.browser, 30).until(
            EC.element_to_be_clickable(locator)).click()
