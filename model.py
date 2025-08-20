import os
from sentence_transformers import SentenceTransformer

os.environ["TRANSFORMERS_VERBOSITY"] = "debug"
os.environ["HF_HOME"] = os.path.expanduser("~/.cache/huggingface")

model = SentenceTransformer("all-MiniLM-L6-v2")
print("✅ Model downloaded and loaded.")
