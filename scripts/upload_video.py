"""Upload a video to StreamDojo from the terminal (until the web app exists).

Usage:
    python scripts/upload_video.py "$API_URL" "$TOKEN" path/to/video.mp4 "Lesson title"

1. Calls POST /videos to create the lesson and get a signed upload form.
2. Sends the file straight to S3 using that form (Python standard library only).
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid


def api_post(url, token, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def multipart(fields, file_path, content_type):
    """Build a multipart/form-data body: the signed fields first, the file last (S3 requires that order)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    with open(file_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(file_path)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main():
    api_url, token, file_path, title = sys.argv[1:5]
    content_type = mimetypes.guess_type(file_path)[0] or "video/mp4"
    size = os.path.getsize(file_path)

    created = api_post(f"{api_url}/videos", token,
                       {"title": title, "contentType": content_type, "fileSizeBytes": size})
    print(f"Lesson created: {created['videoId']} - uploading {size / 1e6:.1f} MB to S3...")

    body, ctype = multipart(created["upload"]["fields"], file_path, content_type)
    req = urllib.request.Request(created["upload"]["url"], data=body, method="POST",
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Upload finished: HTTP {resp.status}")         # S3 returns 204 No Content on success
    except urllib.error.HTTPError as err:
        print(f"Upload failed: HTTP {err.code}\n{err.read().decode()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
