#!/usr/bin/env python3

from dataclasses import dataclass


@dataclass
class Article:
    title: str
    pages: list[int]
    pdfurl: str
    is_toc: bool
