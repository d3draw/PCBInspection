# Example borderline assets

These two `meta.json` files demonstrate the schema in `../SCHEMA.md`. They are
**not real captured data** and are not used by training.

The matching `001.png` / `002.png` images are intentionally absent — once the
optical setup (조명/지그/golden 보드) is locked the operator should:

1. Capture the actual borderline OK / NG samples for `cold_solder`,
2. Drop the PNGs into `../cold_solder/accept/` and `../cold_solder/reject/`,
3. Copy these JSONs alongside (with `image.file` updated) and edit fields,
4. Delete this `_example/` directory.

Repeat for every defect class. INSPECTION_CRITERIA.md §0 gates labeling on
each class having ≥ 5 accept and ≥ 5 reject entries.
