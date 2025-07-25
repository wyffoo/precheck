from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path
import sqlite3, os
import nltk
import uuid
from ai_extract import parse_eml, extract_description, extract_resolution, extract_image_text

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

UPLOAD_FOLDER = "uploads"
DB_PATH = "precheck_records.db"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

SUPPORTED_EXTENSIONS = {
    ".eml": parse_eml,
    ".jpg": extract_image_text,
    ".jpeg": extract_image_text,
    ".png": extract_image_text,
    ".txt": lambda path: open(path, 'r', encoding='utf-8').read(),
}

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
        )
    """)
    conn.commit()
    conn.close()

def save_to_database(filename, structured):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO precheck_records (
            filename, description, pr_id, title, softwareRelease,
            softwareBuild, attachmentIds, groupIncharge, identification,
            resolution, subSystem, root_cause, explanation, category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        filename,
        structured.get("description", ""),
        structured.get("pr_id", ""),
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

        if not main_filename and ext == ".eml":
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
            "pr_id": "", "title": "", "softwareRelease": "",
            "softwareBuild": "", "attachmentIds": "", "groupIncharge": "",
            "identification": "", "explanation": "", "subSystem": "",
            "root_cause": "", "category": ""
        })

        save_to_database(used_filename, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
