#!/usr/bin/env python3

import dataclasses
import datetime
import json
import re
from dataclasses import dataclass, field

from proquest_dl.Article import Article


@dataclass
class Issue:
    publication_id: str = ""
    publication_name: str = ""
    articles: list[Article] = field(default_factory=list)
    page_size: tuple[float, float] = (0, 0)
    toc: bool = False
    date: datetime.date | None = None
    # the issue as ProQuest names it, e.g. "Nov/Dec 2026; Vol. 129 (6)"
    label: str = ""

    def build_final_fp(self):
        """
        Build final file path and check we have not downloaded this issue yet
        """
        ts = self.date.strftime("%Y-%m-%d")
        pnamecl = re.sub(r"\s+", "", self.publication_name)
        return pnamecl + "-" + ts + ".pdf"

    def save(self, fp):
        """Save to a JSON file, to resume an interrupted download."""
        data = dataclasses.asdict(self)
        data["date"] = self.date.isoformat() if self.date else None
        fp.write_text(json.dumps(data, indent=4, sort_keys=True,
                                 ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, fp):
        data = json.loads(fp.read_text(encoding="utf-8"))
        if data["date"]:
            data["date"] = datetime.date.fromisoformat(data["date"])
        data["articles"] = [Article(**a) for a in data["articles"]]
        data["page_size"] = tuple(data["page_size"])
        return cls(**data)
