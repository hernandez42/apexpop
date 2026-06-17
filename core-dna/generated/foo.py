import os

def request_documents():
    missing_docs = ["nvidia", "ali_coder", "xfyun"]
    for doc in missing_docs:
        if not os.path.exists(f"/home/ubuntu/.nanobot/{doc}.txt"):
            print(f"Document {doc}.txt not found. Please provide the document.")

if __name__ == "__main__":
    request_documents()