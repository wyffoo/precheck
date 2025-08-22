# -*- coding: utf-8 -*-
import os
import re
import email
from email import policy
import logging
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image
import pytesseract
from sentence_transformers import SentenceTransformer
from nltk.tokenize import sent_tokenize
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

# ========== CONFIG ==========
LLM_GATEWAY_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyTmFtZSI6IkFFUHJlY2hlY2tUZXN0SUVUTWVzc2FnZXNMTE1Ub29sIiwiT2JqZWN0SUQiOiJERUUyODY5MS04NkQyLTQwMEEtQjM3Ri1FNjE2RTI4NTY1ODAiLCJ3b3JrU3BhY2VOYW1lIjoiVlIxNzE5QUVQcmVjaGVja1Rlc3RJRVRNZXNzYWdlc0xMTSIsIm5iZiI6MTc1MTQ1NzQyOCwiZXhwIjoxNzgyOTkzNDI4LCJpYXQiOjE3NTE0NTc0Mjh9.bgDDcTkVbrndgqT0LZ5rQbZi_vsbQ_FsCKdrkF0an3o"
LLM_GATEWAY_URL = "https://nvdc-prod-euw-llmapiorchestration-app.azurewebsites.net/v1.1/Chat/Completions"
WORKSPACE_NAME = "VR1719AEPrecheckTestIETMessagesLLM"

UPLOAD_FOLDER = "uploads"

# ✅ 关键阈值（尽量全文扔给 LLM；超过阈值再做极轻量拼接）
MAX_CONTEXT_CHARS = 9000        # 直接把全文喂给 LLM 的最大字符数
CHUNK_SIZE_CHARS = 1200         # 超长文本时的分块大小
K_PER_QUERY = 2                 # 每个查询意图挑选的 chunk 数
MAX_JOIN_CHARS = 9000           # 大文本时最终拼接进 prompt 的字符上限
MAX_LLM_TOKENS = 900            # 生成上限（配合一次输出三段）

# ========== SETUP ==========
app = Flask(__name__)
CORS(app)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_extract")

embedder = SentenceTransformer("all-MiniLM-L6-v2")

# 先声明函数；SUPPORTED_EXTENSIONS 里会调用
def parse_eml(eml_path): ...
def parse_msg(msg_path): ...
def extract_image_text(img_path): ...

SUPPORTED_EXTENSIONS = {
    ".eml": lambda path: parse_eml(path),
    ".msg": lambda path: parse_msg(path),
    ".jpg": lambda path: extract_image_text(path),
    ".jpeg": lambda path: extract_image_text(path),
    ".png": lambda path: extract_image_text(path),
    ".txt": lambda path: open(path, "r", encoding="utf-8").read(),
}

# ========== SUPPORT ==========
def clean_text(text: str) -> str:
    """温和清洗：去掉头部字段/客套/分隔线，但不因为“行很长”就丢内容。"""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        if re.match(r"^(from|to|cc|subject|date|sent):", line, re.I):
            continue
        if any(x in line.lower() for x in ["best regards", "thanks", "thank you", "forwarded message"]):
            continue
        if line.startswith("-----"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)

def extract_image_text(img_path: str) -> str:
    try:
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang="eng")
        return (text or "").strip()
    except Exception as e:
        logger.error(f"Failed to extract text from image {img_path}: {e}")
        return ""

def parse_eml(eml_path: str) -> str:
    try:
        with open(eml_path, "r", encoding="utf-8") as f:
            msg = email.message_from_file(f, policy=policy.default)
        subject = msg["subject"] or ""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore") + "\n"
                elif ctype == "text/html":
                    html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    soup = BeautifulSoup(html, "html.parser")
                    body += soup.get_text(separator="\n") + "\n"
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        return (subject.strip() + "\n\n" + clean_text(body)).strip()
    except Exception as e:
        logger.error(f"Failed to parse EML {eml_path}: {e}")
        return ""

def parse_msg(msg_path: str) -> str:
    """解析 Outlook .msg：优先 HTML，没有则退回纯文本；拼接 subject + 清洗正文。"""
    try:
        import extract_msg
        msg = extract_msg.Message(msg_path)
        subject = msg.subject or ""
        try:
            html_body = msg.htmlBody
        except Exception:
            html_body = None
        if html_body:
            soup = BeautifulSoup(html_body, "html.parser")
            body_text = soup.get_text(separator="\n")
        else:
            body_text = msg.body or ""
        cleaned = clean_text(body_text)
        return (subject.strip() + "\n\n" + cleaned).strip()
    except Exception as e:
        logger.error(f"Failed to parse MSG {msg_path}: {e}")
        return ""

def chunk_text_no_preface(body: str, max_chunk_size: int = CHUNK_SIZE_CHARS):
    """不抽取 preface 的轻量分块；仅在文本很长时使用。"""
    try:
        sentences = sent_tokenize(body)
    except Exception:
        sentences = None
    if not sentences or len(" ".join(sentences)) < len(body) * 0.5:
        # 句子切分效果欠佳时，按段落兜底
        sentences = re.split(r"\n{2,}", body)

    chunks, cur = [], ""
    for s in sentences:
        s = (s or "").strip()
        if not s:
            continue
        if len(cur) + len(s) + 1 <= max_chunk_size:
            cur = (cur + " " + s) if cur else s
        else:
            chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks

