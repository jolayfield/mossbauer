# Method-recommendation rationale (mossbauer-dft)

Companion to `method_rules.yaml`. Evidence tags refer to the DOIs in that file. Status: draft for review; calibration constants other than McWilliams 2017 are unverified (see `mossbauer_calibrations.yaml`).

## Core recommendation
- **δ:** B3LYP with CP(PPP) on Fe and def2-TZVP elsewhere (ORCA, Fe grid accuracy 7, no RI), using a calibration line fitted for exactly that protocol. B3LYP is the best or tied-best functional for isomer shifts across Bochevarov 2010 (MUE 0.03 mm/s), Pápai & Vankó 2013 (MAE 0.05-0.06 mm/s, 66 complexes), Comas-Vilà & Salvador 2021 (MAE 0.055, 103 signals) and Gallenkamp 2021 (TPSSh/B3LYP/PBE0 about equal).
- **|ΔEQ|:** B3LYP or TPSSh. Pápai's class-based recipe (TPSSh+COSMO default; B3LYP for S=2 Fe(II) and S=3/2 Fe(III); COSMO-RPBE for π-bonded, square-planar S=1 and distorted open-shell cases) reaches MAE 0.12 mm/s on 66 complexes. Plain B3LYP gives MAE 0.24-0.25 there; McWilliams report 0.27-0.30 for low-valent diketiminates.
- **Basis:** CP(PPP) and def2-TZVP all-atom give near-identical δ statistics for the low-valent set (MAE 0.040 vs 0.038; McWilliams), def2-TZVP being 1.46x cheaper. CP(PPP) is kept as default because the project workflow already uses it.
- **Solvation:** COSMO lowers δ MAE by 0.01-0.02 mm/s (Pápai) but PCM (ε=80) did not help ions in Comas-Vilà; treat as optional and consistent with the calibration.

## Fe(I) specific (project priority)
1. MN15 (44% exact exchange) overestimates |ΔEQ| of Fe(I) in project benchmarks, while B3LYP and TPSSh agree well with experiment. MN15 is therefore reserved for geometries and BS energetics; B3LYP and/or TPSSh are used for properties (single points on MN15 geometries). This is a project observation (n=few compounds), not a literature result.
2. The only published calibration that tabulates Fe(I) species is McWilliams 2017 (9 training compounds; 11 formal Fe(+1) entries across training+test (plus two formal Fe(0)), including three-coordinate Fe(I) hydrides and bis-NHC Fe(I)). Its δ constants are verified; apply with care because the training geometry was BP86-D3BJ/ZORA-def2-TZVP, not MN15/def2-SVP.
3. Radical-ligand or otherwise ambiguous cases: use BS-UKS, compare the BS and high-spin solutions, and judge against experimental spectra (ideally low-temperature and variable-field).

## Known gaps
- Neese 2002, Römelt 2009, Sinnecker 2005, Kurian/Filatov, Hedegård 2014: full text unavailable; their numeric constants are not encoded as verified.
- Bochevarov (SI) and Pápai (figures/SI) fit constants were not retrievable; the tool should refit lines from `mossbauer_training_set.csv` with locally computed ρ0.
- No calibration was found that was fitted with MN15, with ZORA/X2C for Fe(I), or with BS radical-ligand Fe(I) references.
