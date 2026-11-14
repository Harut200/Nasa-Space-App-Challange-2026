# Sweep: U-Net v2 (labels from the math detector)

Score = mean of (Tonle Sap east-30 % held-out mean IoU, 3 classes) and (Sevan east-third held-out mean IoU over water and other land; the math detector finds ~no flooded vegetation at Sevan). It is AGREEMENT WITH ANOTHER ALGORITHM (the math detector), not accuracy. Flooded-veg IoU on Sevan is NaN/0 when neither has any. Wall s = whole subprocess with 3 runs sharing the GPU. Total sweep wall time 252 s. Best -> `unet_v2_best.txt` (`v2_t_d3`).

| rank | version | params | wall s | train s | Tonle Sap held-out IoU water / fl-veg / other | Sevan held-out IoU water / fl-veg / other | score | Sevan largest body MAE vs 1242 km² |
|---|---|---|---|---|---|---|---|---|
| 1 | v2_t_d3 | 483,347 | 119 | 105 | 0.822 / 0.332 / 0.996 | 0.999 / nan / 1.000 | **0.858** | 12 |
| 2 | v2_t_d2_tsonly | 117,971 | 119 | 101 | 0.817 / 0.313 / 0.996 | 0.999 / nan / 1.000 | **0.854** | 5 |
| 3 | v2_t_d2 | 117,971 | 112 | 97 | 0.797 / 0.247 / 0.995 | 0.999 / 0.000 / 1.000 | **0.839** | 4 |
| 4 | v2_not_d2_aug | 117,539 | 134 | 119 | 0.848 / 0.185 / 0.995 | 0.997 / nan / 0.999 | **0.837** | 17 |
| 5 | v2_t_d2_aug | 117,971 | 140 | 122 | 0.845 / 0.158 / 0.994 | 0.999 / nan / 1.000 | **0.832** | 11 |
| 6 | v2_not_d2 | 117,539 | 107 | 92 | 0.665 / 0.339 / 0.992 | 0.996 / nan / 0.999 | **0.832** | 4 |
