from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
from ai_extract import parse_eml, extract_description
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route("/api/extract", methods=["POST"])
def extract_from_eml():
    if 'eml_file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    eml_file = request.files['eml_file']
    filename = secure_filename(eml_file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    eml_file.save(file_path)

    # Step 1: Parse and clean email content
    body = parse_eml(file_path)
    if not body:
        return jsonify({"error": "Failed to parse EML file"}), 500

    # Step 2: Extract description
    description = extract_description(body, email_id=filename)

    # Combine with printed header
    full_output = f"\n===== FINAL DESCRIPTION =====\n{description}"

    # Print to terminal for debugging (optional)
    print(full_output)

    # Return for frontend display
    return jsonify({ "description": full_output })

if __name__ == "__main__":
    app.run(debug=True)
