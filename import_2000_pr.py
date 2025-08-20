#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import os
import csv
import sqlite3
import argparse

DB_PATH = "precheck_records.db"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS precheck_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    description TEXT,
    pr_id TEXT,
    title TEXT,
    softwareRelease TEXT,
    softwareBuild TEXT,
    attachmentIds TEXT,
    groupIncharge TEXT,
    identification TEXT,
    resolution TEXT,
    subSystem TEXT,
    root_cause TEXT,
    explanation TEXT,
    category TEXT
);
"""

CREATE_INDEX_SQL = "CREATE UNIQUE INDEX IF NOT EXISTS ux_precheck_pr_id ON precheck_records(pr_id);"

UPSERT_SQL = """
INSERT INTO precheck_records (
    filename, description, pr_id, title, softwareRelease, softwareBuild,
    attachmentIds, groupIncharge, identification, resolution, subSystem,
    root_cause, explanation, category
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(pr_id) DO UPDATE SET
    filename=excluded.filename,
    description=excluded.description,
    title=excluded.title,
    softwareRelease=excluded.softwareRelease,
    softwareBuild=excluded.softwareBuild,
    attachmentIds=excluded.attachmentIds,
    groupIncharge=excluded.groupIncharge,
    identification=excluded.identification,
    resolution=excluded.resolution,
    subSystem=excluded.subSystem,
    root_cause=excluded.root_cause,
    -- explanation 如新值为空则保留旧值
    explanation=COALESCE(NULLIF(excluded.explanation, ''), precheck_records.explanation),
    -- category 如新值为空则保留旧值（你可以反复导）
    category=COALESCE(NULLIF(excluded.category, ''), precheck_records.category);
"""

def norm(x: object) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s

def detect_pr_id(row: dict) -> str:
    for key in ("pr_id", "PR_ID", "id", "Id", "ID"):
        if key in row and str(row[key]).strip():
            return str(row[key]).strip()
    return ""

def map_row(row: dict, fill_non_cnn: bool) -> dict:
    pr_id = detect_pr_id(row)

    title   = row.get("title") or row.get("Title") or ""
    desc    = row.get("description") or row.get("Description") or ""
    swr     = row.get("softwareRelease") or row.get("SoftwareRelease") or row.get("software_release") or ""
    swb     = row.get("softwareBuild") or row.get("SoftwareBuild") or row.get("software_build") or ""
    attach  = row.get("attachmentIds") or row.get("AttachmentIds") or row.get("attachments") or ""
    gic     = row.get("groupIncharge") or row.get("GroupIncharge") or row.get("gic") or ""
    ident   = row.get("identification") or ""
    resol   = row.get("resolution") or ""
    subs    = row.get("subSystem") or row.get("subsystem") or row.get("component") or ""
    rootc   = row.get("rootCause") or row.get("root_cause") or ""
    expl    = row.get("explanation") or ""  # 可放一些补充说明/原因字段
    state   = (row.get("state") or "").strip()

    category = ""
    if state == "Correction Not Needed":
        category = "Precheck with CNN PR"
    elif fill_non_cnn:
        category = "Precheck with valid PR" if pr_id else "Precheck without PR"

    return {
        "filename": pr_id or f"bulk_{state or 'no_state'}",
        "description": norm(desc),
        "pr_id": norm(pr_id),
        "title": norm(title),
        "softwareRelease": norm(swr),
        "softwareBuild": norm(swb),
        "attachmentIds": norm(attach),
        "groupIncharge": norm(gic),
        "identification": norm(ident),
        "resolution": norm(resol),
        "subSystem": norm(subs),
        "root_cause": norm(rootc),
        "explanation": norm(expl),
        "category": norm(category)
    }

def ensure_schema(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute(TABLE_SQL)
    c.execute(CREATE_INDEX_SQL)
    conn.commit()

def import_csv(csv_path: str, fill_non_cnn: bool) -> int:
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    n = 0
    with open(csv_path, newline='', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mapped = map_row(row, fill_non_cnn=fill_non_cnn)
            vals = (
                mapped["filename"], mapped["description"], mapped["pr_id"], mapped["title"],
                mapped["softwareRelease"], mapped["softwareBuild"], mapped["attachmentIds"],
                mapped["groupIncharge"], mapped["identification"], mapped["resolution"],
                mapped["subSystem"], mapped["root_cause"], mapped["explanation"], mapped["category"]
            )
            # 没有 pr_id 时也允许插入，但无法去重；建议最好有 pr_id
            conn.execute(UPSERT_SQL, vals)
            n += 1
        conn.commit()
    conn.close()
    return n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="ALL 2000 CSV 文件路径，如：pr_first_2000_all (1).csv")
    ap.add_argument("--fill-non-cnn", action="store_true", help="为非 CNN 也自动分类（有 PR→valid，无 PR→without）")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit(f"CSV 不存在：{args.csv}")

    imported = import_csv(args.csv, fill_non_cnn=args.fill_non_cnn)
    print(f"✅ 导入完成：{imported} 行写入 precheck_records.db")
    if not args.fill_non_cnn:
        print("ℹ️ 当前只为 CNN 自动填写 Category，其余行 Category 留空（符合你的默认要求）。")
        print("   若想为非 CNN 也分类，请加 --fill-non-cnn 重新导入。")

if __name__ == "__main__":
    main()
