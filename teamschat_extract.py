from PIL import Image
import pytesseract
import os

def extract_text_from_image(image_path):
    image = Image.open(image_path)

    text = pytesseract.image_to_string(image)

    return text

if __name__ == "__main__":
    image_files = [
        "image.png"
    ]

    for image_file in image_files:
        print(f"\n===== Extracted text from {os.path.basename(image_file)} =====\n")
        extracted_text = extract_text_from_image(image_file)
        print(extracted_text.strip())
