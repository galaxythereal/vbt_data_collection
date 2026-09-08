# VBT Data Collection PhD Thesis

This directory contains the complete PhD graduation thesis documenting the Velocity-Based Training inertial measurement system, from embedded sensor design through ASIC acceleration.

## Directory Structure

```
thesis/
├── thesis_main.tex          # Main document file
├── main.tex                 # Alternative compact version
├── references.bib           # Bibliography
├── build.sh                 # Build script
├── README.md               # This file
├── chapters/
│   ├── 01_introduction.tex       # Chapter 1: Introduction & Motivation
│   ├── 02_background.tex         # Chapter 2: Background & Literature Review
│   ├── 03_architecture.tex       # Chapter 3: System Architecture
│   ├── 04_hardware.tex           # Chapter 4: Hardware Design
│   ├── 05_firmware.tex           # Chapter 5: Embedded Firmware
│   ├── 06_host_software.tex      # Chapter 6: Host Software Architecture
│   ├── 07_pipeline.tex           # Chapter 7: Signal Processing Pipeline
│   ├── 08_segmentation.tex       # Chapter 8: Rep Segmentation Algorithm
│   ├── 09_validation.tex         # Chapter 9: Validation & Results
│   ├── 10_fpga.tex               # Chapter 10: FPGA Implementation
│   ├── 11_asic.tex               # Chapter 11: ASIC Architecture
│   ├── 12_conclusion.tex         # Chapter 12: Conclusion & Future Work
│   └── appendices.tex            # Appendices
└── figures/                    # (Placeholder for images)
    └── system_diagram.pdf
    └── validation_plots.pdf
```

## Thesis Overview

**Title:** A Real-Time Inertial Measurement System for Velocity-Based Training: From Embedded Sensor Fusion to ASIC Acceleration

**Length:** ~120 pages (estimated)

**Contents:**
- **Chapter 1:** Introduction to VBT, problem statement, research objectives
- **Chapter 2:** Background on VBT theory, MEMS sensors, attitude estimation algorithms
- **Chapter 3:** High-level system architecture and component selection
- **Chapter 4:** Hardware design including IMU configuration, sync circuitry, PCB layout
- **Chapter 5:** ESP32 firmware implementation (SPI, ESP-NOW, interrupts)
- **Chapter 6:** Host software architecture (C++17, real-time processing, GUI)
- **Chapter 7:** Signal processing pipeline (VQF filter, ZUPT, integration)
- **Chapter 8:** Real-time rep segmentation algorithm
- **Chapter 9:** Validation results (55 sessions, 622 reps, accuracy metrics)
- **Chapter 10:** FPGA implementation (Xilinx Artix-7, fixed-point, timing)
- **Chapter 11:** ASIC architecture (28nm, power analysis, yield)
- **Chapter 12:** Conclusion, limitations, future work

## Building the Thesis

### Prerequisites
- TeX Live (2020 or later) or MacTeX
- LaTeX compiler with packages: `amsmath`, `tikz`, `booktabs`, `natbib`, `siunitx`

### Build Command
```bash
cd thesis
pdflatex thesis_main.tex
bibtex thesis_main.aux
pdflatex thesis_main.tex
pdflatex thesis_main.tex
```

Or use the build script:
```bash
chmod +x build.sh
./build.sh
```

## Key Contributions Documented

1. **Complete Open-Source System:** Hardware schematics, firmware, host software
2. **Real-Time Algorithm:** Sub-50ms latency, sub-5cm accuracy IMU-only
3. **Validated Dataset:** 55 sessions, 622 annotated repetitions
4. **FPGA Implementation:** Artix-7 RTL with 3.3× power reduction
5. **ASIC Architecture:** 28nm design targeting 185mW power

## Dataset Reference

For citing the dataset:
```
Galaxy. (2026). VBT IMU Dataset: 55 Sessions, 622 Reps 
[Data set]. GitHub. https://github.com/[repo]/datasets
```

## Dissertation Defense Checklist

- [ ] Introduction chapter reviewed by supervisor
- [ ] Literature review complete and up-to-date
- [ ] All validation experiments completed
- [ ] FPGA/ASIC designs synthesized and timing-checked
- [ ] Acknowledgements finalized
- [ ] References cross-checked
- [ ] PDF proofread for typos
- [ ] Final approval from committee

## Contact

Questions about this thesis should be directed to [your email].

---

*This thesis represents 6+ months of research, 55 data collection sessions, and countless hours of debugging SPI interrupts.*