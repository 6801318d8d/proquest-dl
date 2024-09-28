# proquest-dl

## Introduction

Download magazines from ProQuest.

You can download any magazine, although this has been tested on "The Economist" and "MIT Technology Review".

For the former two magazines, covers are also downloaded from their respective websites (ProQuest doesn't provide covers).

The pages of the magazine are put in the correct order in the PDF file (that is, page 5 will be page 5 in the resulting PDF file, and so on).

Pages unavailable on ProQuest (e.g. because they consist of advertisements) are left blank.

Also, has a bookmark for each article is inserted in the resulting PDF.

This builds up a PDF issue of the magazine in question.

1. You need a valid subscription to ProQuest, either directly or through your university.
1. This software will run Firefox with a profile of your choice. To find out your Firefox profile, from Firefox click on hamburger menu -> Help -> More troubleshooting information -> Profile directory.

    This software will give you time to login in Proquest with your credentials.

## Requirements

You will need `qpdf` and `pandoc` in your PATH.

## Run the software

1. Install the software in a virtual environment and run it. For example:

    ```
    git clone https://github.com/6801318d8d/proquest-dl
    cd proquest-dl
    python -m pip install .
    proquest-dl 35850 --data-dir "./data" --firefox-profile-path "/home/USERNAME/.mozilla/firefox/PROFILE"
    ```
