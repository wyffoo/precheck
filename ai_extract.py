# -*- coding: utf-8 -*-
import os
import re
import email
from email import policy
import requests
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import chromadb
from nltk.tokenize import sent_tokenize
from PIL import Image
import easyocr
import pytesseract
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import logging
from doctr.io import DocumentFile
from doctr.models import ocr_predictor



# 初始化模型（可以在应用启动时只加载一次）
ocr_model = ocr_predictor(pretrained=True)

# ========== CONFIG ==========
LLM_GATEWAY_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyTmFtZSI6IkFFUHJlY2hlY2tUZXN0SUVUTWVzc2FnZXNMTE1Ub29sIiwiT2JqZWN0SUQiOiJERUUyODY5MS04NkQyLTQwMEEtQjM3Ri1FNjE2RTI4NTY1ODAiLCJ3b3JrU3BhY2VOYW1lIjoiVlIxNzE5QUVQcmVjaGVja1Rlc3RJRVRNZXNzYWdlc0xMTSIsIm5iZiI6MTc1MTQ1NzQyOCwiZXhwIjoxNzgyOTkzNDI4LCJpYXQiOjE3NTE0NTc0Mjh9.bgDDcTkVbrndgqT0LZ5rQbZi_vsbQ_FsCKdrkF0an3o"
LLM_GATEWAY_URL = "https://nvdc-prod-euw-llmapiorchestration-app.azurewebsites.net/v1.1/Chat/Completions"
WORKSPACE_NAME = "VR1719AEPrecheckTestIETMessagesLLM"
UPLOAD_FOLDER = "uploads"
DB_PATH = "precheck_records.db"

# ========== SETUP ==========
app = Flask(__name__)
CORS(app)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

embedder = SentenceTransformer('all-MiniLM-L6-v2')
chroma_client = chromadb.Client()
collection = chromadb.PersistentClient().get_or_create_collection(name="email_chunks")

SUPPORTED_EXTENSIONS = {
    ".eml": lambda path: parse_eml(path),
    ".jpg": lambda path: extract_image_text(path),
    ".jpeg": lambda path: extract_image_text(path),
    ".png": lambda path: extract_image_text(path),
    ".txt": lambda path: open(path, 'r', encoding='utf-8').read(),
}

ocr_model = None
def extract_image_text(img_path):
    global ocr_model
    if ocr_model is None:
        ocr_model = ocr_predictor(pretrained=True)
    try:
        doc = DocumentFile.from_images(img_path)
        result = ocr_model(doc)
        exported = result.export()
        text_lines = []
        for block in exported["pages"][0]["blocks"]:
            for line in block["lines"]:
                text_lines.append(line["value"])
        return "\n".join(text_lines).strip()
    except Exception as e:
        logger.error(f"[OCR FAIL] doctr failed on {img_path}: {e}")
        return ""

# ========== SUPPORT ==========
def clean_text(text):
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(from|to|cc|subject|date|sent):", line, re.I):
            continue
        if any(x in line.lower() for x in ["best regards", "thanks", "thank you", "forwarded message"]):
            continue
        if line.startswith("-----") or len(line) > 300:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)

def extract_image_text(img_path):
    try:
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang='eng')
        return text.strip()
    except Exception as e:
        logger.error(f"Failed to extract text from image {img_path}: {e}")
        return ""

def parse_eml(eml_path):
    try:
        with open(eml_path, 'r', encoding='utf-8') as f:
            msg = email.message_from_file(f, policy=policy.default)
        subject = msg['subject'] or ""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == 'text/plain':
                    body += part.get_payload(decode=True).decode('utf-8', errors='ignore') + "\n"
                elif ctype == 'text/html':
                    html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(html, "html.parser")
                    body += soup.get_text(separator="\n") + "\n"
        else:
            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
        return subject.strip() + "\n\n" + clean_text(body)
    except Exception as e:
        logger.error(f"Failed to parse EML {eml_path}: {e}")
        return ""

def chunk_body(body, max_chunk_size=1000):
    sentences = sent_tokenize(body)

    # 保留首段（背景介绍）不参与 chunk
    if len(sentences) > 3:
        preface = " ".join(sentences[:3])
        sentences = sentences[3:]
    else:
        preface = " ".join(sentences)
        sentences = []

    chunks, current_chunk = [], ""
    for s in sentences:
        if len(current_chunk) + len(s) < max_chunk_size:
            current_chunk += s + " "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = s + " "
    if current_chunk:
        chunks.append(current_chunk.strip())

    return [preface.strip()] + chunks if preface.strip() else chunks


