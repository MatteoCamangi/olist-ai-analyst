import sys
from pathlib import Path
import duckdb
import pandas as pd

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 50)
pd.set_option("display.max_rows", 500)

arg = sys.argv[1]
is_file = arg.endswith(".sql")
query = Path(arg).read_text() if is_file else arg
con = duckdb.connect("warehouse.duckdb", read_only=True)
df = con.execute(query).fetchdf()
print(df.to_string() if is_file else df.head(50).to_string())