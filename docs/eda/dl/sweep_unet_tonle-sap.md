# Sweep: unet on tonle-sap

Metric = agreement with the physics-rule weak labels on the eastern 30 % hold-out, NOT accuracy against ground truth (there is none).

Wall s = wall time of the whole subprocess (load + train + inference), runs sharing the GPU in parallel. Total sweep wall time: 525 s.

| rank | version | params | wall s | final train CE | IoU water | IoU flooded-veg | IoU other | mean IoU | not-sure -> water / fl-veg / other (east) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | no_weights | 482,915 | 210 | 0.0052 | 0.999 | 0.989 | 0.999 | **0.996** | 0.16 / 0.17 / 0.67 |
| 2 | base16_d2 | 117,539 | 176 | 0.0071 | 0.998 | 0.988 | 0.999 | **0.995** | 0.15 / 0.23 / 0.62 |
| 3 | base16_d3 | 482,915 | 197 | 0.0055 | 0.999 | 0.981 | 0.999 | **0.993** | 0.14 / 0.19 / 0.67 |
| 4 | base32_d3 | 1,927,363 | 391 | 0.0040 | 0.998 | 0.981 | 0.999 | **0.993** | 0.14 / 0.22 / 0.64 |
| 5 | no_ref | 482,627 | 209 | 0.1027 | 0.999 | 0.111 | 0.858 | **0.656** | 0.09 / 0.11 / 0.80 |
| 6 | base32_d2_noref | 466,947 | 141 | 0.0976 | 1.000 | 0.093 | 0.816 | **0.636** | 0.08 / 0.14 / 0.78 |