def filter_chunks(chunks):
    def is_noise(c):
        low_info = ["regards", "thank you", "sent from my", "please find attached", "forwarded message"]
        return any(kw in c.lower() for kw in low_info) or len(c.strip()) < 30
    return [c for c in chunks if not is_noise(c)]

def hash_chunk(chunk):
    return hashlib.md5(chunk.encode('utf-8')).hexdigest()

def store_chunks(chunks, email_id):
    try:
        # 检查是否已有同样 email_id 的嵌入，避免重复存储
        existing = collection.query(
            query_embeddings=[[0.0]*384],  # dummy 向量
            n_results=1,
            where={"email_id": email_id}
        )
        if existing.get("ids"):
            logger.info(f"[SKIP] Chunks for {email_id} already exist in vector DB.")
            return
    except Exception as e:
        logger.warning(f"[WARN] Failed to check existing chunks for {email_id}: {e}")

    try:
        embeddings = embedder.encode(chunks)
        ids, docs, embeds = [], [], []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = f"{email_id}_{hash_chunk(chunk)}"
            ids.append(chunk_id)
            docs.append(chunk)
            embeds.append(embedding.tolist())

        collection.add(
            documents=docs,
            embeddings=embeds,
            ids=ids,
            metadatas=[{"email_id": email_id}] * len(ids)
        )

        logger.info(f"[OK] Stored {len(chunks)} chunks for {email_id}.")

        # ✅ 本地保存 chunk 内容做 debug 或 QA
        Path("debug_embeddings").mkdir(exist_ok=True)
        with open(f"debug_embeddings/{email_id}.json", "w", encoding="utf-8") as f:
            json.dump({"email_id": email_id, "chunks": chunks}, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"[ERROR] Failed to store chunks for {email_id}: {e}")

def query_chunks(query, email_id, query_mapping, n_results=6):
    try:
        query_embedding = embedder.encode([query_mapping.get(query, query)])[0]
        results = collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results,
            where={"email_id": email_id}
        )
        return [doc for doc in results['documents'][0]] if results['documents'] else []
    except Exception as e:
        logger.error(f"Query failed for {email_id}: {e}")
        return []

def ask_llm_gateway(prompt):
    headers = {
        "api-key": LLM_GATEWAY_API_KEY,
        "workspaceName": WORKSPACE_NAME,
        "Content-Type": "application/json-patch+json"
    }
    body = {
        "model": "GPT41",
        "messages": [
            {"role": "system", "content": "You are a senior telecom test engineer."},
            {"role": "user", "content": prompt}
        ],
        "top_p": 0.5,
        "temperature": 0.7,
        "max_tokens": 500
    }
    try:
        res = requests.post(LLM_GATEWAY_URL, json=body, headers=headers)
        res.raise_for_status()
        return res.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""

def extract_description(body, email_id):
    body = clean_text(body)
    description_mapping = {
        "Detail Test Steps": "What are the test actions performed?",
        "Expected Result": "What should happen if everything works correctly?",
        "Actual Result": "What actually happened during the test?"
    }

    # ====== 正则结构提取优先（完整三段）======
    if all(x in body for x in ["[1. Detail Test Steps:]", "[2. Expected Result:]", "[3. Actual Result:]"]):
        try:
            content = re.search(r"(\[1\..*?)(?=\[4\.|\Z)", body, re.DOTALL).group(1)
            steps = re.search(r"\[1\..*?:\](.*?)(?=\[2\.)", content, re.DOTALL).group(1).strip()
            expected = re.search(r"\[2\..*?:\](.*?)(?=\[3\.)", content, re.DOTALL).group(1).strip()
            actual = re.search(r"\[3\..*?:\](.*)", content, re.DOTALL).group(1).strip()
            return {
                "description": f"[1. Detail Test Steps:]\n{steps}\n\n[2. Expected Result:]\n{expected}\n\n[3. Actual Result:]\n{actual}"
            }
        except Exception as e:
            logger.warning(f"Regex extraction for description failed: {e}")

    # ====== 内容短：直接全文提问 ======
    if len(body) < 300:
        logger.info(f"[FALLBACK] Body short — direct LLM prompt: {email_id}")
        fields = {}
        for name in ["Detail Test Steps", "Expected Result", "Actual Result"]:
            prompt = f"""You are a telecom test engineer. Extract the [{name}] section.

—— Instructions ——
• Focus ONLY on test steps, expected behavior, or observed results.
• NO names, greetings, summaries, or email text.
• Be factual, concise, and technical.

========= TEXT INPUT =========
{body[:1500]}
========= END ========="""
            fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)

        return {
            "description": f"[1. Detail Test Steps:]\n{fields.get('detail_test_steps', '')}\n\n[2. Expected Result:]\n{fields.get('expected_result', '')}\n\n[3. Actual Result:]\n{fields.get('actual_result', '')}"
        }

    # ====== 内容长：嵌入 + 检索 + 拼接背景 ======
    chunks = filter_chunks(chunk_body(body))
    store_chunks(chunks, email_id)
    preface = chunks[0] if chunks else ""

    fields = {}
    for name in ["Detail Test Steps", "Expected Result", "Actual Result"]:
        relevant = query_chunks(name, email_id, description_mapping)
        content = preface + "\n\n" + "\n".join(relevant[:2]) if relevant else "\n".join(chunks[:2])
        prompt = f"""You are a telecom test engineer. Extract the [{name}] section.

—— Instructions ——
• NO names, email headers, or summaries.
• ONLY extract clear technical content such as configurations, test sequences, observed results.
• Be concise and specific.

========= TEXT INPUT =========
{content}
========= END ========="""
        fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)

    return {
        "description": f"[1. Detail Test Steps:]\n{fields.get('detail_test_steps', '')}\n\n[2. Expected Result:]\n{fields.get('expected_result', '')}\n\n[3. Actual Result:]\n{fields.get('actual_result', '')}"
    }


