#!/usr/bin/env python3
"""economist-dl: download an issue of The Economist from ProQuest.

The same options as proquest-dl, without the publication ID, plus
--new-issue and --keep-days for a daily scheduled run. Examples:

    economist-dl                                   # the latest issue
    economist-dl --issue-date 2026-11-14           # the issue of that Saturday
    economist-dl --new-issue --keep-days 31 --headless --outdir ~/magazines/economist
"""

import datetime
import logging
import re
import shutil
from pathlib import Path

from proquest_dl import proquest_dl
from proquest_dl.Issue import Issue
from proquest_dl.ProQuestWebScraper import (CaptchaRequired, IssueNotFound,
                                            known_publication_ids)

ECONOMIST = known_publication_ids["The Economist"]
# Name that proquest-dl gives to the PDF of an issue (see Issue.build_final_fp)
ISSUE_FILE = re.compile(r"TheEconomist-(\d{4}-\d{2}-\d{2})\.pdf")


def last_saturday(today):
    """The date of the latest issue: the Saturday of the week (today if Saturday)."""
    return today - datetime.timedelta(days=(today.weekday() - 5) % 7)


def prune(outdir, keep_days, today):
    """Delete the issues in outdir dated more than keep_days days before today."""
    for fp in Path(outdir).glob("TheEconomist-*.pdf"):
        found = ISSUE_FILE.fullmatch(fp.name)
        if found and (today - datetime.date.fromisoformat(found.group(1))).days > keep_days:
            fp.unlink()
            logging.info(f"Deleted {fp}")


def main(argv=None, today=None):
    proquest_dl.setup()
    parser = proquest_dl.build_parser(publication_id=ECONOMIST, prog="economist-dl")
    parser.add_argument('--new-issue', action='store_true',
                        help=("Download the issue of the last Saturday, unless "
                              "it is already in --outdir; when ProQuest does not "
                              "have it yet, stop without an error (for a daily "
                              "scheduled run)"))
    parser.add_argument('--keep-days', type=int, metavar="N", default=None,
                        help="First delete the issues in --outdir dated over N days ago")
    args = parser.parse_args(argv)
    args.publication_id = ECONOMIST
    today = today or datetime.date.today()

    if args.keep_days is not None:
        prune(args.outdir, args.keep_days, today)

    if args.new_issue:
        if args.issue_date:
            parser.error("--new-issue chooses the issue date by itself")
        args.issue_date = last_saturday(today)
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
