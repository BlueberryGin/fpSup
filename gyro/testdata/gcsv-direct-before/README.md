These are the pre-optimization GCSV formatter fixtures captured on 2026-10-04.
`gcsv_rows.S` is the source snapshot; the GCSV and Base `.bin` files and symbol
maps were assembled before the direct-output change. `test_gcsv_direct.py` runs
both versions under ARM emulation and compares rows byte for byte. The blobs
are test inputs, not card files.
