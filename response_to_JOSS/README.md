# JOSS publication archive

This directory preserves the 2025 AirFogSim submission, reviewer responses,
validation scripts, and recorded comparison results. The published project is now
named AeroAgentSim; the current package, installation instructions, and workbench
are described in the [repository README](../README.md).

- [Current publication source](../paper.md) and [bibliography](../paper.bib)
- [Reviewer responses](JOSS_response.md)
- [FogNetSim++ comparison notes](comp_fognetpp_airfogsim.md)
- [Published paper](https://doi.org/10.21105/joss.08267)

The local `paper.md` and `paper.bib` files are historical snapshots. The root
publication source is used by the PDF workflow. Screenshots and example snippets
in these materials describe the publication-era software, not the current
workbench UI.

The scripts and CSV files are research artifacts, not part of the package runtime
or automated test suite. Plotting requires `matplotlib`; the full comparison also
requires `scipy`. Run comparison scripts from this directory because they read
`user_best_ap_sinr_data.csv` using a relative path. Use a separate output directory
or copy if you need to preserve the recorded results while rerunning experiments.

`zenodo_archiver.py` is a manual release-archiving utility. It sends a release
payload to Zenodo and is not invoked by the simulator, package build, or tests.
