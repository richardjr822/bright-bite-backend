# app/utils/file_upload.py
import os
import uuid
from fastapi import UploadFile
import aiofiles
from PIL import Image

# General uploads directory for BrightBite (not just candidates)
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def save_upload_file(upload_file: UploadFile, subfolder: str = "") -> str:
    """
    Save an uploaded file and return the relative file path.
    Optionally specify a subfolder (e.g., 'meal_plans', 'events', etc.).
    """
    # Create subfolder if specified
    folder = os.path.join(UPLOAD_DIR, subfolder) if subfolder else UPLOAD_DIR
    os.makedirs(folder, exist_ok=True)

    # Generate unique filename
    file_extension = os.path.splitext(upload_file.filename)[1]
    new_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(folder, new_filename)

    # Save the file
    async with aiofiles.open(file_path, 'wb') as out_file:
        while content := await upload_file.read(1024 * 1024):
            await out_file.write(content)

    # Optimize image if it's an image
    if file_extension.lower() in ['.jpg', '.jpeg', '.png']:
        try:
            with Image.open(file_path) as img:
                max_size = 1000
                if img.width > max_size or img.height > max_size:
                    ratio = min(max_size / img.width, max_size / img.height)
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)
                img.save(file_path, optimize=True, quality=85)
        except Exception as e:
            print(f"Image optimization failed: {e}")

    # Return relative path (for use in API responses)
    rel_path = os.path.relpath(file_path, ".").replace("\\", "/")
    return f"/{rel_path}"