import os
from google.cloud import vision

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google_vision_key.json"

def extract_image_text(img_path):
    client = vision.ImageAnnotatorClient()
    with open(img_path, "rb") as image_file:
        content = image_file.read()
    image = vision.Image(content=content)
    response = client.text_detection(image=image)
    texts = response.text_annotations
    if not texts:
        return ""
    return texts[0].description.strip()

print(extract_image_text("uploads/validation_host.png"))
