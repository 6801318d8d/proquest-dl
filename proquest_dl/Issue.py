#!/usr/bin/env python3

from dataclasses import dataclass, field
from proquest_dl.Article import Article
import re


@dataclass
class Issue:
    publication_id: int = 0
    publication_name: str = ""
    articles: list[Article] = field(default_factory=list)
    page_size: tuple[float, float] = (0, 0)
    toc: bool = False

    def build_final_fp(self):
        """
        Build final file path and check we have not downloaded this issue yet
        """
        ts = self.date.strftime("%Y-%m-%d")
        pnamecl = re.sub(r"\s+", "", self.publication_name)
        return pnamecl + "-" + ts + ".pdf"
