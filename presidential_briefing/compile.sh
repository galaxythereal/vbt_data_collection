#!/usr/bin/env bash
# Compile the briefing. Run from this directory.
set -e
cd "$(dirname "$0")"

if ! command -v pdflatex >/dev/null 2>&1; then
  echo "pdflatex not found. Install texlive-latex-extra and texlive-fonts-extra."
  exit 1
fi

pdflatex -interaction=nonstopmode -halt-on-error briefing.tex >/dev/null
pdflatex -interaction=nonstopmode -halt-on-error briefing.tex >/dev/null

# Clean intermediates
rm -f briefing.aux briefing.log briefing.out

echo "Built: briefing.pdf"
