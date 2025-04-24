import email
from email import policy
import re
import requests
from sentence_transformers import SentenceTransformer
import chromadb
import os

# set up OpenRouter API
OPENROUTER_API_KEY = "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"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


# Initialize embedding model and Chroma vector database
embedder = SentenceTransformer('all-MiniLM-L6-v2')
chroma_client = chromadb.Client()
collection = chroma_client.create_collection(name="email_chunks")

# Parse .eml file and extract the plain text content
def parse_eml(eml_path):
    try:
        with open(eml_path, 'r', encoding='utf-8') as f:
            msg = email.message_from_file(f, policy=policy.default)

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    break
        else:
            # Single part email
            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        return body
    except Exception as e:
        print(f"Failed to parse EML: {e}")
        return ""

# Clean body text,normalize line breaks and remove empty lines
def clean_body(body):
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.strip() for line in body.split('\n') if line.strip()]
    return '\n'.join(lines)

# Chunk long email content into smaller pieces for vector embedding
def chunk_body(body, max_chunk_size=400):
    words = body.split()
    chunks = []
    current_chunk = []
    current_size = 0
    for word in words:
        current_chunk.append(word)
        current_size += len(word) + 1
        if current_size >= max_chunk_size:
            chunks.append(' '.join(current_chunk))
            current_chunk = []
            current_size = 0
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    return chunks

#  Filter out common boilerplate and irrelevant lines
def filter_chunks(chunks):
    ignore_keywords = [
        "mailto:", "from:", "to:", "cc:", "subject:", "sent:",
        "@nokia.com", "regards", "thank you", "forwarded", 
        "confidential", "legal", "disclaimer", "Best,", "--",
        "Yours sincerely", "FYI", "Hi", "Hello", "Dear"
    ]
    return [
        chunk for chunk in chunks
        if not any(k in chunk.lower() for k in ignore_keywords)
    ]

#  Store the chunks into ChromaDB with vector embeddings
def store_chunks(chunks, email_id):
    try:
        embeddings = embedder.encode(chunks)
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            collection.add(
                documents=[chunk],
                embeddings=[embedding.tolist()],
                ids=[f"{email_id}_chunk_{i}"],
                metadatas={"email_id": email_id}
            )
    except Exception as e:
        print(f"Failed to store chunks: {e}")

#  Query top-N relevant chunks for a given question
def query_chunks(query, email_id, n_results=2):
    try:
        query_embedding = embedder.encode([query])[0]
        results = collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results,
            where={"email_id": email_id}
        )
        return [doc for doc in results['documents'][0]] if results['documents'] else []
    except Exception as e:
        print(f"Failed to query chunks: {e}")
        return []

#  Ask OpenRouter LLM for extracting a specific test-related section
def ask_openrouter(prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "mistralai/mistral-7b-instruct",
        "messages": [
            {"role": "system", "content": "You are a senior telecom test engineer. Extract only the test-specific technical content."},
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(API_URL, json=body, headers=headers)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']

#  Main function to extract description from cleaned email body
def extract_description(body, email_id):
    body = clean_body(body)

    # If structured block present, use regex extraction
    if all(x in body for x in ["[1. Detail Test Steps:]", "[2. Expected Result:]", "[3. Actual Result:]"]):
        print("Using Regex extraction...")
        pattern = r"(\[1\.\s*Detail Test Steps:\].*?)(?=\[4\.\s*Analysis of Logs:\]|\Z)"
        match = re.search(pattern, body, re.DOTALL)
        if match:
            content = match.group(1).strip()
            steps = re.search(r"\[1\.\s*Detail Test Steps:\](.*?)(?=\[2\.\s*Expected Result:\]|\Z)", content, re.DOTALL)
            expected = re.search(r"\[2\.\s*Expected Result:\](.*?)(?=\[3\.\s*Actual Result:\]|\Z)", content, re.DOTALL)
            actual = re.search(r"\[3\.\s*Actual Result:\](.*?)(?=\[4\.\s*Analysis of Logs:\]|\Z)", content, re.DOTALL)
            return (
                "[1. Detail Test Steps:]\n" + (steps.group(1).strip() if steps else "[Not found]") + "\n\n" +
                "[2. Expected Result:]\n" + (expected.group(1).strip() if expected else "[Not found]") + "\n\n" +
                "[3. Actual Result:]\n" + (actual.group(1).strip() if actual else "[Not found]")
            )

    # Otherwise fallback to LLM-based semantic extraction
    print("Using OpenRouter LLM extraction...")
    chunks = chunk_body(body)
    chunks = filter_chunks(chunks)
    store_chunks(chunks, email_id)

    desc_parts = []
    for name in ["Detail Test Steps", "Expected Result", "Actual Result"]:
        related_chunks = query_chunks(f"What are the {name.lower()}?", email_id)
        content = "\n".join(related_chunks) if related_chunks else body[:1000]
        prompt = f"""
Extract ONLY the original content for the section: {name}

Focus on technical instructions, test steps, KPIs, alarm messages or configuration.
Exclude names, emails, forwarded headers or greetings.

========= TEXT START =========
{content}
========= TEXT END =========
"""
        extracted = ask_openrouter(prompt).strip()
        desc_parts.append(f"[{name}:]\n{extracted}\n\n")

    return ''.join(desc_parts).strip()

if __name__ == "__main__":
    eml_path = "example_eml.eml"  # Path to  .eml file
    email_id = os.path.basename(eml_path).split('.')[0]  

    body = parse_eml(eml_path)  
    description = extract_description(body, email_id)

    print("\n========== EXTRACTED DESCRIPTION ==========\n")
    print(description)