#!/bin/bash
# Build script for PhD Thesis

set -e

echo "=== Building PhD Thesis ==="

# Create output directory
mkdir -p build

# Compile PDF
echo "Compiling LaTeX..."
pdflatex -interaction=nonstopmode thesis_main.tex
bibtex thesis_main.aux
pdflatex -interaction=nonstopmode thesis_main.tex
pdflatex -interaction=nonstopmode thesis_main.tex

# Move to output directory
mv thesis_main.pdf build/PhD_Thesis_$(date +%Y%m%d).pdf

echo "=== Build Complete ==="
echo "PDF located at: build/PhD_Thesis_$(date +%Y%m%d).pdf"