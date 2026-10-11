# The sandbox a Bit works in: Python plus the everyday tools for documents, images, data and the
# web, so a Bit can get to work without installing basics first. ZENO builds it on first start
# (SANDBOX_IMAGE=auto) and tags it by this file's hash, so editing it triggers a rebuild.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git jq unzip zip xz-utils file less \
        poppler-utils tesseract-ocr ffmpeg sqlite3 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        requests httpx beautifulsoup4 lxml feedparser python-dateutil \
        pypdf pdfplumber pillow pytesseract python-docx openpyxl \
        numpy pandas matplotlib
