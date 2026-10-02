"""위키 변환기 — 내 컴퓨터에서만 도는 작은 웹 화면 (표준 라이브러리만 사용).

    python app.py              # 브라우저가 열립니다
    python app.py --port 9000 --no-browser

두 가지 기능
  문서 변환 : 글을 붙여넣으면 다른 위키 문법으로 바꿔 줍니다.
  대량 변환 : zip·폴더·파일째로 넣으면 다른 위키의 데이터 형식(zip)으로 만들어 줍니다.
이 프로그램은 127.0.0.1(내 컴퓨터)에서만 열리고, 올린 파일은 어디로도 보내지 않습니다.
"""
import argparse
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bulk

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
UP_DIR = os.path.join(HERE, ".uploads")
JOBS = {}
LOCK = threading.Lock()


def safe_stem(name):
    name = os.path.basename((name or "wiki").replace("\\", "/"))
    stem = re.sub(r"(\.xml)?\.(zip|db|sqlite3?|xml|gz|bz2)$", "", name, flags=re.I) or "wiki"
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", stem)[:80]


def run_job(job_id, path, src, dst, stem, cleanup=None, fnencode="url"):
    job = JOBS[job_id]
    out = os.path.join(OUT_DIR, f"{stem}-{dst}.zip")
    if os.path.exists(out):
        out = os.path.join(OUT_DIR, f"{stem}-{dst}-{time.strftime('%H%M%S')}.zip")

    def progress(done, total):
        job["done"], job["total"] = done, total
    try:
        rep = bulk.convert_archive(path, src, dst, out, progress, doku_fnencode=fnencode)
        job.update(state="done", report=rep.as_dict(), file=os.path.basename(out), path=out)
    except Exception as e:  # 사용자에게 이유를 보여 준다
        if not isinstance(e, ValueError):
            traceback.print_exc()
        job.update(state="error", error=str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}")
    finally:
        if cleanup and os.path.exists(cleanup):
            os.remove(cleanup)


def start_job(path, src, dst, stem, cleanup=None, fnencode="url"):
    if fnencode not in ("url", "utf-8"):
        raise ValueError("도쿠위키 파일 이름 방식은 url 이나 utf-8 이어야 합니다")
    src, dst = bulk.norm_fmt(src), bulk.norm_fmt(dst)
    if src == dst:
        raise ValueError("읽는 형식과 만들 형식이 같습니다")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "done": 0, "total": None, "src": src, "dst": dst}
    threading.Thread(target=run_job, args=(job_id, path, src, dst, stem, cleanup, fnencode), daemon=True).start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    server_version = "WikiConverter"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- 응답 도우미
    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def fail(self, msg, code=400):
        self.send_json({"error": msg}, code)

    # ---- 안전: 내 컴퓨터 화면에서 온 요청만 받는다(다른 웹사이트가 몰래 부르지 못하게)
    def allowed(self, post):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        if post:
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                return False
            if self.headers.get("X-Requested-With") != "wiki-converter":
                return False
        return True

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 << 20:
            raise ValueError("글이 너무 깁니다(20MB 넘음). 대량 변환을 쓰세요")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    # ---- GET
    def do_GET(self):
        if not self.allowed(False):
            return self.fail("허용되지 않은 주소입니다", 403)
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "ui.html"), "rb") as f:
                return self.send_bytes(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/job":
            job = JOBS.get((q.get("id") or [""])[0])
            return self.send_json({k: v for k, v in job.items() if k != "path"}) if job else self.fail("없는 작업입니다", 404)
        if u.path == "/api/download":
            job = JOBS.get((q.get("id") or [""])[0])
            if not job or job.get("state") != "done":
                return self.fail("받을 파일이 없습니다", 404)
            size = os.path.getsize(job["path"])
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(job["file"]))
            self.end_headers()
            with open(job["path"], "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return None
        return self.fail("없는 주소입니다", 404)

    # ---- POST
    def do_POST(self):
        if not self.allowed(True):
            return self.fail("허용되지 않은 요청입니다", 403)
        u = urllib.parse.urlparse(self.path)
        try:
            if u.path == "/api/text":
                d = self.read_json()
                text, dst = d.get("text", ""), d.get("dst", "")
                src = d.get("src") or "auto"
                detected = None
                if src == "auto":
                    detected, _ = bulk.guess_format(text)
                    src = detected or "markdown"
                result, raw = bulk.convert_text(text, src, dst, d.get("title", ""))
                return self.send_json({"result": result, "src": bulk.norm_fmt(src), "detected": detected, "raw": raw})
            if u.path == "/api/bulk-path":
                d = self.read_json()
                path = (d.get("path") or "").strip().strip('"')
                if not path or not os.path.exists(path):
                    return self.fail("그런 파일이나 폴더가 없습니다: " + path)
                return self.send_json({"id": start_job(path, d.get("src"), d.get("dst"), safe_stem(path), fnencode=d.get("fnencode") or "url")})
            if u.path == "/api/bulk":
                q = urllib.parse.parse_qs(u.query)
                name = (q.get("name") or ["wiki"])[0]
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0:
                    return self.fail("파일이 비어 있습니다")
                os.makedirs(UP_DIR, exist_ok=True)
                ext = os.path.splitext(name)[1].lower()
                if name.lower().endswith((".xml.gz", ".xml.bz2")):
                    ext = ".xml" + ext
                tmp = os.path.join(UP_DIR, uuid.uuid4().hex[:10] + (ext if re.fullmatch(r"(\.xml)?\.\w{1,5}", ext) else ""))
                left = n
                with open(tmp, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                if left:
                    os.remove(tmp)
                    return self.fail("업로드가 중간에 끊겼습니다")
                try:
                    jid = start_job(tmp, (q.get("src") or [""])[0], (q.get("dst") or [""])[0], safe_stem(name), cleanup=tmp,
                                  fnencode=(q.get("fnencode") or ["url"])[0])
                except ValueError:
                    os.remove(tmp)
                    raise
                return self.send_json({"id": jid})
        except ValueError as e:
            return self.fail(str(e))
        except Exception as e:
            traceback.print_exc()
            return self.fail(f"{type(e).__name__}: {e}", 500)
        return self.fail("없는 주소입니다", 404)


def free_port(start):
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("빈 포트를 찾지 못했습니다")


def main():
    ap = argparse.ArgumentParser(description="위키 변환기")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    shutil.rmtree(UP_DIR, ignore_errors=True)  # 지난번에 남은 올린 파일 정리
    port = free_port(a.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"위키 변환기: {url}  (끄려면 이 창에서 Ctrl+C)")
    print(f"변환 결과는 {OUT_DIR} 에도 저장됩니다.")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
