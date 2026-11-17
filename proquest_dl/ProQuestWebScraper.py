#!/usr/bin/env python3

import html
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException,
                                        TimeoutException, WebDriverException)
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
PROCEED = (By.CSS_SELECTOR, "button[name=_eventId_proceed], "
           "input[type=submit][name=_eventId_proceed]")


class IssueNotFound(Exception):
    """ProQuest has no issue of the requested date (yet)."""


class CaptchaRequired(Exception):
    """ProQuest asks for a captcha, and nobody can solve it in a headless browser."""


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
        self.headless = False
        self.captcha_over_http = False
        self.password_page = None  # where the password was sent (once per run)

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

    def issue_date_of(self, option):
        """Date of an option of the issue select box: from its value
        (e.g. "02026Y11Y14#Nov 14, 2026; Vol. 461 (9525)"), else its label."""
        found = re.search(r"(\d{4})Y(\d{2})Y(\d{2})", option.get_attribute("value") or "")
        try:
            return datetime.date(*(int(x) for x in found.groups()))
        except (AttributeError, ValueError):  # no date in the value
            try:
                return self.parse_issue_date(option.text)
            except ValueError:
                return None

    def get_issue_date(self):
        select = Select(self.browser.find_element(*ISSUE_SELECT))
        return self.issue_date_of(select.first_selected_option)

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
        if "verifyCaptcha" in r.text:
            # No more requests over HTTP: they would all get the captcha
            logging.warning("ProQuest answers with a captcha")
            self.captcha_over_http = True
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
        cookies, one at a time (4 at a time made ProQuest ask for a captcha
        after about 40 articles); the browser takes over where that fails."""
        jobs = [(article, pdffn) for article in issue.articles
                if (pdffn := self.download_article(article))]
        logging.info(f"PDF files to download: {len(jobs)}")
        cookies = self.session_cookies()
        need_browser = []
        for article, pdffn in tqdm(jobs, desc="PDF files"):
            if self.captcha_over_http or not self.fetch_pdf(article, pdffn, cookies):
                need_browser.append((article, pdffn))
        # Usually a captcha: the browser shows it once (headless: the run
        # stops, see check_captcha), then HTTP goes on with the cookies that
        # solving it gave
        for article, pdffn in need_browser:
            self.captcha_over_http = False
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
        Get number of articles to download (the list can take a few
        seconds to appear; if it does not, show the issue contents again)
        """
        items = (By.CSS_SELECTOR, "li.resultItem.ltr")
        for i in range(3):
            try:
                WebDriverWait(self.browser, 15).until(
                    EC.presence_of_element_located(items))
                return len(self.browser.find_elements(*items))
            except TimeoutException:
                self.click_view_issue_btn()
        raise Exception("Cannot get number of articles")

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
        self.headless = headless_browser
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
                # Never offer to save the institution's password. Keep the
                # cookie of ProQuest's login redirector (it remembers where to
                # go back): sent with the identity provider's cross-site POST,
                # and not purged as a bounce tracker
                (Path(firefox_profile_path) / "user.js").write_text(
                    'user_pref("signon.rememberSignons", false);\n'
                    'user_pref("network.cookie.sameSite.laxByDefault", false);\n'
                    'user_pref("privacy.bounceTrackingProtection.mode", 0);\n')
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
        if self.headless:
            raise CaptchaRequired(
                "ProQuest asks for a captcha and the browser is headless: run "
                "again later with --continue-download (the PDF files already "
                "downloaded are kept), or without --headless to solve it")
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
        get_credentials() gives (username, password). The password is sent
        once per run only, so a wrong one cannot lock the account. Returns
        False when the automatic login must not be tried again.
        """
        logging.info(f"Logging in through {institution_url}")
        # Start from a clean ProQuest session: with the cookies of an old
        # one, ProQuest lands on its home page without logging in
        self.browser.delete_all_cookies()
        self.browser.get(institution_url)
        password = (By.CSS_SELECTOR, "input[type=password]")
        # ProQuest comes back to the page it was on (the publication) when
        # the login is over; the sign-in page asks for the password unless
        # the institution still has a session
        steady_since = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.browser.find_elements(*ISSUE_SELECT):
                logging.info("Logged in")
                return True
            # Visible fields only: the "Loading Session Information" page of
            # Shibboleth has a hidden one while it checks for a session
            try:
                fields = [f for f in self.browser.find_elements(*password)
                          if f.is_displayed()]
            except WebDriverException:  # the page is changing
                fields = []
            if fields and self.password_page:
                # The page where it was sent, still there, is not a new request
                if self.browser.current_url != self.password_page:
                    logging.warning("The sign-in page asks for the password "
                                    "again: it is not sent twice")
                    return False
            elif fields:
                self.password_page = self.browser.current_url
                credentials = get_credentials()
                if credentials is None:
                    logging.info("No credentials configured: "
                                 "log in in the browser window")
                    return False
                user = fields[0].find_elements(
                    By.XPATH,
                    "preceding::input[@type='text' or @type='email'][1]")
                if user:
                    user[0].clear()
                    user[0].send_keys(credentials[0])
                # Enter, not submit(): the form must send its button's name
                fields[0].send_keys(credentials[1] + Keys.RETURN)
            elif not fields:
                # Pages after the password (e.g. the consent to release
                # the attributes) are accepted with their "proceed" button
                for button in self.browser.find_elements(*PROCEED):
                    try:
                        if button.is_displayed():
                            button.click()
                            break
                    except WebDriverException:  # the page is changing
                        break
            # Back on another ProQuest page, loaded and still for 5 seconds:
            # the login is over, open the publication again
            try:
                ready = (back_on_proquest(self.browser.current_url)
                         and self.browser.execute_script(
                             "return document.readyState") == "complete")
            except WebDriverException:  # the page is changing
                ready = False
            steady_since = (steady_since or time.monotonic()) if ready else None
            if steady_since and time.monotonic() - steady_since > 5:
                self.browser.get(url)
                return True
            time.sleep(1)
        logging.warning("The login did not get back to ProQuest "
                        f"(the browser is at {self.browser.current_url}): "
                        "finish it in the browser window")
        return False

    def wait_for_issue_page(self, url, timeout=1800, institution_url=None,
                            get_credentials=lambda: None):
        """Wait until the browser shows the issue page of the publication.

        With institution_url, log in automatically, up to 3 times: about half
        of the logins come back to ProQuest's home page (?shibbolethParameters=)
        without logging in, and the next one, without the password (the
        institution keeps its session), usually works. Otherwise, or if that
        fails, log in to ProQuest in the browser window, then open the
        publication page again, in the first tab, if ProQuest does not go
        back to it.
        """
        deadline = time.monotonic() + timeout
        # first message after 10 seconds, then every minute
        last_message = time.monotonic() - 50
        attempts = 0
        while True:
            self.reject_cookies()
            if self.browser.find_elements(*ISSUE_SELECT):
                return
            self.check_captcha()
            if institution_url and attempts < 3:
                attempts += 1
                if attempts > 1:
                    logging.info(f"Not logged in: login attempt {attempts} of 3")
                if self.log_in(url, institution_url, get_credentials) is False:
                    attempts = 3
                try:  # the publication page, if the login worked
                    WebDriverWait(self.browser, 10).until(
                        EC.presence_of_element_located(ISSUE_SELECT))
                except TimeoutException:
                    pass
                last_message = time.monotonic() - 50
                continue
            now = time.monotonic()
            if now > deadline:
                raise TimeoutException("Timed out waiting for the issue page")
            if now - last_message > 60:
                logging.info("Waiting for the issue page: log in to ProQuest "
                             f"in the browser window, then open {url} "
                             f"(the browser shows '{self.browser.title}' "
                             f"at {self.browser.current_url})")
                last_message = now
            time.sleep(2)

    def select_issue(self, issue_date):
        """Select the issue of the given date and show its contents.

        The options are chosen by value (year "2026", month "11", issue
        "02026Y11Y14#..."), which do not depend on the language of the page.
        """
        for select_id, value in (("yearSelected", str(issue_date.year)),
                                 ("monthSelected", f"{issue_date.month:02d}")):
            logging.info(f"Selecting {select_id}: {value}")
            try:
                select = Select(self.browser.find_element(By.ID, select_id))
            except NoSuchElementException:
                raise Exception(f"Cannot find the select box {select_id}")
            try:
                select.select_by_value(value)
            except NoSuchElementException:
                raise IssueNotFound(f"No issue in {select_id} {value} (yet)")
            time.sleep(2)

        # Select issue
        logging.info(f"Selecting issue of {issue_date}")
        try:
            select = Select(self.browser.find_element(*ISSUE_SELECT))
        except NoSuchElementException:
            raise Exception("Cannot find issue select box")
        for i, option in enumerate(select.options):
            if self.issue_date_of(option) == issue_date:
                break
        else:
            raise IssueNotFound(
                f"No issue dated {issue_date}. Issues of that month: "
                f"{[option.text.strip() for option in select.options]}")
        select.select_by_index(i)

        # Click on "View Issue" button
        time.sleep(4)
        self.click_view_issue_btn()
        time.sleep(5)

    def click_view_issue_btn(self):
        # Click "Show issue contents" button (its label depends on the language)
        locator = (By.CSS_SELECTOR, "input[name=searchPubIssues]")
        WebDriverWait(self.browser, 30).until(
            EC.element_to_be_clickable(locator)).click()
