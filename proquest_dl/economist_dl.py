#!/usr/bin/env python3
"""economist-dl: download an issue of The Economist from ProQuest.

The same options as proquest-dl, without the publication ID. Examples:

    economist-dl                                   # the latest issue
    economist-dl --issue-date 2026-11-14           # the issue of that Saturday
    economist-dl --outdir ~/magazines/economist
"""

import sys

from proquest_dl import proquest_dl
from proquest_dl.ProQuestWebScraper import known_publication_ids


def main():
    proquest_dl.main(sys.argv[1:],
                     publication_id=known_publication_ids["The Economist"],
                     prog="economist-dl")


if __name__ == "__main__":
    main()
