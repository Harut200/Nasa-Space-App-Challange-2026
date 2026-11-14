# Sweep: ae on tonle-sap

Metric = reconstruction error on eastern-30 % pixels (self-supervised), NOT accuracy; lower only means the embedding keeps more of the series. k does not change it.

Wall s = wall time of the whole subprocess (load + train + inference), runs sharing the GPU in parallel. Total sweep wall time: 80 s.

| rank | version | params | wall s | final train MSE | held-out recon MSE | k | non-empty clusters |
|---|---|---|---|---|---|---|---|
| 1 | gru_e4_k6 | 18,966 | 29 | 0.00595 | **0.00598** | 6 | 6 |
| 2 | gru_e16_k8 | 20,706 | 34 | 0.00594 | **0.00604** | 8 | 8 |
| 3 | gru_e8_k8 | 19,546 | 43 | 0.00619 | **0.00635** | 8 | 8 |
| 4 | gru_e8_k6 | 19,546 | 36 | 0.00619 | **0.00635** | 6 | 6 |
| 5 | conv_e8_k8 | 11,434 | 32 | 0.00627 | **0.00658** | 8 | 8 |
| 6 | lstm_e8_k8 | 24,826 | 44 | 0.00647 | **0.00680** | 8 | 8 |
