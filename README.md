# proquest-dl

## Introduction

Download magazines from ProQuest.

You can download any magazine, although this has been tested on "The Economist" and "MIT Technology Review".

For The Economist, the cover is also downloaded from its website (ProQuest doesn't provide covers).

The pages of the magazine are put in the correct order in the PDF file (that is, page 5 will be page 5 in the resulting PDF file, and so on).

Pages unavailable on ProQuest (e.g. because they consist of advertisements) are left blank.

Also, a bookmark for each article is inserted in the resulting PDF.

This builds up a PDF issue of the magazine in question.

1. You need a valid subscription to ProQuest, either directly or through your university.
1. This software runs Firefox with its own profile (by default `~/.proquest-dl/firefox-profile`), used in place: log in to ProQuest in the browser window the first time, and the login is kept for the next runs. The download starts by itself as soon as the browser shows the issue page of the publication; if ProQuest does not go back to it after the login, open it again in the first tab.

    If ProQuest shows a captcha, solve it in the browser window: the download goes on by itself, and the pauses between pages double for the rest of the run.

## Requirements

You will need in your PATH:

- `qpdf`;
- Ghostscript (`gs`, or `gswin64c` on Windows), to compress the final PDF;
- ImageMagick (`magick`), for the cover of The Economist;
- `pandoc` with a LaTeX engine, only for magazines without a table of contents on ProQuest.

Selenium finds or downloads `geckodriver` by itself.

## Run the software

Install the software with [uv](https://docs.astral.sh/uv/) and run it. For example:

```
git clone https://github.com/6801318d8d/economist-proquest-dl
cd economist-proquest-dl
uv sync
uv run proquest-dl 35850
```

### The Economist

`economist-dl` downloads The Economist and takes the same options as `proquest-dl`, without the publication ID:

```
uv run economist-dl                          # the latest issue
uv run economist-dl --issue-date 2026-11-14  # the issue of that Saturday
```

### Useful options

- `--issue-date YYYY-MM-DD`: the issue to download, by the date ProQuest shows for it (for monthly magazines, the first day of the month). Default: the latest issue.
- `--outdir DIR`: where to save the PDF (default: the current folder).
- `--continue-download`: resume an interrupted download from the temporary folder (`--tmpdir`, default `./proquest-dl-temp`).
- `--pause MIN MAX`: random pause in seconds after each page load (default 2 to 5).
- `--exclude-section SECTION`: skip the articles of a section (e.g. `"Special report"`); it costs one more page load per article.
- `--firefox-profile-path DIR`: another Firefox profile, used in place; no other Firefox may be using it.

Run `uv run proquest-dl --help` for all the options.

## Tests

```
uv run pytest
```

The tests check the PDF processing offline (they need `qpdf` and `magick`), not the download from ProQuest.
