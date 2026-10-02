"""웹 화면(app.py)의 요청 처리 시험: 서버를 잠깐 띄워 문서 변환·업로드 대량 변환·경로 변환·보안 검사를 해 본다."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

H = {"X-Requested-With": "wiki-converter"}


def call(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = dict(headers or {})
    if host:
        h["Host"] = host
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def jpost(port, path, obj, headers=None):
    st, d = call(port, "POST", path, json.dumps(obj).encode("utf-8"), dict(H, **{"Content-Type": "application/json"}, **(headers or {})))
    return st, json.loads(d)


def wait(port, jid):
    for _ in range(100):
        st, d = call(port, "GET", "/api/job?id=" + jid)
        j = json.loads(d)
        if j["state"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("작업이 끝나지 않음")


def main():
    tmp = tempfile.mkdtemp(prefix="wikiconv-app-")
    app.OUT_DIR = os.path.join(tmp, "output")
    app.UP_DIR = os.path.join(tmp, "up")
    os.makedirs(app.OUT_DIR)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, body = call(port, "GET", "/")
        assert st == 200 and "위키 변환기" in body.decode("utf-8")

        # 문서 변환(자동 감지)
        st, j = jpost(port, "/api/text", {"text": "====== 제목 ======\n**굵게** //기울임//", "src": "auto", "dst": "mediawiki"})
        assert st == 200 and j["detected"] == "dokuwiki" and "'''굵게'''" in j["result"], j
        st, j = jpost(port, "/api/text", {"text": "x", "src": "markdown", "dst": "nope"})
        assert st == 400 and "형식" in j["error"], j

        # 보안: 요청 머리글 없이/다른 Host 로는 거절
        st, _ = call(port, "POST", "/api/text", b"{}", {"Content-Type": "application/json"})
        assert st == 403, st
        st, _ = call(port, "GET", "/", host="evil.example.com")
        assert st == 403, st
        st, _ = call(port, "POST", "/api/text", b"{}", dict(H, Origin="http://evil.example.com"))
        assert st == 403, st

        # 업로드 대량 변환: 도쿠위키 zip → 마크다운
        zpath = os.path.join(tmp, "내 위키.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("data/pages/start.txt", "====== 시작 ======\n**안녕** [[다른글]]\n")
            z.writestr("data/pages/다른글.txt", "다른 글입니다\n")
            z.writestr("data/media/a.png", b"png")
        st, d = call(port, "POST", "/api/bulk?src=dokuwiki&dst=markdown&name=" + "%EB%82%B4%20%EC%9C%84%ED%82%A4.zip",
                     open(zpath, "rb").read(), dict(H, **{"Content-Type": "application/octet-stream"}))
        assert st == 200, d
        j = wait(port, json.loads(d)["id"])
        assert j["state"] == "done" and j["report"]["pages"] == 2 and j["report"]["media"] == 1, j
        assert j["file"].startswith("내 위키-markdown"), j["file"]
        st, data = call(port, "GET", "/api/download?id=" + json.loads(d)["id"])
        assert st == 200 and data[:2] == b"PK"
        assert not os.listdir(app.UP_DIR), "올린 임시 파일이 지워지지 않음"

        # 경로 대량 변환(폴더): 마크다운 폴더 → 오픈나무
        folder = os.path.join(tmp, "md")
        os.makedirs(folder)
        open(os.path.join(folder, "문서.md"), "w", encoding="utf-8").write("# 제목\n**굵게**\n")
        st, j = jpost(port, "/api/bulk-path", {"path": '"' + folder + '"', "src": "markdown", "dst": "opennamu"})
        assert st == 200, j
        j = wait(port, j["id"])
        assert j["state"] == "done" and j["report"]["pages"] == 1, j

        # 잘못된 입력은 이유를 알려 준다
        st, j = jpost(port, "/api/bulk-path", {"path": folder, "src": "markdown", "dst": "markdown"})
        assert st == 400 and "같" in j["error"], j
        st, j = jpost(port, "/api/bulk-path", {"path": os.path.join(tmp, "없음.zip"), "src": "dokuwiki", "dst": "markdown"})
        assert st == 400, j
        st, j = jpost(port, "/api/bulk-path", {"path": folder, "src": "dokuwiki", "dst": "markdown"})
        j = wait(port, j["id"])
        assert j["state"] == "error" and "도쿠위키" in j["error"], j
    finally:
        srv.shutdown()
    print("test_app: 통과")


if __name__ == "__main__":
    main()
