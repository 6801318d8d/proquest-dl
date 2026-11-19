"""Offline checks of the cover addresses (cover_url), without the network."""

import datetime
from types import SimpleNamespace

from proquest_dl import proquest_dl
from proquest_dl.ProQuestWebScraper import known_publication_ids

# Two cards of New Scientist's yearly archive, as the page has them
NEW_SCIENTIST_ARCHIVE = """
<div class="issue-card"> <a href="https://www.newscientist.com/issue/3620/" class="issue-card__link">
 <div class="issue-card__details"> <div class="issue-card__date">7 November 2026</div>
 <div class="issue-card__number"> Issue 3620 </div> </div>
 <img width="196" height="258" src="https://www.newscientist.com/wp-content/uploads/2026/11/B.jpg?w=196" alt="" /></a></div>
<div class="issue-card"> <a href="https://www.newscientist.com/issue/3619/" class="issue-card__link">
 <div class="issue-card__details"> <div class="issue-card__date">31 October 2026</div>
 <div class="issue-card__number"> Issue 3619 </div> </div>
 <img width="196" height="258" src="https://www.newscientist.com/wp-content/uploads/2026/10/A.jpg?w=196" alt="" /></a></div>
"""


def test_cover_url_economist_and_pressreader():
    date = datetime.date(2026, 11, 9)
    assert proquest_dl.cover_url(known_publication_ids["The Economist"], date) == \
        "https://www.economist.com/content-assets/images/20261109_DE_EU.jpg"
    assert proquest_dl.cover_url(known_publication_ids["Barron's"], date) == \
        "https://t.prcdn.co/img?file=12652026110900000000001001&page=1&width=1700"
    assert proquest_dl.cover_url("9197", date) is None  # NYT Book Review: no source


def mit_issue(year, slug, cover):
    return {"name": "magazine-issue",
            "config": {"permalink": f"https://www.technologyreview.com/magazines/{slug}/",
                       "yearPublished": str(year)},
            "children": [{"name": "image", "config": {"url": cover}}]}


def test_cover_url_mit_technology_review(monkeypatch):
    # the site's list of issues (a year per page) and the issues' own pages
    answers = {
        proquest_dl.MIT_TR_ISSUES.format(1): [mit_issue(2026, "nd", "https://c/ND26.jpg"),
                                              mit_issue(2026, "so", "https://c/SO26.jpg")],
        proquest_dl.MIT_TR_ISSUES.format(2): [{"name": "magazine-card", "config": {"title": 2025},
                                               "children": []},
                                              mit_issue(2025, "nd25", "https://c/ND25.jpg")],
        "https://www.technologyreview.com/magazines/nd/":
            '{"title":"x","issueDate":"November/December 2026","description":"y"}',
        "https://www.technologyreview.com/magazines/so/":
            '{"issueDate":"September/October 2026"}',
    }
    asked = []

    def fake_get(url, headers, timeout):
        asked.append(url)
        return SimpleNamespace(json=lambda: answers[url], text=answers.get(url),
                               raise_for_status=lambda: None)

    monkeypatch.setattr(proquest_dl.requests, "get", fake_get)
    mit = known_publication_ids["MIT Technology Review"]
    # ProQuest dates "Nov/Dec 2026" 1 November 2026: the newest issue, two requests
    assert proquest_dl.cover_url(mit, datetime.date(2026, 11, 1)) == "https://c/ND26.jpg"
    assert len(asked) == 2
    assert proquest_dl.cover_url(mit, datetime.date(2026, 9, 1)) == "https://c/SO26.jpg"
    # an issue the site does not list: no cover, no error, no page of another year
    asked.clear()
    assert proquest_dl.cover_url(mit, datetime.date(2026, 7, 1)) is None
    assert proquest_dl.MIT_TR_ISSUES.format(2) in asked
    assert "https://www.technologyreview.com/magazines/nd25/" not in asked


def test_cover_url_new_scientist(monkeypatch):
    asked = []

    def fake_get(url, headers, timeout):
        asked.append(url)
        return SimpleNamespace(text=NEW_SCIENTIST_ARCHIVE, raise_for_status=lambda: None)

    monkeypatch.setattr(proquest_dl.requests, "get", fake_get)
    ns = known_publication_ids["New Scientist"]
    assert proquest_dl.cover_url(ns, datetime.date(2026, 10, 31)) == \
        "https://www.newscientist.com/wp-content/uploads/2026/10/A.jpg?w=1200"
    assert asked == ["https://www.newscientist.com/issues/2026/"]
    # an issue missing from the archive: no cover, no error
    assert proquest_dl.cover_url(ns, datetime.date(2026, 10, 24)) is None
