import requests
import uuid
import time

# ✅ 替换成你的信息
BASE_URL = "https://nvdc-prod-euw-llmapiorchestration-app.azurewebsites.net"
FILE_PATH = "test_image.png"  # 支持 PDF, DOCX, PNG, EML, TXT 等
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyTmFtZSI6IkFFUHJlY2hlY2tUZXN0SUVUTWVzc2FnZXNMTE1Ub29sIiwiT2JqZWN0SUQiOiJERUUyODY5MS04NkQyLTQwMEEtQjM3Ri1FNjE2RTI4NTY1ODAiLCJ3b3JrU3BhY2VOYW1lIjoiVlIxNzE5QUVQcmVjaGVja1Rlc3RJRVRNZXNzYWdlc0xMTSIsIm5iZiI6MTc1MTQ1NzQyOCwiZXhwIjoxNzgyOTkzNDI4LCJpYXQiOjE3NTE0NTc0Mjh9.bgDDcTkVbrndgqT0LZ5rQbZi_vsbQ_FsCKdrkF0an3o"
WORKSPACE_NAME = "VR1719AEPrecheckTestIETMessagesLLM"
# Step 1: 上传并嵌入
upload_url = f"{BASE_URL}/v1.1/DocumentAssistant/UploadDocumentsAndIndex"
upload_headers = {
    "api-key": API_KEY
}
with open(FILE_PATH, "rb") as f:
    files = {
        "DataFile": (FILE_PATH, f),
        "WorkspaceName": (None, WORKSPACE_NAME),
        "isMultimodal": (None, "true")
    }
    upload_response = requests.post(upload_url, headers=upload_headers, files=files)
    print("Upload+Index Response:", upload_response.json())

# ✅ 获取 documentId（从 headers 或返回值）
document_id = upload_response.headers.get("documentId")
if not document_id:
    print("❗可能的 header 中未返回 documentId，尝试 fallback...")
    try:
        document_id = upload_response.json().get("payload", {}).get("documentId")
    except:
        pass
if not document_id:
    raise Exception("❌ 无法获取 documentId")

print("✅ documentId:", document_id)

# 可选等待时间，确保索引完成
time.sleep(3)

# Step 2: 使用 prompt 查询摘要
query_url = f"{BASE_URL}/v1.1/DocumentAssistant/QueryDocumentsSummary"
query_headers = {
    "api-key": API_KEY,
    "workspaceName": WORKSPACE_NAME,
    "Content-Type": "application/json-patch+json"
}
query_payload = {
    "sessionId": str(uuid.uuid4()),
    "sessionName": "SmartDocSummary",
    "input": "Summarize the content of the uploaded file. Focus on test steps, expected result, and actual result.",
    "embeddingModel": "Ada",
    "vectorStore": "AzureAISearch",
    "vectorIndexName": "",
    "promptTemplate": "ChatDocumentAssistant",
    "completionModel": "GPT35_16K",
    "topResults": 4,
    "strictnessScore": 2,
    "searchType": "SemanticHybrid",
    "maxTokens": 1000,
    "pastMessages": 0,
    "origin": "Chat",
    "stream": False,
    "filterOperation": "AND"
}
query_response = requests.post(query_url, headers=query_headers, json=query_payload)
print("📄 Summary Response:")
print(query_response.json())
