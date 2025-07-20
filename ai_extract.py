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
import pytesseract
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path
import sqlite3
import time
import logging

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
}

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
        return '\n'.join([line.strip() for line in body.splitlines() if line.strip()])
    except Exception as e:
        logger.error(f"Failed to parse EML {eml_path}: {e}")
        return ""

def chunk_body(body, max_chunk_size=1000):
    sentences = sent_tokenize(body)
    chunks, current_chunk = [], ""
    for s in sentences:
        if len(current_chunk) + len(s) < max_chunk_size:
            current_chunk += s + " "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = s + " "
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def filter_chunks(chunks):
    return [c for c in chunks if '--' not in c]

def store_chunks(chunks, email_id):
    embeddings = embedder.encode(chunks)
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        collection.add(
            documents=[chunk],
            embeddings=[embedding.tolist()],
            ids=[f"{email_id}_chunk_{i}"],
            metadatas={"email_id": email_id}
        )

def query_chunks(query, email_id, n_results=4):
    try:
        query_mapping = {
            "Detail Test Steps": "What are the specific step-by-step actions performed during the test?",
            "Expected Result": "What behavior or outcome was expected from this test?",
            "Actual Result": "What behavior or result was actually observed during the test?"
        }
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
        "max_tokens": 200
    }
    try:
        res = requests.post(LLM_GATEWAY_URL, json=body, headers=headers)
        res.raise_for_status()
        return res.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""

def extract_description(body, email_id):
    if all(x in body for x in ["[1. Detail Test Steps:]", "[2. Expected Result:]", "[3. Actual Result:]"]):
        try:
            content = re.search(r"(\[1\..*?)(?=\[4\.|\Z)", body, re.DOTALL).group(1)
            steps = re.search(r"\[1\..*?:\](.*?)(?=\[2\.)", content, re.DOTALL).group(1).strip()
            expected = re.search(r"\[2\..*?:\](.*?)(?=\[3\.)", content, re.DOTALL).group(1).strip()
            actual = re.search(r"\[3\..*?:\](.*)", content, re.DOTALL).group(1).strip()
            logger.info("Extracted description using regex.")
            description = (
                "[1. Detail Test Steps:]\n" + steps + "\n\n" +
                "[2. Expected Result:]\n" + expected + "\n\n" +
                "[3. Actual Result:]\n" + actual
            )
            return {"description": description.strip()}
        except Exception as e:
            logger.warning(f"Regex extraction failed: {e}")

    logger.info("Using LLM for description extraction...")
    chunks = filter_chunks(chunk_body(body))
    store_chunks(chunks, email_id)
    fields = {}
    for name in ["Detail Test Steps", "Expected Result", "Actual Result"]:
        relevant = query_chunks(name, email_id)
        content = "\n".join(relevant) if relevant else body[:1000]
        prompt = f"""Extract ONLY the {name} from the following text.

—— Instructions ——
- Use 1–2 clear sentences.
- No greeting, no background.
- Do not assume, only use visible content.

========= EMAIL CONTENT =========
{content}
========= END ========="""
        fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)
    description = (
        "[1. Detail Test Steps:]\n" + fields.get("detail_test_steps", "") + "\n\n" +
        "[2. Expected Result:]\n" + fields.get("expected_result", "") + "\n\n" +
        "[3. Actual Result:]\n" + fields.get("actual_result", "")
    )
    return {"description": description.strip()}

def extract_resolution(body, email_id):
    if all(x in body for x in ["[1. Workaround:]", "[2. Description of the correction:]", "[3. Test requirements:]"]):
        try:
            content = re.search(r"(\[1\..*?)(?=\[4\.|\Z)", body, re.DOTALL).group(1)
            workaround = re.search(r"\[1\..*?:\](.*?)(?=\[2\.)", content, re.DOTALL).group(1).strip()
            correction = re.search(r"\[2\..*?:\](.*?)(?=\[3\.)", content, re.DOTALL).group(1).strip()
            testreq = re.search(r"\[3\..*?:\](.*)", content, re.DOTALL).group(1).strip()
            logger.info("Extracted resolution using regex.")
            return {
                "resolution": f"[1. Workaround:]\n{workaround}\n\n[2. Description of the correction:]\n{correction}\n\n[3. Test requirements:]\n{testreq}".strip()
            }
        except Exception as e:
            logger.warning(f"Regex extraction for resolution failed: {e}")

    logger.info("Using LLM for resolution extraction...")
    chunks = filter_chunks(chunk_body(body))
    store_chunks(chunks, email_id + "_res")
    fields = {}
    for name, question in [
        ("Workaround", "How problem can be avoided or effects mitigated without code/HW changes before correction is ready?"),
        ("Description of the correction", "What changes were done in code/HW architecture to fix the issue and how problem will be solved? Do not list the lines of code, give the explanation in plain text instead."),
        ("Test requirements", "How to test the correction in real environment available for Customer? How to test the correction and catch the problem in future in SCT/UT/MT level?")
    ]:
        relevant = query_chunks(name, email_id + "_res")
        content = "\n".join(relevant) if relevant else body[:1000]
        prompt = f"""Extract ONLY the [{name}] from the following text.

—— Instructions ——
- Use 1–2 clear sentences.
- No greeting, no background.
- Do not assume, only use visible content.

========= EMAIL CONTENT =========
{content}
========= END ========="""
        fields[name.lower().replace(" ", "_")] = ask_llm_gateway(prompt)
    resolution = (
        "[1. Workaround:]\n" + fields.get("workaround", "") + "\n\n" +
        "[2. Description of the correction:]\n" + fields.get("description_of_the_correction", "") + "\n\n" +
        "[3. Test requirements:]\n" + fields.get("test_requirements", "")
    )
    return {"resolution": resolution.strip()}
