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
2. If your university requires you to login to ProQuest before being able to download articles, as it is most likely, you can either:
	1. Pause the script, login through your university's portal, then resume the script.
	2. Implement the login process of your university by yourself, as I'm not going to implement the login process of a particular university.

## Requirements

Other than the python packages in `requirements.txt`, you will need `qpdf` and `pandoc` in your PATH.

## Run the software

1. Clone the repository

    ```
    git clone https://github.com/6801318d8d/proquest-dl
    cd proquest-dl
    ```

2. Create a virtual environment and install required Python packages.

    For example, using pyenv:

    ```
    pyenv virtualenv 3.12 proquest-dl
    pyenv local proquest-dl
    python -m pip install -r requirements.txt
    ```
3. Run the software

    ```
    cd src
    ./proquest-dl.py
    ```
