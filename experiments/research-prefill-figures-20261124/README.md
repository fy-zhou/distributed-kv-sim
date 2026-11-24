# RTX 4090 calibration figure

The SVG uses the archived idle H2D/D2H summaries and both 4096-token
prefill orderings. Run `python3 scripts/research_gpu_figures.py --out calibration.svg`
with Matplotlib installed. CUDA intervals include kernel or copy launch gaps.
