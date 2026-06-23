import sqlite3

path = r"D:\Users\dylix\Downloads\Mobile Atlas Creator 2.3.3\atlases\mytiles.mbtiles"

conn = sqlite3.connect(path)
cur = conn.cursor()

print("Distinct zoom levels:")
print(cur.execute("SELECT DISTINCT zoom_level FROM tiles").fetchall())

print("\nSample tile rows:")
rows = cur.execute("SELECT zoom_level, tile_column, tile_row, length(tile_data) FROM tiles LIMIT 20").fetchall()
for r in rows:
    print(r)

conn.close()
