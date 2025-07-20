import sqlite3

conn = sqlite3.connect("precheck.db")
cursor = conn.cursor()

# 所有需要添加的新字段
columns_to_add = [
    "pr_id TEXT",
    "title TEXT",
    "softwareRelease TEXT",
    "softwareBuild TEXT",
    "attachmentIds TEXT",
    "groupIncharge TEXT",
    "identification TEXT",
    "explanation TEXT",
    "subSystem TEXT"
]

# 添加每个字段（跳过已存在的）
for column in columns_to_add:
    try:
        cursor.execute(f"ALTER TABLE precheck_records ADD COLUMN {column}")
        print(f"✅ Added column: {column}")
    except sqlite3.OperationalError as e:
        print(f"⚠️ Skipped {column} (maybe already exists): {e}")

conn.commit()
conn.close()
print("✅ Database structure updated successfully.")
