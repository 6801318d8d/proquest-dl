#!/usr/bin/env python3
"""economist-dl: download an issue of The Economist from ProQuest.

The same options as proquest-dl, without the publication ID, plus
--new-issue and --keep-days for a scheduled run. Examples:

    economist-dl                                   # the latest issue
    economist-dl --issue-date 2026-11-14           # the issue of that Saturday
    economist-dl --new-issue --keep-days 31 --headless --outdir ~/magazines/economist
"""

import datetime
import logging
import shutil
from pathlib import Path

from proquest_dl import proquest_dl
from proquest_dl.Issue import Issue
from proquest_dl.ProQuestWebScraper import (CaptchaRequired, IssueNotFound,
                                            known_publication_ids)

ECONOMIST = known_publication_ids["The Economist"]


def last_saturday(today):
    """The Saturday on or before today."""
    return today - datetime.timedelta(days=(today.weekday() - 5) % 7)


def latest_issue(today):
    """The date of the latest issue: issues are dated Saturday, and ProQuest
    loads them on the Thursday (sometimes the Friday) before."""
    return last_saturday(today + datetime.timedelta(days=2))


def main(argv=None, today=None):
    proquest_dl.setup()
    # --new-issue: here the latest issue is computed from today's date
    # (dated Saturday, on ProQuest from the Thursday before), so no browser
    # opens when it is already in --outdir, and an issue that ProQuest does
    # not have yet stops the run without an error
    parser = proquest_dl.build_parser(publication_id=ECONOMIST, prog="economist-dl")
    args = parser.parse_args(argv)
    args.publication_id = ECONOMIST
    today = today or datetime.date.today()

    if args.keep_days is not None:
        proquest_dl.prune(args.outdir, args.keep_days, today)

    if args.new_issue:
        if args.issue_date:
            parser.error("--new-issue chooses the issue date by itself")
        args.issue_date = latest_issue(today)
        done = Path(args.outdir) / f"TheEconomist-{args.issue_date}.pdf"
        if done.is_file():
            logging.info(f"Already downloaded: {done}")
            return
        # Resume an interrupted download of this issue; drop anything else
        # left in the temporary folder (another issue, or a run that stopped
        # before the list of articles was saved)
        tmpdir = Path(args.tmpdir)
        saved = tmpdir / "issue.json"
        if saved.is_file() and Issue.load(saved).date == args.issue_date:
            args.continue_download = True
        elif tmpdir.is_dir():
            shutil.rmtree(tmpdir)

    try:
        proquest_dl.run(args)
    except IssueNotFound as e:
        if not args.new_issue:
            raise
        logging.info(f"Not on ProQuest yet, retry later: {e}")
    except CaptchaRequired:
        if not args.new_issue:
            raise
        logging.warning("ProQuest asks for a captcha: the next run resumes the "
                        "download (the PDF files already downloaded are kept)")


if __name__ == "__main__":
    main()
