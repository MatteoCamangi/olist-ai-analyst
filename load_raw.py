import duckdb
from pathlib import Path

con = duckdb.connect("warehouse.duckdb")
con.execute("CREATE SCHEMA IF NOT EXISTS raw")

for f in sorted(Path("data/raw").glob("*.csv")):
    name = f.stem.replace("olist_", "").replace("_dataset", "")
    con.execute(f"CREATE OR REPLACE TABLE raw.{name} AS SELECT * FROM read_csv_auto('{f}')")
    n = con.execute(f"SELECT COUNT(*) FROM raw.{name}").fetchone()[0]
    print(f"raw.{name}: {n:,} rows")