def _build_context_by_similarity(full_text: str, group: str) -> str:
    """
    对于超长文本：不落地、不用向量库，内存里做一次小型相似度打分，拼接最相关片段。
    """
    # 分块 + 去噪
    chunks = chunk_text_no_preface(full_text, max_chunk_size=CHUNK_SIZE_CHARS)
    chunks = [c for c in chunks if len(c.strip()) >= 30]
    if not chunks:
        return full_text[:MAX_JOIN_CHARS]

    # 召回意图
    if group == "desc":
        queries = [
            "What are the test actions performed?",
            "What should happen if everything works correctly?",
            "What actually happened during the test?",
        ]
    else:
        queries = [
            "What workaround was used before applying a full fix?",
            "What correction or change was implemented to fix the issue?",
            "How was the correction tested or validated?",
        ]

    # 语义相似度（内存计算，无持久化）
    chunk_vecs = embedder.encode(chunks, normalize_embeddings=True)
    query_vecs = embedder.encode(queries, normalize_embeddings=True)

    # 为每个查询挑选 top-k chunk，按得分从高到低合并，控制总体字符数
    selected_idx = []
    for qv in query_vecs:
        sims = np.dot(chunk_vecs, qv)  # 因为 normalize_embeddings=True，所以是 cosine 相似度
        top = np.argsort(-sims)[:K_PER_QUERY]
        for idx in top:
            if idx not in selected_idx:
                selected_idx.append(idx)

    # 兜底：若还太少，顺序追加前几块
    if not selected_idx:
        selected_idx = list(range(min(len(chunks), 6)))

    # 拼接直至达到上限
    assembled, total = [], 0
    for idx in selected_idx:
        c = chunks[idx]
        if total + len(c) + 2 > MAX_JOIN_CHARS:
            break
        assembled.append(c)
        total += len(c) + 2

    # 仍过短就再顺序补齐
    i = 0
    while total < MAX_JOIN_CHARS and i < len(chunks):
        if i not in selected_idx:
            c = chunks[i]
            if total + len(c) + 2 > MAX_JOIN_CHARS:
                break
            assembled.append(c)
            total += len(c) + 2
        i += 1

    return "\n\n".join(assembled)

def ask_llm_gateway(prompt: str, max_tokens: int = MAX_LLM_TOKENS) -> str:
    headers = {
        "api-key": LLM_GATEWAY_API_KEY,
        "workspaceName": WORKSPACE_NAME,
        "Content-Type": "application/json-patch+json",
    }
    body = {
        "model": "GPT41",
        "messages": [
            {"role": "system", "content": "You are a senior telecom test engineer."},
            {"role": "user", "content": prompt},
        ],
        "top_p": 0.5,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    try:
        res = requests.post(LLM_GATEWAY_URL, json=body, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""

def make_triplet_prompt(full_text: str, group: str = "desc") -> str:
    """
    group="desc" -> Detail Test Steps / Expected Result / Actual Result
    group="reso" -> Workaround / Description of the correction / Test requirements
    强制 LLM 按 EXACT 模板输出。
    """
    if group == "desc":
        header = "Extract these three sections: [1. Detail Test Steps:], [2. Expected Result:], [3. Actual Result:]"
        instructions = """Rules:
- Output MUST be EXACTLY the following template, in English, with no extra commentary, no greetings, no extra headers, no markdown fences:
[1. Detail Test Steps:]
<content>

[2. Expected Result:]
<content>

[3. Actual Result:]
<content>
- Focus ONLY on test steps, expected behavior, and actual observed results.
- Remove names, email headers, signatures, and non-technical fluff.
- Be concise and technical. Use bullet points only if they add technical value."""
    else:
        header = "Extract these three sections: [1. Workaround:], [2. Description of the correction:], [3. Test requirements:]"
        instructions = """Rules:
- Output MUST be EXACTLY the following template, in English, with no extra commentary, no greetings, no extra headers, no markdown fences:
[1. Workaround:]
<content>

[2. Description of the correction:]
<content>

[3. Test requirements:]
<content>
- Focus ONLY on workaround logic, the implemented correction, and how it was tested/validated.
- Remove names, email headers, signatures, and summaries.
- Be concise and technical. Use bullet points only if they add technical value."""

    return f"""You are a telecom test engineer. {header}

{instructions}

========= TEXT INPUT =========
{full_text[:MAX_CONTEXT_CHARS]}
========= END ========="""

# ========== CORE EXTRACT (尽可能全文扔给 LLM；只有超长才轻量拼接) ==========
def extract_description(body: str, email_id: str):
    body = clean_text(body)

    # 已是标准三段 → 直接解析
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

    # 小文本：直接全文喂给 LLM
    if len(body) <= MAX_CONTEXT_CHARS:
        prompt = make_triplet_prompt(body, group="desc")
        out = ask_llm_gateway(prompt, max_tokens=MAX_LLM_TOKENS)
        return {"description": out}

    # 大文本：不持久化、仅内存召回若干片段再一次问三段
    context = _build_context_by_similarity(body, group="desc")
    prompt = make_triplet_prompt(context, group="desc")
    out = ask_llm_gateway(prompt, max_tokens=MAX_LLM_TOKENS)
    return {"description": out}

def extract_resolution(body: str, email_id: str):
    body = clean_text(body)

    # 已是标准三段 → 直接解析
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

    # 小文本：直接全文喂给 LLM
    if len(body) <= MAX_CONTEXT_CHARS:
        prompt = make_triplet_prompt(body, group="reso")
        out = ask_llm_gateway(prompt, max_tokens=MAX_LLM_TOKENS)
        return {"resolution": out}

    # 大文本：不持久化、仅内存召回若干片段再一次问三段
    context = _build_context_by_similarity(body, group="reso")
    prompt = make_triplet_prompt(context, group="reso")
    out = ask_llm_gateway(prompt, max_tokens=MAX_LLM_TOKENS)
    return {"resolution": out}
