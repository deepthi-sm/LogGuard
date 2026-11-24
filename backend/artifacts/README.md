# Model artifacts

This directory is intentionally empty in the repository. The artifacts
are **generated locally** by running the training pipeline; they are
not version-controlled.

## What goes here after training

| File | Produced by | Required at runtime by |
| --- | --- | --- |
| `transformer.pt` | `training/train_transformer.py` | `ml/detector.py` |
| `autoencoder.pt` | `training/train_autoencoder.py` | `ml/detector.py` |
| `confidence_scorer.pt` | `training/calibrate.py` | `ml/detector.py` |
| `thresholds.json` | `training/calibrate.py` | `ml/ensemble.py`, `ml/detector.py` |
| `drain3_state.bin` | `ingestion/parser.py` (auto-saved) | `ingestion/runner.py` |
| `faiss.index` | `training/build_faiss.py` | `rag/faiss_client.py` |
| `incidents.jsonl` | `training/build_faiss.py` | `rag/faiss_client.py` |
| `transformer_metrics.json` | `training/train_transformer.py` | reporting |
| `autoencoder_metrics.json` | `training/train_autoencoder.py` | reporting |

## How to generate

Once datasets are in place under `backend/training/data/` (see that
folder's README for download links), run:

```bash
cd backend
python -m training.run_full_pipeline --dataset openstack
```

The pipeline produces every artifact listed above and writes them
here. From a fresh clone the runner / RAG worker will not start
until these files exist — `ml/detector.py` and `rag/faiss_client.py`
both raise on missing artifacts at boot.

## Why no binaries in the repo

- `embeddings_openstack.npy` is ~6.4 GB at full scale — over GitHub
  Releases' hard limit.
- The model `.pt` files, FAISS index, and incident corpus are all
  **derivatives** of the training dataset and the source code. Anyone
  with the data and the code can rebuild them; checking them in would
  duplicate work and bloat the repo.
- Keeping artifacts out of git also means a clone never accidentally
  ships stale weights from a previous training run.
