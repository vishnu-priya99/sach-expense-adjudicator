import os
import shutil

def add_invalid_sample():
    src = r"C:\Users\vishn\OneDrive\Pictures\image_2.jpg"
    dst_dir = "demo_bills"
    dst = os.path.join(dst_dir, "invalid_scenic_photo.jpg")
    
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"Successfully copied scenic photo from '{src}' to '{dst}'")
    else:
        print(f"Source file not found at '{src}' to copy.")

if __name__ == "__main__":
    add_invalid_sample()
