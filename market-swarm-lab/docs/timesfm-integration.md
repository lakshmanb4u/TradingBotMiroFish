# TimesFM 2.5 integration

## Model

TimesFM 2.5 (200M parameters, PyTorch) from Google Research.
Source: https://github.com/google-research/timesfm
Checkpoint: google/timesfm-2.5-200m-pytorch (Hugging Face)

## Operating modes

### timesfm_2p5_pytorch

Enabled when `ENABLE_TIMESFM=true` and the `timesfm` + `torch` packages are installed.
The model is loaded lazily on first request, compiled with quantile head, and called
via `model.forecast(horizon, inputs)`.

Quantile output mapping:
- quantile_forecast shape: (n_series, horizon, 10)
- columns: [mean, q10, q20, q30, q40, q50, q60, q70, q80, q90]
- p10 = column 1, p50 = column 5, p90 = column 9

### local_fallback

Used when TimesFM is not installed or `ENABLE_TIMESFM=false` (default).
Computes a linear extrapolation from the recent trend plus 1.28σ bands.
Always returns the full output schema.

### timesfm_runtime_error_fallback

Used if TimesFM loads successfully but raises during inference.
Falls back to local_fallback and includes `fallback_reason` in the response.

## Enabling TimesFM locally

```bash
# install timesfm and torch (requires Python 3.11 or 3.12)
pip install timesfm torch

# then set env var and run
ENABLE_TIMESFM=true PYTHONPATH=. uvicorn services.forecasting.app:app --port 8002
```

## Enabling TimesFM via Docker

```bash
# in .env
ENABLE_TIMESFM=true
INSTALL_TIMESFM=true

make run
```

The Dockerfile installs timesfm + torch only when `INSTALL_TIMESFM=true` is passed as a build arg.
Note: the model download (~800MB) happens at first request, not at build time.

## API

```
POST http://localhost:8002/forecast
Content-Type: application/json

{
  "ticker": "NVDA",
  "series": {
    "close":  [907.6, 916.3, 924.9, 931.7, 939.6, 949.8, 958.4, 966.7],
    "volume": [48400000, 45100000, 43750000, 46200000, 47050000, 49840000, 52100000, 53800000],
    "vwap":   [903.4, 912.1, 920.5, 928.2, 936.0, 946.3, 955.7, 963.9],
    "rsi":    [52.1, 54.3, 56.8, 58.1, 60.4, 63.2, 65.7, 67.9]
  },
  "horizon": 5
}
```

Response:
```json
{
  "ticker": "NVDA",
  "provider_mode": "local_fallback",
  "horizon": 5,
  "forecast": [975.45, 984.2, 992.95, 1001.7, 1010.45],
  "quantiles": {
    "p10": [949.05, 957.8, 966.55, 975.3, 984.05],
    "p50": [975.45, 984.2, 992.95, 1001.7, 1010.45],
    "p90": [1001.85, 1010.6, 1019.35, 1028.1, 1036.85]
  },
  "direction": "up",
  "confidence": 0.862
}
```

## Covariate notes

TimesFM 2.5 supports covariates via XReg.
Currently the API passes `close` as the primary series.
`volume`, `vwap`, and `rsi` are accepted in the request and available for XReg extension.
