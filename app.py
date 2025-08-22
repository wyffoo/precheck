# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path
import sqlite3, os
import nltk
import uuid
import time
import requests
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========= Your existing extraction module =========
from ai_extract import parse_eml, parse_msg, extract_description, extract_resolution, extract_image_text

# ========= NLTK Dependencies =========
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')

# ========= Flask Setup =========
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

UPLOAD_FOLDER = "uploads"
DB_PATH = "precheck_records.db"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

SUPPORTED_EXTENSIONS = {
    ".eml": parse_eml,
    ".msg": parse_msg,
    ".jpg": extract_image_text,
    ".jpeg": extract_image_text,
    ".png": extract_image_text,
    ".txt": lambda path: open(path, 'r', encoding='utf-8').read(),
}

# ========= PRONTO sync config (hardcoded) =========
PRONTO_USER = "wyifan"
PRONTO_PASS = "Wyywjh1018"
PRONTO_BASE = "https://pronto.ext.net.nokia.com/prontoapi/rest/api/latest"
PAGE_SIZE = 50
FA_WORKERS = 5
MAX_RETRIES = 3
SLEEP_BETWEEN_PAGES = 0.3

# ========= Database Initialization (with unique index) =========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
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
    """)
    # Deduplicate based on pr_id
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_precheck_pr_id ON precheck_records(pr_id);")
    conn.commit()
    conn.close()

# ========= Generic UPSERT SQL (dedup by pr_id) =========
UPSERT_SQL = """
INSERT INTO precheck_records (
    filename, description, pr_id, title, softwareRelease, softwareBuild,
    attachmentIds, groupIncharge, identification, resolution, subSystem,
    root_cause, explanation, category
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(pr_id) DO UPDATE SET
    filename           = COALESCE(NULLIF(excluded.filename, ''),           precheck_records.filename),
    description        = COALESCE(NULLIF(excluded.description, ''),        precheck_records.description),
    title              = COALESCE(NULLIF(excluded.title, ''),              precheck_records.title),
    softwareRelease    = COALESCE(NULLIF(excluded.softwareRelease, ''),    precheck_records.softwareRelease),
    softwareBuild      = COALESCE(NULLIF(excluded.softwareBuild, ''),      precheck_records.softwareBuild),
    attachmentIds      = COALESCE(NULLIF(excluded.attachmentIds, ''),      precheck_records.attachmentIds),
    groupIncharge      = COALESCE(NULLIF(excluded.groupIncharge, ''),      precheck_records.groupIncharge),
    identification     = COALESCE(NULLIF(excluded.identification, ''),     precheck_records.identification),
    resolution         = COALESCE(NULLIF(excluded.resolution, ''),         precheck_records.resolution),
    subSystem          = COALESCE(NULLIF(excluded.subSystem, ''),          precheck_records.subSystem),
    root_cause         = COALESCE(NULLIF(excluded.root_cause, ''),         precheck_records.root_cause),
    explanation        = COALESCE(NULLIF(excluded.explanation, ''),        precheck_records.explanation),
    category           = COALESCE(NULLIF(excluded.category, ''),           precheck_records.category)
"""

def save_to_database(filename, structured):
    def _none_if_blank(v):
        if v is None:
            return None
        s = str(v).strip()
        return None if s == "" else s

    pr_id_val = _none_if_blank(structured.get("pr_id"))

    conn = sqlite3.connect(DB_PATH)
    conn.execute(UPSERT_SQL, (
        filename,
        structured.get("description", ""),
        pr_id_val,
        structured.get("title", ""),
        structured.get("softwareRelease", ""),
        structured.get("softwareBuild", ""),
        structured.get("attachmentIds", ""),
        structured.get("groupIncharge", ""),
        structured.get("identification", ""),
        structured.get("resolution", ""),
        structured.get("subSystem", ""),
        structured.get("root_cause", ""),
        structured.get("explanation", ""),
        structured.get("category", "")
    ))
    conn.commit()
    conn.close()

# ========= /api/extract =========
@app.route("/api/extract", methods=["POST"])
def extract_from_uploaded_files():
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files received"}), 400

    all_texts = []
    main_filename = ""
    is_image_file = False

    for file in files:
        filename = secure_filename(file.filename)
        if not filename:
            continue
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(save_path)

        extracted_text = SUPPORTED_EXTENSIONS[ext](save_path)
        if extracted_text.strip():
            all_texts.append(f"--- {filename} ---\n" + extracted_text)

        if not main_filename and ext in [".eml", ".msg", ".txt"]:
            main_filename = filename

        if ext in [".jpg", ".jpeg", ".png"]:
            is_image_file = True

    if not all_texts:
        return jsonify({"error": "No valid content extracted"}), 400

    merged_text = "\n\n".join(all_texts)
    unique_id = uuid.uuid4().hex[:8]
    used_filename = main_filename or f"manual_entry_{unique_id}.eml"

    try:
        description_part = extract_description(merged_text, used_filename)
        resolution_part = extract_resolution(merged_text, used_filename)

        result = {**description_part, **resolution_part}
        result.update({
            "pr_id": None, "title": "", "softwareRelease": "",
            "softwareBuild": "", "attachmentIds": "", "groupIncharge": "",
            "identification": "", "explanation": "", "subSystem": "",
            "root_cause": "", "category": ""
        })

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ========= /api/records CRUD =========
@app.route("/api/records", methods=["GET"])
def get_records():
    search = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 20

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if search:
        query = f"""
            SELECT * FROM precheck_records
            WHERE LOWER(description) LIKE ? OR LOWER(title) LIKE ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?;
        """
        count_query = "SELECT COUNT(*) FROM precheck_records WHERE LOWER(description) LIKE ? OR LOWER(title) LIKE ?;"
        args = [f"%{search}%", f"%{search}%", per_page, (page - 1) * per_page]
        count_args = [f"%{search}%", f"%{search}%"]
    else:
        query = "SELECT * FROM precheck_records ORDER BY id DESC LIMIT ? OFFSET ?;"
        count_query = "SELECT COUNT(*) FROM precheck_records;"
        args = [per_page, (page - 1) * per_page]
        count_args = []

    c.execute(query, args)
    records = [dict(row) for row in c.fetchall()]

    c.execute(count_query, count_args)
    total = c.fetchone()[0]
    total_pages = max((total + per_page - 1) // per_page, 1)

    conn.close()
    return jsonify({
        "records": records,
        "totalPages": total_pages
    })

@app.route("/api/records", methods=["POST"])
def insert_record():
    try:
        data = request.json
        save_to_database(data.get("filename", "manual_entry.eml"), data)
        return get_records()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/records/<int:record_id>", methods=["PATCH"])
def patch_record(record_id):
    try:
        data = request.json
        fields = [
            "description", "pr_id", "title", "softwareRelease", "softwareBuild",
            "attachmentIds", "groupIncharge", "identification", "resolution",
            "subSystem", "root_cause", "explanation", "category"
        ]
        updates, values = [], []
        for field in fields:
            if field in data:
                updates.append(f"{field} = ?")
                values.append(data[field])
        if updates:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(f"UPDATE precheck_records SET {', '.join(updates)} WHERE id = ?", values + [record_id])
            conn.commit()
            conn.close()
        return get_records()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/records/<int:record_id>", methods=["DELETE"])
def delete_record(record_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM precheck_records WHERE id = ?", (record_id,))
        conn.commit()
        conn.close()
        return get_records()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ========= PRONTO Sync Helpers =========
def _safe_text(x):
    return str(x or "").replace("\n", " ").replace("\r", " ").strip()

def _get_session():
    s = requests.Session()
    s.auth = HTTPBasicAuth(PRONTO_USER, PRONTO_PASS)
    s.headers.update({"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
    return s

def _get_with_retries(session, url, expect_json=True):
    backoff = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=25, allow_redirects=True)
        except Exception as e:
            if attempt == MAX_RETRIES:
                return False, None, f"Request error: {e}"
            time.sleep(backoff); backoff *= 2; continue

        if r.status_code == 200:
            ct = (r.headers.get("Content-Type") or "").lower()
            if expect_json and "application/json" in ct:
                try:
                    return True, 200, r.json()
                except Exception as e:
                    if attempt == MAX_RETRIES:
                        return False, 200, f"JSON decode failed: {e}"
            elif not expect_json:
                return True, 200, r.text

        if 500 <= r.status_code < 600 and attempt < MAX_RETRIES:
            time.sleep(backoff); backoff *= 2; continue

        return False, r.status_code, (r.text[:300] if r.text else "")
    return False, None, "Unknown error"

def _fetch_fa_details(session, fa_id):
    empty = {'identification': '', 'resolution': '', 'subSystem': '', 'rootCause': '', 'internalAnalysisInfo': ''}
    if not fa_id:
        return empty
    ok, code, data = _get_with_retries(session, f"{PRONTO_BASE}/faultAnalysis/{fa_id}", expect_json=True)
    if ok and isinstance(data, dict):
        return {
            'identification': _safe_text(data.get('identification')),
            'resolution': _safe_text(data.get('resolution')),
            'subSystem': _safe_text(data.get('subSystem')),
            'rootCause': _safe_text(data.get('rootCause')),
            'internalAnalysisInfo': _safe_text(data.get('internalAnalysisInfo')),
        }
    return empty

def _fetch_recent_prs_with_fa(limit):
    session = _get_session()
    rows = []
    start_at = 0
    while len(rows) < limit:
        url = f"{PRONTO_BASE}/problemReport?startAt={start_at}&maxResults={PAGE_SIZE}"
        ok, code, data = _get_with_retries(session, url, expect_json=True)
        if not ok:
            start_at += PAGE_SIZE
            time.sleep(SLEEP_BETWEEN_PAGES)
            continue
        prs = data.get('values', [])
        if not prs:
            break

        fa_ids = [pr.get('faultAnalysisId') for pr in prs if pr.get('faultAnalysisId')]
        fa_map = {}
        if fa_ids:
            with ThreadPoolExecutor(max_workers=FA_WORKERS) as ex:
                fut2id = {ex.submit(_fetch_fa_details, session, fid): fid for fid in fa_ids}
                for fut in as_completed(fut2id):
                    fid = fut2id[fut]
                    try:
                        fa_map[fid] = fut.result()
                    except Exception:
                        fa_map[fid] = {'identification': '', 'resolution': '', 'subSystem': '', 'rootCause': '', 'internalAnalysisInfo': ''}

        for pr in prs:
            if len(rows) >= limit:
                break
            fa = fa_map.get(pr.get('faultAnalysisId'),
                            {'identification': '', 'resolution': '', 'subSystem': '', 'rootCause': '', 'internalAnalysisInfo': ''})
            rows.append({
                "pr_id": pr.get('id'),
                "title": _safe_text(pr.get('title')),
                "softwareRelease": _safe_text(pr.get('softwareRelease')),
                "softwareBuild": _safe_text(pr.get('softwareBuild')),
                "description": _safe_text(pr.get('description')),
                "attachmentIds": ", ".join(pr.get('attachmentIds', [])),
                "groupIncharge": _safe_text(pr.get('groupIncharge')),
                "state": _safe_text(pr.get('state')),
                "explanation": _safe_text(pr.get('collaborationCNNExplanation')),
                "identification": fa['identification'],
                "resolution": fa['resolution'],
                "subSystem": fa['subSystem'],
                "root_cause": fa['rootCause'],  # matches table field
            })
        start_at += PAGE_SIZE
        time.sleep(SLEEP_BETWEEN_PAGES)
    return rows[:limit]

@app.route("/api/pronto/sync", methods=["POST"])
def pronto_sync():
    """
    Sync recent PRs from PRONTO into SQLite DB.
    Body (optional): { "limit": 100, "autoCategorizeNonCNN": false }
    """
    payload = request.get_json(silent=True) or {}
    limit = int(payload.get("limit") or 100)
    auto_categorize_non_cnn = bool(payload.get("autoCategorizeNonCNN") or False)

    try:
        items = _fetch_recent_prs_with_fa(limit=limit)
    except Exception as e:
        return jsonify({"ok": False, "error": f"PRONTO fetch failed: {e}"}), 500

    conn = sqlite3.connect(DB_PATH)
    # Ensure table and unique index exist
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ux_precheck_pr_id'")
    if not c.fetchone():
        init_db()

    n_upsert = 0
    for it in items:
        state = it.get("state", "")
        if state == "Correction Not Needed":
            category = "Precheck with CNN PR"
        elif auto_categorize_non_cnn:
            category = "Precheck with valid PR" if it.get("pr_id") else "Precheck without PR"
        else:
            category = ""

        vals = (
            it.get("pr_id") or f"bulk_{state or 'no_state'}",  # filename
            it.get("description",""),
            it.get("pr_id"),  
            it.get("title",""),
            it.get("softwareRelease",""),
            it.get("softwareBuild",""),
            it.get("attachmentIds",""),
            it.get("groupIncharge",""),
            it.get("identification",""),
            it.get("resolution",""),
            it.get("subSystem",""),
            it.get("root_cause",""),
            it.get("explanation",""),
            category
        )
        conn.execute(UPSERT_SQL, vals)
        n_upsert += 1
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "count": n_upsert})

# ========= Entry =========
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