def extract_resolution(body, email_id):
    body = clean_text(body)
    resolution_mapping = {
        "Workaround": "What workaround was used before applying a full fix?",
        "Description of the correction": "What correction or change was implemented to fix the issue?",
        "Test requirements": "How was the correction tested or validated?"
    }

    if all(x in body for x in ["[1. Workaround:]", "[2. Description of the correction:]", "[3. Test requirements:]"]):
        try:
            content = re.search(r"(\[1\..*?)(?=\[4\.|\Z)", body, re.DOTALL).group(1)
            workaround = re.search(r"\[1\..*?:\](.*?)(?=\[2\.)", content, re.DOTALL).group(1).strip()
            correction = re.search(r"\[2\..*?:\](.*?)(?=\[3\.)", content, re.DOTALL).group(1).strip()
            testreq = re.search(r"\[3\..*?:\](.*)", content, re.DOTALL).group(1).strip()
            return {
                "resolution": f"[1. Workaround:]\n{workaround}\n\n[2. Description of the correction:]\n{correction}\n\n[3. Test requirements:]\n{testreq}"
            }
        except Exception as e:
            logger.warning(f"Regex extraction for resolution failed: {e}")

    if len(body) < 300:
        logger.info(f"[FALLBACK] Short body — direct LLM resolution: {email_id}")
        fields = {}
        for name in ["Workaround", "Description of the correction", "Test requirements"]:
            prompt = f"""You are a telecom test engineer. Extract the [{name}] section.

—— Instructions ——
• DO NOT include names or summaries.
• Be concise and technical.
• Focus only on resolution logic, test validation, or applied fixes.

========= TEXT INPUT =========
{body[:1500]}
========= END ========="""
            fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)

        return {
            "resolution": f"[1. Workaround:]\n{fields.get('workaround', '')}\n\n[2. Description of the correction:]\n{fields.get('description_of_the_correction', '')}\n\n[3. Test requirements:]\n{fields.get('test_requirements', '')}"
        }

    chunks = filter_chunks(chunk_body(body))
    store_chunks(chunks, email_id + "_res")
    preface = chunks[0] if chunks else ""

    fields = {}
    for name in ["Workaround", "Description of the correction", "Test requirements"]:
        relevant = query_chunks(name, email_id + "_res", resolution_mapping)
        content = preface + "\n\n" + "\n".join(relevant[:2]) if relevant else "\n".join(chunks[:2])
        prompt = f"""You are a telecom test engineer. Extract the [{name}] section.

—— Instructions ——
• DO NOT include names, headers, or summaries.
• Focus ONLY on workaround logic, correction fix, and test conditions.
• Output should be factual and test-related.

========= TEXT INPUT =========
{content}
========= END ========="""
        fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)

    return {
        "resolution": f"[1. Workaround:]\n{fields.get('workaround', '')}\n\n[2. Description of the correction:]\n{fields.get('description_of_the_correction', '')}\n\n[3. Test requirements:]\n{fields.get('test_requirements', '')}"
    }
