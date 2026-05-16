# PhD Thesis - Compilation Instructions

## Thesis: "A Real-Time Inertial Measurement System for Velocity-Based Training"
### From Embedded Sensor Fusion to ASIC Acceleration

---

## Current Status

All 12 chapters plus appendices have been created. The thesis contains approximately **100+ pages** of content covering:

1. **Introduction** - VBT fundamentals, problem statement, research objectives
2. **Background & Literature Review** - Sensor technologies, attitude estimation algorithms
3. **System Architecture** - High-level design decisions and component selection
4. **Hardware Design** - IMU configuration, synchronization circuitry, PCB design
5. **Embedded Firmware** - ESP32 SPI driver, ESP-NOW implementation
6. **Host Software Architecture** - C++17 application design
7. **Signal Processing Pipeline** - VQF filter, ZUPT, double integration
8. **Rep Segmentation Algorithm** - Dual-trigger boundary detection
9. **Validation & Results** - 55 sessions, 622 repetitions analyzed
10. **FPGA Implementation** - Xilinx Artix-7 RTL design
11. **ASIC Architecture** - 28nm CMOS microarchitecture
12. **Conclusion & Future Work** - Summary and research directions

---

## To Compile the PDF

You need a LaTeX distribution. Choose one:

### Option 1: Ubuntu/Debian
```bash
sudo apt-get update
sudo apt-get install texlive-full
cd thesis
pdflatex thesis_main.tex
bibtex thesis_main.aux
pdflatex thesis_main.tex
pdflatex thesis_main.tex
```

### Option 2: macOS
```bash
brew install --cask mactex
cd thesis
pdflatex thesis_main.tex
bibtex thesis_main.aux
pdflatex thesis_main.tex
pdflatex thesis_main.tex
```

### Option 3: Online (Overleaf)
1. Go to https://www.overleaf.com
2. Create new project
3. Upload all files from the `thesis/` directory
4. Click "Recompile"

---

## File Structure

```
thesis/
├── thesis_main.tex      <- Main document (compile this)
├── references.bib       <- Bibliography
├── build.sh             <- Build script
├── README.md
└── chapters/
    ├── 01_introduction.tex
    ├── 02_background.tex
    ├── 03_architecture.tex
    ├── 04_hardware.tex
    ├── 05_firmware.tex
    ├── 06_host_software.tex
    ├── 07_pipeline.tex
    ├── 08_segmentation.tex
    ├── 09_validation.tex
    ├── 10_fpga.tex
    ├── 11_asic.tex
    ├── 12_conclusion.tex
    └── appendices.tex
```

---

## Estimated Page Count

| Chapter | Pages |
|---------|-------|
| Title Page | 2 |
| Abstract | 1 |
| Acknowledgements | 1 |
| Table of Contents | 2 |
| List of Figures/Tables | 2 |
| Chapter 1 | 8-10 |
| Chapter 2 | 12-15 |
| Chapter 3 | 8-10 |
| Chapter 4 | 10-12 |
| Chapter 5 | 10-12 |
| Chapter 6 | 10-12 |
| Chapter 7 | 12-15 |
| Chapter 8 | 10-12 |
| Chapter 9 | 12-15 |
| Chapter 10 | 10-12 |
| Chapter 11 | 10-12 |
| Chapter 12 | 8-10 |
| References | 3-5 |
| Appendices | 5-8 |
| **Total** | **~150-180 pages** |

---

## Required LaTeX Packages

The document uses these packages (all included in `texlive-full`):
- `amsmath`, `amssymb` - Mathematics
- `tikz` - Diagrams and figures
- `booktabs` - Professional tables
- `natbib` - Citations
- `siunitx` - SI units
- `listings` - Code listings
- `geometry` - Page layout
- `hyperref` - PDF links
- `cleveref` - Smart references
- `algorithm`, `algpseudocode` - Algorithms

---

## Quick Test (Optional)

To verify the setup works, compile just the introduction:

```bash
cd thesis/chapters
pdflatex ../test_build.tex
```

---

## Notes for Defense

1. **Replace placeholder names**: Update `[Name]`, `[University Name]`, etc.
2. **Add figures**: Replace `[Insert figure]` placeholders with actual diagrams
3. **Verify references**: Check all citations in `references.bib`
4. **Update date**: Modify `\today` if needed

---

## Contact

If you encounter compilation errors:
1. Ensure you have `texlive-full` (not just `texlive` minimal)
2. Check that all `.tex` files are in the correct directories
3. Run `bibtex` after the first `pdflatex` pass

---

*Thesis compiled successfully when this message appears.*