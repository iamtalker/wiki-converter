"""위키나루 — 위키 문법으로 쓰면서 옆에서 바로 보고, 다른 문법으로 바꿔 저장하는 창 프로그램(표준 라이브러리 tkinter).

    pythonw 위키나루.pyw [파일]      (콘솔 창 없이)      또는   python editor.py [파일]
"""
import json
import math
import os
import re
import sys
import webbrowser

FROZEN = bool(getattr(sys, "frozen", False))   # PyInstaller 로 묶은 exe 로 실행 중인가
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))        # 코드·아이콘이 있는 곳
APP_DIR = os.path.dirname(sys.executable) if FROZEN else HERE                              # exe(또는 프로그램 폴더)가 있는 곳
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bulk  # noqa: E402
import preview  # noqa: E402
import wikiconv  # noqa: E402
from wikiconv.markdown import md_name  # noqa: E402

APP = "위키나루"
PROG_ID = "WikiNaru.App"
OLD_PROG_IDS = ("WikiEditor.App", "WikiConverter.Editor")   # 예전 이름으로 등록돼 있던 것(등록·해제할 때 같이 정리)
CLASSES = r"Software\Classes"
OPEN_EXTS = (".md", ".markdown", ".txt")
SYNTAXES = [("markdown", "마크다운"), ("dokuwiki", "도쿠위키"), ("mediawiki", "미디어위키"), ("opennamu", "오픈나무(나무마크)")]
LABEL = dict(SYNTAXES)
# 두 패널의 드롭박스 항목: 문법 4가지 + 렌더링(읽기 전용 모양 보기)
MODES = [("rendered", "렌더링"), ("markdown", "마크다운"), ("opennamu", "오픈나무(나무마크)"), ("dokuwiki", "도쿠위키"),
         ("mediawiki", "미디어위키")]
MODE_LABEL = dict(MODES)
COMBO_SEP = "─" * 16   # 렌더링과 문법 사이의 구분선(고를 수 없음)


def combo_label(mode):
    """드롭박스에 보이는 글: 렌더링은 ◉ 표시와 '읽기 전용'을 붙여 문법들과 구별한다."""
    return "◉ 렌더링 (읽기 전용)" if mode == "rendered" else MODE_LABEL[mode]


COMBO_VALUES = [combo_label("rendered"), COMBO_SEP] + [MODE_LABEL[k] for k, _ in MODES if k != "rendered"]


def mode_from_label(text):
    """드롭박스 글 → 모드(구분선이면 None). 예전 글('렌더링')도 받는다."""
    if text == COMBO_SEP:
        return None
    if text.startswith("◉") or text == "렌더링":
        return "rendered"
    return next(k for k, v in MODES if v == text)
DEBOUNCE_MS = 150
EXT ={"markdown": ".md", "dokuwiki": ".txt", "mediawiki": ".wiki", "opennamu": ".txt"}
FILETYPES = [("위키·마크다운 문서", "*.md *.markdown *.txt *.wiki"), ("모든 파일", "*.*")]


# ================================================================ 파일 읽기·쓰기 도우미 (화면 없이 시험할 수 있게 분리)
def decode_file(data):
    """바이트 → (글, 인코딩, 줄바꿈). 글은 항상 \\n 줄바꿈."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        enc, text = "utf-8", data.decode("utf-8", "replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), ("utf-8" if enc == "utf-8-sig" else enc), nl


def encode_file(text, enc="utf-8", nl="\n"):
    text = text.replace("\n", nl) if nl != "\n" else text
    try:
        return text.encode(enc)
    except UnicodeEncodeError:
        return text.encode("utf-8")


def detect_format(text, path=""):
    """파일 이름과 내용으로 어느 문법인지 짐작한다(확실하지 않으면 마크다운)."""
    fmt, _ = bulk.guess_format(text)
    if os.path.splitext(path)[1].lower() in (".md", ".markdown"):
        return "markdown"
    return fmt or "markdown"


IMG_MAX_W = 520                       # 렌더링 창에 보이는 그림의 최대 너비(픽셀)
IMG_MAX_BYTES = 20 * 1024 * 1024      # 외부 그림 하나의 최대 크기
IMG_CLSID = {"png": "557cf406-1a04-11d3-9a73-0000f81ef32e", "jpeg": "557cf401-1a04-11d3-9a73-0000f81ef32e"}
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def gdip_convert(src, dst, kind="png", max_w=None):
    """윈도 GDI+ 로 JPEG·PNG·GIF·BMP 그림을 읽어 PNG(또는 JPEG)로 저장한다(너비가 max_w 보다 크면 줄임). ctypes 만 사용.
    tkinter 는 JPEG 를 못 읽어서, 화면에 보이려면 PNG 로 바꿔야 한다."""
    import ctypes
    import uuid
    if sys.platform != "win32":
        raise OSError("이 운영체제에서는 PNG·GIF 그림만 볼 수 있습니다")
    gdip = ctypes.WinDLL("gdiplus")
    vp, cu = ctypes.c_void_p, ctypes.c_uint
    gdip.GdipLoadImageFromFile.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(vp)]
    gdip.GdipGetImageWidth.argtypes = [vp, ctypes.POINTER(cu)]
    gdip.GdipGetImageHeight.argtypes = [vp, ctypes.POINTER(cu)]
    gdip.GdipGetImageThumbnail.argtypes = [vp, cu, cu, ctypes.POINTER(vp), vp, vp]
    gdip.GdipSaveImageToFile.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_char_p, vp]
    gdip.GdipDisposeImage.argtypes = [vp]
    gdip.GdiplusShutdown.argtypes = [ctypes.c_size_t]

    class StartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", vp),
                    ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]
    token = ctypes.c_size_t()
    if gdip.GdiplusStartup(ctypes.byref(token), ctypes.byref(StartupInput(1, None, 0, 0)), None) != 0:
        raise OSError("그림 라이브러리를 시작하지 못했습니다")
    img, thumb = vp(), vp()
    try:
        if gdip.GdipLoadImageFromFile(src, ctypes.byref(img)) != 0 or not img.value:
            raise OSError("그림을 읽지 못했습니다(지원하지 않는 형식이거나 깨진 파일)")
        w, h = cu(), cu()
        gdip.GdipGetImageWidth(img, ctypes.byref(w))
        gdip.GdipGetImageHeight(img, ctypes.byref(h))
        target = img
        if max_w and w.value > max_w:
            if gdip.GdipGetImageThumbnail(img, max_w, max(1, h.value * max_w // w.value), ctypes.byref(thumb),
                                          None, None) == 0:
                target = thumb
        if gdip.GdipSaveImageToFile(target, dst, uuid.UUID(IMG_CLSID[kind]).bytes_le, None) != 0:
            raise OSError("그림을 저장하지 못했습니다")
    finally:
        if thumb.value:
            gdip.GdipDisposeImage(thumb)
        if img.value:
            gdip.GdipDisposeImage(img)
        gdip.GdiplusShutdown(token.value)


def image_cache_dir():
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "wikinaru-img")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_image(url, cache_dir=None, max_w=IMG_MAX_W, timeout=12):
    """http(s) 주소의 그림을 내려받아 화면에 쓸 PNG 파일 경로를 돌려준다(캐시). 실패하면 OSError 등을 던진다."""
    import hashlib
    import urllib.parse
    import urllib.request
    if not re.match(r"(?i)^https?://", url):
        raise ValueError("http·https 주소만 불러옵니다")
    cache_dir = cache_dir or image_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    out = os.path.join(cache_dir, key + ".png")
    if os.path.isfile(out):
        return out
    u = urllib.parse.urlsplit(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WikiNaru)", "Accept": "image/*,*/*;q=0.8",
                                               "Referer": f"{u.scheme}://{u.netloc}/"})   # 그림 서버가 막지 않게
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(IMG_MAX_BYTES + 1)
    if not data:
        raise OSError("빈 응답입니다")
    if len(data) > IMG_MAX_BYTES:
        raise OSError("그림이 너무 큽니다(20MB 초과)")
    raw = os.path.join(cache_dir, key + ".raw")
    with open(raw, "wb") as f:
        f.write(data)
    try:
        if sys.platform == "win32":
            gdip_convert(raw, out, "png", max_w)
        elif data[:8] == PNG_SIG:
            os.replace(raw, out)
        else:
            raise OSError("이 운영체제에서는 PNG·GIF 그림만 볼 수 있습니다")
    finally:
        if os.path.exists(raw):
            os.remove(raw)
    return out


def local_image_png(path, cache_dir=None, max_w=IMG_MAX_W):
    """JPEG·BMP 같은 파일 그림을 화면에 쓸 PNG 로(캐시)."""
    import hashlib
    cache_dir = cache_dir or image_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()[:20]
    out = os.path.join(cache_dir, key + ".png")
    if not os.path.isfile(out):
        gdip_convert(path, out, "png", max_w)
    return out


def find_note(base_dir, page, depth=3):
    """링크 대상 문서가 같은 폴더(또는 아래 폴더)에 있으면 그 경로."""
    want = md_name(page).lower()
    direct = os.path.join(base_dir, md_name(page))
    if os.path.isfile(direct):
        return direct
    base_len = len(base_dir.rstrip(os.sep).split(os.sep))
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if len(root.split(os.sep)) - base_len >= depth:
            dirs[:] = []
        for f in files:
            if f.lower() == want:
                return os.path.join(root, f)
    return None


# ================================================================ 서식 도구 (문법마다 표기가 달라 표로 둔다)
WRAP = {  # 글을 감싸는 서식: (앞, 뒤)
    "markdown": {"b": ("**", "**"), "i": ("*", "*"), "s": ("~~", "~~"), "code": ("`", "`"),
                 "link": ("[", "](주소)"), "img": ("![", "](그림.png)")},
    "dokuwiki": {"b": ("**", "**"), "i": ("//", "//"), "s": ("<del>", "</del>"), "code": ("''", "''"),
                 "link": ("[[", "]]"), "img": ("{{", "}}"), "fn": ("((", "))")},
    "mediawiki": {"b": ("'''", "'''"), "i": ("''", "''"), "s": ("<s>", "</s>"), "code": ("<code>", "</code>"),
                  "link": ("[[", "]]"), "img": ("[[File:", "]]"), "quote": ("<blockquote>", "</blockquote>"), "fn": ("<ref>", "</ref>")},
    "opennamu": {"b": ("'''", "'''"), "i": ("''", "''"), "s": ("~~", "~~"), "code": ("{{{", "}}}"),
                 "link": ("[[", "]]"), "img": ("[[파일:", "]]"), "fn": ("[* ", "]")},
}
WRAP_DEFAULT = {"b": "굵은 글", "i": "기울인 글", "s": "취소선", "code": "코드", "link": "링크 글", "img": "설명", "quote": "인용",
                "fn": "각주 내용"}
LINE = {  # 줄 앞에 붙이는 서식: (붙일 글, 이미 붙었는지 보는 식). {n} 은 번호
    "markdown": {"ul": ("- ", r"^[-*+] "), "ol": ("{n}. ", r"^\d+\. "), "quote": ("> ", r"^> ?")},
    "dokuwiki": {"ul": ("  * ", r"^ +\* "), "ol": ("  - ", r"^ +- "), "quote": ("> ", r"^>+ ?")},
    "mediawiki": {"ul": ("* ", r"^\*+ "), "ol": ("# ", r"^#+ ")},
    "opennamu": {"ul": (" * ", r"^ +\* "), "ol": (" 1. ", r"^ +1\. "), "quote": ("> ", r"^>+ ?")},
}
BLOCK = {  # 한 덩어리 넣기
    "hr": {"markdown": "---", "dokuwiki": "----", "mediawiki": "----", "opennamu": "----"},
    "codeblock": {"markdown": "```\n{}\n```", "dokuwiki": "<code>\n{}\n</code>",
                  "mediawiki": "<pre>\n{}\n</pre>", "opennamu": "{{{\n{}\n}}}"},
    "table": {"markdown": "| 제목1 | 제목2 |\n| --- | --- |\n| 내용 | 내용 |",
              "dokuwiki": "^ 제목1 ^ 제목2 ^\n| 내용 | 내용 |",
              "mediawiki": '{| class="wikitable"\n! 제목1 !! 제목2\n|-\n| 내용 || 내용\n|}',
              "opennamu": "|| 제목1 || 제목2 ||\n|| 내용 || 내용 ||"},
}
HEADING_RE = {"markdown": r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$",
              "dokuwiki": r"^\s*={2,6}\s*(.*?)\s*={2,6}\s*$", "mediawiki": r"^\s*={1,6}\s*(.*?)\s*={1,6}\s*$",
              "opennamu": r"^\s*={1,6}\s*(.*?)\s*={1,6}\s*$"}


def wrap_text(sel, before, after, default=""):
    """선택한 글을 서식으로 감싼다(이미 감싸져 있으면 벗긴다). (새 글, 고를 시작, 고를 끝) 을 돌려준다."""
    if len(sel) >= len(before) + len(after) and sel.startswith(before) and sel.endswith(after) and (before or after):
        inner = sel[len(before):len(sel) - len(after)]
        return inner, 0, len(inner)
    inner = sel or default
    out = before + inner + after
    for token in ("주소", "그림.png"):  # 주소 자리는 바로 고쳐 쓰게 골라 둔다
        if token in after and sel:
            s = len(before) + len(inner) + after.index(token)
            return out, s, s + len(token)
    return out, len(before), len(before) + len(inner)


def toggle_prefix(lines, template, strip_re):
    """줄들 앞에 목록·인용 표시를 붙인다(모두 이미 붙어 있으면 뗀다)."""
    import re as _re
    body = [ln for ln in lines if ln.strip()]
    if body and all(_re.match(strip_re, ln) for ln in body):
        return [_re.sub(strip_re, "", ln, count=1) if ln.strip() else ln for ln in lines]
    out, n = [], 0
    for ln in lines:
        if ln.strip():
            n += 1
            out.append(template.format(n=n) + _re.sub(strip_re, "", ln, count=1))
        else:
            out.append(ln)
    return out


def set_heading(line, level, fmt):
    """한 줄을 제목 level(1~4, 0 은 본문)으로. 원래 제목 표시는 먼저 걷어 낸다."""
    import re as _re
    m = _re.match(HEADING_RE[fmt], line)
    body = (m.group(1) if m else line).strip()
    if level <= 0 or not body:
        return body
    if fmt == "markdown":
        return "#" * level + " " + body
    eq = "=" * (7 - level) if fmt == "dokuwiki" else "=" * level
    return f"{eq} {body} {eq}"


def syntax_entries(fmt):
    """사이드바에 나열할 문법: [(묶음, 이름, 쓰는 모양, 동작)].
    동작 = ("wrap", 키) · ("heading", 단계) · ("line", 키) · ("block", 키)  — 툴바 버튼과 같은 동작이다."""
    w, ln = WRAP[fmt], LINE[fmt]
    out = [("제목", f"제목 {lv}", set_heading("제목", lv, fmt), ("heading", lv)) for lv in (1, 2, 3, 4)]
    for key, name in (("b", "굵게"), ("i", "기울임"), ("s", "취소선"), ("code", "코드")):
        out.append(("글자", name, w[key][0] + "글" + w[key][1], ("wrap", key)))
    for kind, name in (("ul", "글머리 목록"), ("ol", "번호 목록")):
        out.append(("목록·인용", name, ln[kind][0].format(n=1) + "항목", ("line", kind)))
    if "quote" in w:
        out.append(("목록·인용", "인용", w["quote"][0] + "글" + w["quote"][1], ("wrap", "quote")))
    elif "quote" in ln:
        out.append(("목록·인용", "인용", ln["quote"][0] + "글", ("line", "quote")))
    for key, name in (("link", "링크"), ("img", "그림"), ("fn", "각주")):
        if key in w:
            out.append(("넣기", name, w[key][0] + "글" + w[key][1], ("wrap", key)))
    first = lambda s: s.split("\n")[0] + (" …" if "\n" in s else "")  # noqa: E731
    out.append(("넣기", "표", first(BLOCK["table"][fmt]), ("block", "table")))
    out.append(("넣기", "가로줄", BLOCK["hr"][fmt], ("block", "hr")))
    out.append(("넣기", "코드 블록", first(BLOCK["codeblock"][fmt].replace("{}", "코드", 1)), ("block", "codeblock")))
    return out


PLACEHOLDER = {"ul": "항목", "ol": "항목", "quote": "인용문"}   # 빈 줄에서 목록·인용을 눌렀을 때 넣을 글
BIG_DOC = 40_000   # 이보다 큰 글은 변환을 백그라운드 스레드에서(타이핑이 끊기지 않게)
THEME = {"bg": "#f8fafc", "card": "#ffffff", "line": "#e2e8f0", "text": "#0f172a", "muted": "#64748b",
         "accent": "#7c3aed", "soft": "#ede9fe", "sel": "#ddd6fe", "code": "#f1f5f9",
         "focus": "#000000"}   # 슬레이트 + 바이올렛 하나(강조), 포커스된 창 테두리는 검정


def build_cache(text, fmt, title, modes):
    """글을 한 번만 읽어 화면 조각·원문 보존 수·요청한 문법별 변환 결과를 만든다(순수 함수, 스레드에서도 안전)."""
    doc = wikiconv.parse(text, bulk.SYNTAX[fmt], title)
    out = {m: (text if m == fmt else wikiconv.render(doc, bulk.SYNTAX[m], title)) for m in modes}
    return {"key": (text, fmt, title), "segs": preview.layout(doc), "raw": bulk.count_raw(doc.blocks), "out": out}


def default_bulk_out(path, dst):
    stem = os.path.splitext(os.path.basename(os.path.normpath(path)))[0] or "wiki"
    return os.path.join(APP_DIR, "output", f"{stem}-{dst}.zip")


# 편집기 설정(사이드바 켜고 끄기 등) — 사용자 폴더에 작은 JSON 하나
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".wiki-naru.json")


DEFAULT_SETTINGS_PATH = SETTINGS_PATH
LEGACY_SETTINGS = (".wiki-editor.json", ".wiki-converter-editor.json")   # 예전 이름의 설정 파일


def load_settings():
    paths = [SETTINGS_PATH]
    if SETTINGS_PATH == DEFAULT_SETTINGS_PATH:   # 기본 위치일 때만 예전 설정 파일을 이어받는다
        paths += [os.path.join(os.path.expanduser("~"), n) for n in LEGACY_SETTINGS]
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


# ================================================================ Windows: '열기 방법' 목록에 등록
def icon_path(name):
    return os.path.join(HERE, "assets", name)


def launcher_command():
    if FROZEN:   # 단독 exe: 자기 자신을 연다
        return f'"{sys.executable}" "%1"'
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w
    return f'"{exe}" "{os.path.join(HERE, "위키나루.pyw")}" "%1"'


def _drop_progids(winreg, root, ids):
    for pid in ids:
        for ext in OPEN_EXTS:
            try:
                with winreg.OpenKey(root, CLASSES + "\\" + ext + r"\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, pid)
            except OSError:
                pass
        base = CLASSES + "\\" + pid
        for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
            try:
                winreg.DeleteKey(root, base + sub)
            except OSError:
                pass


def register_open_with():
    """현재 사용자 범위(HKCU)에만 쓴다. 기본 프로그램을 바꾸지는 않고 '열기 방법' 목록에 이 프로그램을 올린다."""
    import winreg
    root = winreg.HKEY_CURRENT_USER
    _drop_progids(winreg, root, OLD_PROG_IDS)
    base = CLASSES + "\\" + PROG_ID
    with winreg.CreateKey(root, base) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, APP)
        winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, APP)
    with winreg.CreateKey(root, base + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launcher_command())
    for ext in OPEN_EXTS:
        with winreg.CreateKey(root, CLASSES + "\\" + ext + r"\OpenWithProgids") as k:
            winreg.SetValueEx(k, PROG_ID, 0, winreg.REG_NONE, b"")
    _notify_shell()


def unregister_open_with():
    import winreg
    _drop_progids(winreg, winreg.HKEY_CURRENT_USER, (PROG_ID,) + OLD_PROG_IDS)
    _notify_shell()


def _notify_shell():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


# ================================================================ 창
def run_gui(argv=None, loop=True):
    import queue
    import threading
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk

    C = THEME

    class Panel:
        """패널 하나: 위쪽 드롭박스(문법 또는 렌더링) + 글쓰기 창 / 렌더링 창."""

        def __init__(self, ed, parent, mode):
            self.ed, self.mode, self.last_text = ed, mode, ""
            self.links, self.images = {}, []
            self.frame = tk.Frame(parent, bg=C["bg"])
            head = tk.Frame(self.frame, bg=C["bg"], padx=8, pady=6)
            head.pack(side="top", fill="x")
            self.combo = ttk.Combobox(head, state="readonly", width=22, font=(ed.ui, 10), values=COMBO_VALUES,
                                      postcommand=self.style_popdown)
            self.combo.pack(side="left")
            self.badge = tk.Label(head, text="", font=(ed.ui, 9), fg=C["muted"], bg=C["bg"])
            self.badge.pack(side="left", padx=10)
            body = tk.Frame(self.frame, bg=C["card"])
            body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            ring = dict(highlightthickness=2, highlightbackground=C["line"], highlightcolor=C["focus"], borderwidth=0)
            self.edit_frame = tk.Frame(body, bg=C["card"])
            self.text = tk.Text(self.edit_frame, wrap="word", undo=True, maxundo=-1, font=(ed.mono, ed.size), padx=10,
                                pady=8, tabs=("1c",), bg=C["card"], fg=C["text"], insertbackground=C["accent"],
                                selectbackground=C["sel"], selectforeground=C["text"], **ring)
            sb = tk.Scrollbar(self.edit_frame, command=self.text.yview)
            self.text.config(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.text.pack(side="left", fill="both", expand=True)
            self.view_frame = tk.Frame(body, bg=C["card"])
            self.view = tk.Text(self.view_frame, wrap="word", state="disabled", font=(ed.ui, ed.size), padx=14, pady=10,
                                cursor="arrow", bg=C["card"], fg=C["text"], selectbackground=C["sel"],
                                selectforeground=C["text"], **ring)
            sb2 = tk.Scrollbar(self.view_frame, command=self.view.yview)
            self.view.config(yscrollcommand=sb2.set)
            sb2.pack(side="right", fill="y")
            self.view.pack(side="left", fill="both", expand=True)

        def editable(self):
            return self.mode != "rendered"

        def get_text(self):
            return self.text.get("1.0", "end-1c")

        def show(self):
            self.edit_frame.pack_forget()
            self.view_frame.pack_forget()
            (self.edit_frame if self.editable() else self.view_frame).pack(fill="both", expand=True)
            self.combo.set(combo_label(self.mode))
            self.badge.config(text="편집" if self.editable() else "읽기 전용")

        def style_popdown(self):
            """펼친 목록에서 구분선은 흐리게(목록이 열린 직후에 적용). 색으로 강조하지 않는다."""
            def apply():
                try:
                    pop = self.combo.tk.call("ttk::combobox::PopdownWindow", str(self.combo))
                    lb = f"{pop}.f.l"
                    self.combo.tk.call(lb, "itemconfigure", 1, "-foreground", C["line"], "-background", C["card"],
                                       "-selectbackground", C["card"], "-selectforeground", C["line"])
                except Exception:   # 목록 위젯 구조가 다른 Tk 에서는 표시만 생략
                    pass
            self.combo.after(1, apply)

    class BulkDialog:
        """파일 > 대량 변환…: 위키 데이터(zip·폴더·파일)를 통째로 다른 위키 형식의 zip 으로."""
        HINT = {
            "dokuwiki": "data/pages 와 data/media 가 들어 있는 data 폴더(또는 그것을 묶은 zip)",
            "mediawiki": "Special:Export 로 받은 XML(.xml, .xml.gz). 이미지 폴더를 같이 zip 에 넣으면 이미지도 옮김",
            "opennamu": "openNAMU 의 data.db 파일(이미지 폴더와 함께 zip 으로 묶어도 됨)",
            "markdown": ".md 파일들이 들어 있는 폴더 또는 zip(그림 파일도 같이 옮김)",
        }

        def __init__(self, ed):
            self.ed, self.running, self.q = ed, False, queue.Queue()
            self.top = top = tk.Toplevel(ed.root)
            top.title("대량 변환")
            top.configure(bg=C["bg"])
            top.transient(ed.root)
            top.resizable(False, False)
            top.protocol("WM_DELETE_WINDOW", self.close)
            f = tk.Frame(top, bg=C["bg"], padx=18, pady=14)
            f.pack(fill="both", expand=True)
            self.src = tk.StringVar(value=LABEL["dokuwiki"])
            self.dst = tk.StringVar(value=LABEL["mediawiki"])
            self.path = tk.StringVar()
            self.out = tk.StringVar()
            self.enc = tk.StringVar(value="url")
            self.out_touched = False
            fnt = (ed.ui, 10)

            def row(r, label):
                tk.Label(f, text=label, anchor="w", font=fnt, bg=C["bg"], fg=C["text"]).grid(row=r, column=0, sticky="w",
                                                                                           pady=5, padx=(0, 12))

            row(0, "원본 형식")
            self.src_combo = ttk.Combobox(f, state="readonly", width=22, font=fnt, textvariable=self.src,
                                          values=[v for _, v in SYNTAXES])
            self.src_combo.grid(row=0, column=1, sticky="w")
            self.hint = tk.Label(f, text="", justify="left", wraplength=470, font=(ed.ui, 9), fg=C["muted"], bg=C["bg"])
            self.hint.grid(row=1, column=1, columnspan=2, sticky="w")
            row(2, "원본 위치")
            tk.Entry(f, textvariable=self.path, width=48, relief="solid", bd=1, font=fnt).grid(row=2, column=1, sticky="w")
            bf = tk.Frame(f, bg=C["bg"])
            bf.grid(row=2, column=2, padx=(6, 0))
            tk.Button(bf, text="파일…", command=self.pick_file, relief="groove").pack(side="left")
            tk.Button(bf, text="폴더…", command=self.pick_dir, relief="groove").pack(side="left", padx=(4, 0))
            row(3, "만들 형식")
            self.dst_combo = ttk.Combobox(f, state="readonly", width=22, font=fnt, textvariable=self.dst,
                                          values=[v for _, v in SYNTAXES])
            self.dst_combo.grid(row=3, column=1, sticky="w")
            self.enc_label = tk.Label(f, text="도쿠위키 파일 이름", anchor="w", font=fnt, bg=C["bg"], fg=C["text"])
            self.enc_box = ttk.Combobox(f, state="readonly", width=34, font=fnt,
                                        values=["url  (기본값: 한글이 %EA%B0%80 로 저장)", "utf-8  (한글 이름 그대로)"])
            self.enc_box.set("url  (기본값: 한글이 %EA%B0%80 로 저장)")
            row(5, "저장 위치")
            tk.Entry(f, textvariable=self.out, width=48, relief="solid", bd=1, font=fnt).grid(row=5, column=1, sticky="w")
            tk.Button(f, text="찾기…", command=self.pick_out, relief="groove").grid(row=5, column=2, padx=(6, 0))
            self.go = tk.Button(f, text="변환 시작", command=self.start, bg=C["accent"], fg="white", relief="flat",
                                activebackground=C["accent"], activeforeground="white", font=(ed.ui, 10, "bold"), padx=16,
                                pady=5, cursor="hand2")
            self.go.grid(row=6, column=1, sticky="w", pady=(14, 4))
            tk.Button(f, text="닫기", command=self.close, relief="groove", padx=10, pady=4).grid(row=6, column=2, pady=(14, 4))
            self.bar = ttk.Progressbar(f, style="Violet.Horizontal.TProgressbar", mode="determinate", length=470)
            self.bar.grid(row=7, column=0, columnspan=3, sticky="we", pady=(4, 6))
            self.msg = tk.Label(f, text="", justify="left", anchor="w", wraplength=560, font=(ed.ui, 9), bg=C["bg"],
                                fg=C["text"])
            self.msg.grid(row=8, column=0, columnspan=3, sticky="w")
            self.open_btn = tk.Button(f, text="저장 폴더 열기", command=self.open_folder, relief="groove")
            self.src_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self.dst_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self.path.trace_add("write", lambda *a: self.refresh_out())
            self.out.trace_add("write", lambda *a: None)
            self.refresh()
            top.grab_set()

        def key(self, var):
            return next(k for k, v in SYNTAXES if v == var.get())

        def refresh(self):
            self.hint.config(text="넣을 것: " + self.HINT[self.key(self.src)])
            if self.key(self.dst) == "dokuwiki":
                self.enc_label.grid(row=4, column=0, sticky="w", pady=5, padx=(0, 12))
                self.enc_box.grid(row=4, column=1, columnspan=2, sticky="w")
            else:
                self.enc_label.grid_remove()
                self.enc_box.grid_remove()
            self.refresh_out()

        def refresh_out(self):
            if not self.out_touched or not self.out.get():
                self.out.set(default_bulk_out(self.path.get() or "wiki", self.key(self.dst)))
                self.out_touched = False

        def pick_file(self):
            p = filedialog.askopenfilename(parent=self.top, filetypes=[("위키 데이터", "*.zip *.xml *.gz *.bz2 *.db"),
                                                                         ("모든 파일", "*.*")])
            if p:
                self.path.set(p)

        def pick_dir(self):
            p = filedialog.askdirectory(parent=self.top)
            if p:
                self.path.set(p)

        def pick_out(self):
            p = filedialog.asksaveasfilename(parent=self.top, defaultextension=".zip", initialfile=os.path.basename(
                self.out.get()), filetypes=[("zip", "*.zip")])
            if p:
                self.out.set(p)
                self.out_touched = True

        def open_folder(self):
            d = os.path.dirname(os.path.abspath(self.out.get()))
            try:
                os.startfile(d)  # type: ignore[attr-defined]
            except AttributeError:
                webbrowser.open("file://" + d)

        def start(self):
            if self.running:
                return
            path, out = self.path.get().strip().strip('"'), self.out.get().strip()
            src, dst = self.key(self.src), self.key(self.dst)
            if not path or not os.path.exists(path):
                self.msg.config(text="원본 파일이나 폴더를 찾지 못했습니다. 위치를 확인해 주세요.", fg="#b91c1c")
                return
            if src == dst:
                self.msg.config(text="원본 형식과 만들 형식이 같습니다.", fg="#b91c1c")
                return
            self.out_touched = True
            enc = "utf-8" if self.enc_box.get().startswith("utf-8") else "url"
            self.running = True
            self.go.config(state="disabled", text="변환 중…")
            self.open_btn.grid_remove()
            self.bar.config(mode="indeterminate")
            self.bar.start(12)
            self.msg.config(text="변환하고 있습니다…", fg=C["text"])
            q = self.q

            def work():
                try:
                    def progress(done, total):
                        q.put(("progress", done, total))
                    rep = bulk.convert_archive(path, src, dst, out, progress, doku_fnencode=enc)
                    q.put(("done", rep))
                except Exception as e:  # noqa: BLE001  (ValueError·OSError·깨진 압축 파일 …)
                    q.put(("error", str(e)))
            threading.Thread(target=work, daemon=True).start()
            self.top.after(100, self.poll)

        def poll(self):
            try:
                while True:
                    kind, *rest = self.q.get_nowait()
                    if kind == "progress":
                        done, total = rest
                        if total:
                            self.bar.stop()
                            self.bar.config(mode="determinate", maximum=total, value=done)
                        self.msg.config(text=f"문서 {done:,}" + (f" / {total:,}" if total else "") + " 변환 중…")
                    elif kind == "done":
                        return self.finish(rest[0])
                    else:
                        return self.fail(rest[0])
            except queue.Empty:
                pass
            if self.running:
                self.top.after(100, self.poll)

        def end(self):
            self.running = False
            self.bar.stop()
            self.go.config(state="normal", text="변환 시작")

        def finish(self, rep):
            self.end()
            self.bar.config(mode="determinate", maximum=1, value=1)
            lines = [f"완료: 문서 {rep.pages:,}개 · 미디어 {rep.media_total:,}개 · {rep.seconds:.1f}초", rep.out]
            for k, v in rep.skipped.items():
                lines.append(f"건너뜀: {k} {v:,}개")
            if rep.raw:
                lines.append(f"옮길 수 없어 원문으로 남긴 곳 {rep.raw:,}곳 (문서 {rep.raw_pages:,}개)")
            if rep.media_missing:
                lines.append(f"파일을 찾지 못한 그림 {len(rep.media_missing)}개")
            if rep.failed:
                lines.append(f"오류가 난 문서 {len(rep.failed)}개(원문 그대로 담음): " + "; ".join(rep.failed[:3]))
            self.report = rep
            self.msg.config(text="\n".join(lines), fg=C["text"])
            self.open_btn.grid(row=9, column=1, sticky="w", pady=(8, 0))

        def fail(self, text):
            self.end()
            self.report = None
            self.msg.config(text="변환하지 못했습니다: " + text, fg="#b91c1c")

        def close(self):
            if self.running:
                messagebox.showinfo("대량 변환", "변환이 끝날 때까지 기다려 주세요.", parent=self.top)
                return
            self.top.grab_release()
            self.top.destroy()

    class Editor:
        def __init__(self, root, path=None):
            self.root = root
            self.path, self.enc, self.nl = None, "utf-8", "\n"
            self.file_fmt = None   # 파일 자신의 문법(열 때 짐작). 저장은 패널 선택과 상관없이 이 문법으로
            self.src_text, self.src_fmt = "", "markdown"      # 문서의 기준 글(마지막으로 쓴 패널의 글)과 그 문법
            self.saved_text, self.saved_fmt, self.dirty = "", "markdown", False
            self.after_id, self.pending = None, None
            self._c = {"key": None, "segs": [], "raw": 0, "out": {}}
            self.raw, self.gen = 0, 0
            self.imgs, self.skipped_urls = {}, set()    # 외부 그림 상태(주소 → ("pending",) · ("ok", PNG 경로) · ("fail", 이유))
            self.img_results = queue.Queue()
            self.jobs, self.results = queue.Queue(), queue.Queue()
            self.polling = False
            self.size = max(7, min(28, int(load_settings().get("size", 11) or 11)))
            self.active = None
            fams = set(tkfont.families())
            self.ui = next((f for f in ("맑은 고딕", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR") if f in fams),
                           tkfont.nametofont("TkDefaultFont").actual("family"))
            self.mono = next((f for f in ("D2Coding", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New") if f in fams),
                             "TkFixedFont")
            threading.Thread(target=self.worker, daemon=True).start()
            self.build()
            self.update_all()
            if path:
                self.open_path(path)
            root.protocol("WM_DELETE_WINDOW", self.quit)

        # ------------------------------------------------ 지금 편집 중인 패널 (서식 버튼·사이드바가 쓰는 곳)
        def visible_panels(self):
            if not hasattr(self, "panels"):
                return []
            names = [str(x) for x in self.pane.panes()]
            return [p for p in self.panels if str(p.frame) in names]

        @property
        def panel(self):
            vis = self.visible_panels()
            if self.active in vis and self.active.editable():
                return self.active
            return next((p for p in vis if p.editable()), None)

        @property
        def text(self):
            p = self.panel
            return p.text if p else None

        @property
        def fmt(self):
            p = self.panel
            return p.mode if p else self.src_fmt

        def ready(self):
            if self.panel is None:
                self.update_status("편집할 수 있는 패널이 없습니다. 패널의 드롭박스에서 문법을 고르세요")
                return False
            return True

        # ------------------------------------------------ 화면
        def build(self):
            r = self.root
            st = load_settings()
            r.geometry(self.saved_geometry(st))
            if st.get("zoomed"):
                try:
                    r.state("zoomed")
                except tk.TclError:
                    pass
            r.configure(bg=C["bg"])
            self.fmt_var = tk.StringVar(value=self.src_fmt)
            self.show_sidebar = tk.BooleanVar(value=bool(st.get("sidebar", True)))
            self.ext_images = tk.BooleanVar(value=bool(st.get("external_images", True)))
            self.view_mode = tk.StringVar(value=st.get("view") if st.get("view") in ("split", "preview", "source")
                                          else "split")
            self.build_style()
            self.build_menu()
            self.status = tk.Label(r, anchor="w", padx=12, pady=3, font=(self.ui, 9), bg=C["bg"], fg=C["muted"])
            self.status.pack(side="bottom", fill="x")
            tk.Frame(r, height=1, bg=C["line"]).pack(side="bottom", fill="x")
            self.build_toolbar()
            self.pane = tk.PanedWindow(r, orient="horizontal", sashwidth=5, bg=C["line"], borderwidth=0)
            self.pane.pack(fill="both", expand=True)
            self.build_sidebar()
            lm = st.get("left") if st.get("left") in MODE_LABEL else "markdown"
            rm = st.get("right") if st.get("right") in MODE_LABEL else "rendered"
            self.panels = [Panel(self, self.pane, lm), Panel(self, self.pane, rm)]
            if self.show_sidebar.get():
                self.pane.add(self.sidebar, minsize=230, width=330, stretch="never")
            for p in self.panels:
                p.show()
                p.combo.bind("<<ComboboxSelected>>", lambda e, p=p: self.pick_mode(p))
                p.text.bind("<<Modified>>", lambda e, p=p: self.on_modified(p))
                p.text.bind("<FocusIn>", lambda e, p=p: self.set_active(p))
                p.text.bind("<KeyRelease>", lambda e: self.update_status())
                p.text.bind("<ButtonRelease-1>", lambda e: self.update_status())
                p.view.bind("<Button-1>", lambda e, p=p: self.on_view_click(p, e))
                for seq, fn in (("<Control-n>", self.new), ("<Control-o>", self.open_dialog),
                                ("<Control-s>", self.save), ("<Control-S>", self.save_as),
                                ("<Control-f>", self.find_dialog), ("<Control-plus>", lambda: self.zoom(1)),
                                ("<Control-equal>", lambda: self.zoom(1)), ("<Control-minus>", lambda: self.zoom(-1)),
                                ("<Control-b>", lambda: self.wrap("b")), ("<Control-i>", lambda: self.wrap("i")),
                                ("<Control-k>", lambda: self.wrap("link"))):
                    p.text.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
            for seq, fn in (("<Control-n>", self.new), ("<Control-o>", self.open_dialog), ("<Control-s>", self.save),
                            ("<Control-S>", self.save_as), ("<Control-f>", self.find_dialog)):
                r.bind(seq, lambda e, fn=fn: fn())
            r.report_callback_exception = lambda *a: messagebox.showerror(APP, "오류가 났습니다:\n" + str(a[1]))
            self.make_fonts()
            for p in self.panels:
                self.configure_tags(p)
            self.apply_view_mode(persist=False)
            self.active = self.panel
            self.fill_sidebar()
            if self.panel:
                self.panel.text.focus_set()

        def build_style(self):
            st = ttk.Style()
            try:
                st.theme_use("clam")
            except tk.TclError:
                pass
            st.configure("TCombobox", fieldbackground=C["card"], background=C["card"], foreground=C["text"],
                         bordercolor=C["line"], lightcolor=C["card"], darkcolor=C["card"], arrowcolor=C["muted"],
                         padding=4)
            st.map("TCombobox", bordercolor=[("focus", C["accent"])], fieldbackground=[("readonly", C["card"])],
                   foreground=[("readonly", C["text"])], selectbackground=[("readonly", C["card"])],
                   selectforeground=[("readonly", C["text"])])
            st.configure("Syntax.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                         rowheight=26, borderwidth=0, font=(self.ui, 10))
            st.map("Syntax.Treeview", background=[("selected", C["soft"])], foreground=[("selected", C["text"])])
            st.configure("Syntax.Treeview.Heading", background=C["bg"], foreground=C["muted"], relief="flat",
                         font=(self.ui, 9, "bold"))
            st.configure("Violet.Horizontal.TProgressbar", troughcolor=C["line"], background=C["accent"],
                         bordercolor=C["line"], lightcolor=C["accent"], darkcolor=C["accent"])
            self.root.option_add("*TCombobox*Listbox.background", C["card"])
            self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
            self.root.option_add("*TCombobox*Listbox.selectBackground", C["soft"])
            self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

        def build_menu(self):
            r = self.root
            menu = tk.Menu(r)
            f = tk.Menu(menu, tearoff=0)
            f.add_command(label="새 문서", accelerator="Ctrl+N", command=self.new)
            f.add_command(label="열기…", accelerator="Ctrl+O", command=self.open_dialog)
            f.add_command(label="저장", accelerator="Ctrl+S", command=self.save)
            f.add_command(label="다른 이름으로 저장…", accelerator="Ctrl+Shift+S", command=self.save_as)
            f.add_separator()
            f.add_command(label="끝내기", command=self.quit)
            menu.add_cascade(label="파일", menu=f)
            e = tk.Menu(menu, tearoff=0)
            e.add_command(label="실행 취소", accelerator="Ctrl+Z", command=lambda: self.fire("<<Undo>>"))
            e.add_command(label="다시 실행", accelerator="Ctrl+Y", command=lambda: self.fire("<<Redo>>"))
            e.add_separator()
            e.add_command(label="찾기…", accelerator="Ctrl+F", command=self.find_dialog)
            e.add_command(label="모두 선택", accelerator="Ctrl+A",
                          command=lambda: self.text and self.text.tag_add("sel", "1.0", "end"))
            menu.add_cascade(label="편집", menu=e)
            s = tk.Menu(menu, tearoff=0)
            s.add_command(label="글은 그대로 두고, 이 글의 문법을 이렇게 읽기(짐작이 틀렸을 때)", state="disabled")
            s.add_separator()
            for k, v in SYNTAXES:
                s.add_radiobutton(label=v, value=k, variable=self.fmt_var, command=self.reinterpret)
            menu.add_cascade(label="원본 문법", menu=s)
            c = tk.Menu(menu, tearoff=0)
            c.add_command(label="대량 변환…", command=self.bulk_dialog)
            menu.add_cascade(label="변환", menu=c)
            v = tk.Menu(menu, tearoff=0)
            for val, name in (("split", "좌우 분할"), ("preview", "렌더링만"), ("source", "원문만")):
                v.add_radiobutton(label=name, value=val, variable=self.view_mode, command=self.apply_view_mode)
            v.add_separator()
            v.add_checkbutton(label="문법 해설 사이드바", variable=self.show_sidebar, command=self.toggle_sidebar)
            v.add_checkbutton(label="외부 이미지 불러오기 (그림 주소에 접속)", variable=self.ext_images,
                              command=self.toggle_ext_images)
            v.add_command(label="글자 크게", accelerator="Ctrl++", command=lambda: self.zoom(1))
            v.add_command(label="글자 작게", accelerator="Ctrl+-", command=lambda: self.zoom(-1))
            menu.add_cascade(label="보기", menu=v)
            h = tk.Menu(menu, tearoff=0)
            if sys.platform == "win32":
                h.add_command(label="'열기 방법' 목록에 등록", command=self.register)
                h.add_command(label="'열기 방법' 등록 해제", command=self.unregister)
                h.add_separator()
            h.add_command(label="정보", command=lambda: messagebox.showinfo(
                APP, "도쿠위키 · 미디어위키 · 오픈나무 · 마크다운 문법으로 쓰고,\n"
                     "다른 문법으로 바꿔 보여 주는 편집기입니다.\n내 컴퓨터 안에서만 동작합니다."))
            menu.add_cascade(label="도움말", menu=h)
            r.config(menu=menu)

        def fire(self, event):
            t = self.text
            if t is not None:
                t.event_generate(event)

        def bulk_dialog(self):
            self.flush()
            self.bulk = BulkDialog(self)
            return self.bulk

        def build_sidebar(self):
            self.sidebar = tk.Frame(self.pane, width=330, bg=C["card"])
            self.side_title = tk.Label(self.sidebar, text="문법 해설", anchor="w", font=(self.ui, 10, "bold"), padx=12,
                                       bg=C["card"], fg=C["text"])
            self.side_title.pack(fill="x", pady=(10, 4))
            self.tree = ttk.Treeview(self.sidebar, columns=("name",), style="Syntax.Treeview", selectmode="none")
            self.tree.heading("#0", text="문법")
            self.tree.heading("name", text="해설")
            self.tree.column("#0", width=205, minwidth=100, stretch=True)
            self.tree.column("name", width=95, minwidth=60, stretch=False)
            tsb = tk.Scrollbar(self.sidebar, command=self.tree.yview)
            self.tree.config(yscrollcommand=tsb.set)
            tsb.pack(side="right", fill="y", pady=4)
            self.tree.pack(fill="both", expand=True, padx=(8, 0), pady=4)
            self.tree.bind("<ButtonRelease-1>", self.on_sidebar_click)
            tk.Label(self.sidebar, text="누르면 커서 자리에 넣고,\n글을 고르고 누르면 감쌉니다.", justify="left",
                     fg=C["muted"], bg=C["card"], font=(self.ui, 9), padx=12).pack(fill="x", pady=(0, 8))

        # ------------------------------------------------ 아이콘 막대
        def build_toolbar(self):
            bar = tk.Frame(self.root, padx=8, pady=5, bg=C["card"])
            bar.pack(side="top", fill="x")
            tk.Frame(self.root, height=1, bg=C["line"]).pack(side="top", fill="x")

            def tip(widget, text):
                tt = {"win": None, "job": None}

                def show():
                    w = tk.Toplevel(widget)
                    w.wm_overrideredirect(True)
                    w.wm_geometry(f"+{widget.winfo_rootx() + 4}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
                    tk.Label(w, text=text, background=C["text"], foreground="white", padx=8, pady=3,
                             font=(self.ui, 9)).pack()
                    tt["win"] = w

                def enter(_):
                    tt["job"] = widget.after(450, show)

                def leave(_):
                    if tt["job"]:
                        widget.after_cancel(tt["job"])
                        tt["job"] = None
                    if tt["win"]:
                        tt["win"].destroy()
                        tt["win"] = None
                widget.bind("<Enter>", enter, add="+")
                widget.bind("<Leave>", leave, add="+")
                widget.bind("<ButtonPress>", leave, add="+")

            def hover(b):
                b.bind("<Enter>", lambda e: b.config(bg=C["soft"]), add="+")
                b.bind("<Leave>", lambda e: b.config(bg=C["card"]), add="+")

            def button(label, hint, cmd, **font):
                family = font.pop("family", self.ui)
                b = tk.Button(bar, text=label, command=cmd, width=3, relief="flat", bd=0, takefocus=False, bg=C["card"],
                              fg=C["text"], activebackground=C["sel"], font=tkfont.Font(family=family, size=11, **font),
                              cursor="hand2", pady=2)
                b.pack(side="left", padx=1)
                hover(b)
                tip(b, hint)
                return b

            def sep():
                tk.Frame(bar, width=1, bg=C["line"]).pack(side="left", fill="y", padx=6, pady=3)

            button("☰", "문법 해설 사이드바 보이기/숨기기", self.flip_sidebar)
            sep()
            button("📄", "새 문서 (Ctrl+N)", self.new)
            button("📂", "열기 (Ctrl+O)", self.open_dialog)
            button("💾", "저장 (Ctrl+S)", self.save)
            button("⇄", "대량 변환…", self.bulk_dialog)
            sep()
            button("↶", "실행 취소 (Ctrl+Z)", lambda: self.fire("<<Undo>>"))
            button("↷", "다시 실행 (Ctrl+Y)", lambda: self.fire("<<Redo>>"))
            sep()
            button("B", "굵게 (Ctrl+B)", lambda: self.wrap("b"), weight="bold")
            button("I", "기울임 (Ctrl+I)", lambda: self.wrap("i"), slant="italic")
            button("S", "취소선", lambda: self.wrap("s"), overstrike=True)
            button("</>", "코드", lambda: self.wrap("code"), family=self.mono)
            sep()
            hb = tk.Menubutton(bar, text="H ▾", relief="flat", bd=0, takefocus=False, bg=C["card"], fg=C["text"],
                               activebackground=C["sel"], font=(self.ui, 11, "bold"), cursor="hand2")
            hm = tk.Menu(hb, tearoff=0)
            for lv, name in ((0, "본문"), (1, "제목 1"), (2, "제목 2"), (3, "제목 3"), (4, "제목 4")):
                hm.add_command(label=name, command=lambda lv=lv: self.heading(lv))
            hb.config(menu=hm)
            hb.pack(side="left", padx=1)
            hover(hb)
            tip(hb, "제목 크기")
            button("•", "글머리 목록", lambda: self.line_prefix("ul"))
            button("1.", "번호 목록", lambda: self.line_prefix("ol"))
            button("❝", "인용", self.quote)
            sep()
            button("🔗", "링크 (Ctrl+K)", lambda: self.wrap("link"))
            button("🖼", "그림", lambda: self.wrap("img"))
            button("▦", "표 넣기", lambda: self.insert_block("table"))
            button("―", "가로줄", lambda: self.insert_block("hr"))
            button("{ }", "코드 블록", self.code_block, family=self.mono)

            # 오른쪽: 보기 모드(좌우 분할 / 렌더링만 / 원문만)
            seg = tk.Frame(bar, bg=C["line"], padx=1, pady=1)
            seg.pack(side="right")
            for val, name, hint in (("split", "좌우 분할", "두 패널을 나란히"), ("preview", "렌더링만", "렌더링 결과만 크게"),
                                    ("source", "원문만", "원래 문법만 크게(집중 편집)")):
                rb = tk.Radiobutton(seg, text=name, value=val, variable=self.view_mode, indicatoron=0, bd=0,
                                    relief="flat", padx=12, pady=4, bg=C["card"], fg=C["muted"],
                                    selectcolor=C["soft"], activebackground=C["soft"], cursor="hand2",
                                    font=(self.ui, 10), command=self.apply_view_mode)
                rb.pack(side="left")
                tip(rb, hint)

        # ------------------------------------------------ 보기 모드
        def assign_mode(self, p, mode):
            p.mode = mode
            p.show()
            self.render_panel(p)

        def apply_view_mode(self, persist=True):
            """좌우 분할 / 렌더링만 / 원문만."""
            if hasattr(self, "after_id") and self.after_id:
                self.flush()
            mode = self.view_mode.get()
            L, R = self.panels
            if mode == "preview" and all(p.editable() for p in self.panels):
                self.assign_mode(R if R is not self.active else L, "rendered")
            if mode == "source" and not any(p.editable() for p in self.panels):
                self.assign_mode(L, self.src_fmt if self.src_fmt in dict(SYNTAXES) else "markdown")
            names = [str(x) for x in self.pane.panes()]
            for p in self.panels:
                if str(p.frame) in names:
                    self.pane.forget(p.frame)
            if mode == "split":
                show = [L, R]
            elif mode == "preview":
                show = [next(p for p in self.panels if not p.editable())]
            else:
                show = [self.active if self.active is not None and self.active.editable() else
                        next(p for p in self.panels if p.editable())]
            for p in show:
                self.pane.add(p.frame, minsize=260, stretch="always")
            self.active = next((p for p in show if p.editable()), None)
            self.fill_sidebar()
            if persist:
                s = load_settings()
                s["view"] = mode
                save_settings(s)
                if self.active is not None:
                    self.active.text.focus_set()
                self.update_status()

        # ------------------------------------------------ 서식 도구 (지금 편집 중인 패널에 적용)
        def sel_range(self, whole_lines=False):
            t = self.text
            if t.tag_ranges("sel"):
                a, b = t.index("sel.first"), t.index("sel.last")
            else:
                a = b = t.index("insert")
            if whole_lines:
                if b != a and t.index(f"{b} linestart") == b:  # 줄 맨 앞에서 끝난 선택은 그 줄을 빼기
                    b = t.index(f"{b} - 1c")
                a, b = t.index(f"{a} linestart"), t.index(f"{b} lineend")
            return a, b

        def replace(self, a, b, new, s=None, e=None):
            t = self.text
            t.edit_separator()
            t.config(autoseparators=False)  # 지우기+넣기를 되돌리기 한 번으로
            t.delete(a, b)
            t.insert(a, new)
            t.config(autoseparators=True)
            t.edit_separator()
            if s is not None:
                t.tag_remove("sel", "1.0", "end")
                t.tag_add("sel", f"{a}+{s}c", f"{a}+{e}c")
                t.mark_set("insert", f"{a}+{e}c")
            t.focus_set()

        def wrap(self, key):
            if not self.ready():
                return "break"
            spec = WRAP[self.fmt].get(key)
            if not spec:
                self.update_status("이 문법에는 없는 서식입니다")
                return "break"
            a, b = self.sel_range()
            new, s, e = wrap_text(self.text.get(a, b), spec[0], spec[1], WRAP_DEFAULT[key])
            self.replace(a, b, new, s, e)
            return "break"

        def quote(self):
            if self.ready():
                if "quote" in WRAP[self.fmt]:
                    self.wrap("quote")
                else:
                    self.line_prefix("quote")

        def line_prefix(self, kind):
            if not self.ready():
                return
            spec = LINE[self.fmt].get(kind)
            if not spec:
                self.update_status("이 문법에는 없는 서식입니다")
                return
            a, b = self.sel_range(True)
            lines = self.text.get(a, b).split("\n")
            if not any(ln.strip() for ln in lines):   # 빈 줄에서 누르면 표시와 자리 글을 넣고 그 글을 골라 둔다
                head = spec[0].format(n=1)
                new = "\n".join([head + PLACEHOLDER[kind]] + lines[1:])
                self.replace(a, b, new, len(head), len(head) + len(PLACEHOLDER[kind]))
                return
            new = "\n".join(toggle_prefix(lines, *spec))
            self.replace(a, b, new, 0, len(new))  # 바꾼 줄들을 그대로 선택해 둬서 한 번 더 누르면 뗀다

        def heading(self, level):
            if not self.ready():
                return
            a, b = self.sel_range(True)
            lines = self.text.get(a, b).split("\n")
            if level > 0 and not any(ln.strip() for ln in lines):   # 빈 줄: 제목 표시와 '제목' 자리 글을 넣는다
                new = set_heading("제목", level, self.fmt)
                s = new.index("제목")
                self.replace(a, b, new, s, s + 2)
                return
            new = "\n".join(set_heading(ln, level, self.fmt) if ln.strip() else ln for ln in lines)
            self.replace(a, b, new, 0, len(new))

        def insert_block(self, kind):
            if not self.ready():
                return
            a, _ = self.sel_range()
            at = self.text.index(f"{a} lineend")
            line_empty = not self.text.get(f"{a} linestart", at).strip()
            self.replace(at, at, ("" if line_empty else "\n\n") + BLOCK[kind][self.fmt] + "\n")

        def code_block(self):
            if not self.ready():
                return
            a, b = self.sel_range()
            body = self.text.get(a, b) or "코드"
            self.replace(a, b, BLOCK["codeblock"][self.fmt].replace("{}", body, 1))

        # ------------------------------------------------ 보기 설정
        def make_fonts(self):
            sz, fam = self.size, self.ui
            F = lambda **kw: tkfont.Font(family=kw.pop("family", fam), size=kw.pop("size", sz), **kw)  # noqa: E731
            self.fonts = {"base": F(), "b": F(weight="bold"), "i": F(slant="italic"), "bi": F(weight="bold", slant="italic"),
                          "mono": F(family=self.mono, size=max(sz - 1, 8)), "small": F(size=max(sz - 3, 7))}
            self.hfonts = {n: F(weight="bold", size=sz + max(8 - 2 * n, 0) + (3 if n == 1 else 0)) for n in range(1, 7)}

        def configure_tags(self, p):
            v, f = p.view, self.fonts
            v.configure(font=f["base"])
            v.tag_configure("b", font=f["b"])
            v.tag_configure("i", font=f["i"])
            v.tag_configure("bi", font=f["bi"])
            v.tag_configure("u", underline=True)
            v.tag_configure("s", overstrike=True)
            v.tag_configure("sup", offset=5, font=f["small"])
            v.tag_configure("sub", offset=-3, font=f["small"])
            v.tag_configure("code", font=f["mono"], background=C["code"])
            v.tag_configure("codeblock", font=f["mono"], background=C["code"], lmargin1=8, lmargin2=8)
            v.tag_configure("math", font=f["mono"])
            v.tag_configure("raw", font=f["mono"], foreground=C["muted"], background="#fef3c7")
            v.tag_configure("chip", foreground="#92400e", background="#fef3c7")
            v.tag_configure("muted", foreground=C["muted"])
            v.tag_configure("link", underline=True)
            v.tag_configure("fnref")
            v.tag_configure("quote", foreground=C["muted"])
            v.tag_configure("hr", foreground=C["line"])
            for n in range(1, 7):
                v.tag_configure(f"h{n}", font=self.hfonts[n], spacing1=6, spacing3=2)
            for n in range(1, preview.MAX_LEVEL + 1):
                v.tag_configure(f"M{n}", lmargin1=20 * n, lmargin2=20 * n)
                v.tag_configure(f"B{n}", lmargin1=20 * (n - 1) + 6, lmargin2=20 * n)
            v.tag_raise("link")
            v.tag_raise("code")
            v.tag_bind("link", "<Enter>", lambda e: v.config(cursor="hand2"))
            v.tag_bind("link", "<Leave>", lambda e: v.config(cursor="arrow"))
            p.text.tag_configure("found", background="#fde68a")

        def zoom(self, d):
            self.size = max(7, min(28, self.size + d))
            self.save_ui_state()
            self.make_fonts()
            for p in self.panels:
                p.text.config(font=(self.mono, self.size))
                self.configure_tags(p)
            self._c["key"] = None
            self.update_all()

        # ------------------------------------------------ 문법 해설 사이드바
        def flip_sidebar(self):
            self.show_sidebar.set(not self.show_sidebar.get())
            self.toggle_sidebar()

        def toggle_sidebar(self):
            if self.show_sidebar.get():
                first = self.visible_panels()
                self.pane.add(self.sidebar, before=first[0].frame if first else None, minsize=230, width=330,
                              stretch="never")
            else:
                self.pane.forget(self.sidebar)
            s = load_settings()
            s["sidebar"] = bool(self.show_sidebar.get())
            save_settings(s)

        def fill_sidebar(self):
            """문법 해설: 지금 편집 중인 패널 문법의 서식을 '문법 → 해설' 순서로 나열한다."""
            self.tree.delete(*self.tree.get_children())
            self.entry_actions = {}
            group, fmt = None, self.fmt
            for g, name, example, action in syntax_entries(fmt):
                if g != group:
                    group = g
                    parent = self.tree.insert("", "end", text=g, open=True, values=("",))
                item = self.tree.insert(parent, "end", text=example, values=(name,))
                self.entry_actions[item] = action
            self.side_title.config(text=f"문법 해설 — {LABEL[fmt]}")

        def on_sidebar_click(self, e):
            item = self.tree.identify_row(e.y)
            action = self.entry_actions.get(item)
            if action:
                self.apply_action(action)
            self.tree.selection_remove(*self.tree.selection())

        def apply_action(self, action):
            kind = action[0]
            if kind == "wrap":
                self.wrap(action[1])
            elif kind == "heading":
                self.heading(action[1])
            elif kind == "line":
                self.line_prefix(action[1])
            elif kind == "block":
                if action[1] == "codeblock":
                    self.code_block()
                else:
                    self.insert_block(action[1])

        # ------------------------------------------------ 두 패널: 모드 고르기와 실시간 동기화
        def set_active(self, p):
            if self.active is not p:
                self.active = p
                self.fill_sidebar()

        def saved_geometry(self, st):
            """저장해 둔 창 크기·위치. 화면 밖이거나 너무 작으면 기본값."""
            g = st.get("geometry")
            m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", g) if isinstance(g, str) else None
            if m:
                w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                if w >= 600 and h >= 400 and w <= sw + 20 and h <= sh + 20 and -w + 120 < x < sw - 120 and -20 < y < sh - 80:
                    return g
            return "1240x740"

        def save_ui_state(self, window=False):
            """패널 선택(좌·우)·보기 모드·사이드바·글자 크기를 저장한다(window=True 면 창 크기·위치도)."""
            s = load_settings()
            s["left"], s["right"] = self.panels[0].mode, self.panels[1].mode
            s["view"] = self.view_mode.get()
            s["sidebar"] = bool(self.show_sidebar.get())
            s["size"] = self.size
            s["external_images"] = bool(self.ext_images.get())
            if window:
                try:
                    zoomed = self.root.state() == "zoomed"
                    s["zoomed"] = zoomed
                    if not zoomed:
                        s["geometry"] = self.root.geometry()
                except tk.TclError:
                    pass
            save_settings(s)

        def pick_mode(self, p):
            mode = mode_from_label(p.combo.get())
            if mode is None:                      # 구분선을 골랐으면 원래대로
                p.combo.set(combo_label(p.mode))
                return
            self.set_mode(p, mode)

        def set_mode(self, p, mode):
            """패널의 드롭박스 값을 바꾼다. 문법이면 기준 글을 그 문법으로 바꿔 보여 주고, 렌더링이면 읽기 전용 모양을 보여 준다."""
            self.flush()
            self.assign_mode(p, mode)
            if self.view_mode.get() != "split":     # 크게 보는 중에 고르면 그 종류의 보기 모드로
                self.view_mode.set("source" if p.editable() else "preview")
                self.apply_view_mode(persist=False)
            if p.editable():
                self.active = p
            elif self.active is p:
                self.active = self.panel
            self.fill_sidebar()
            self.save_ui_state()
            self.update_status()
            if p.editable():
                p.text.focus_set()

        def on_modified(self, p):
            """사용자가 이 패널에서 글을 고쳤다: 기준 글을 갱신하고 다른 패널을 150ms 뒤에 맞춘다."""
            if not p.text.edit_modified():
                return
            p.text.edit_modified(False)
            cur = p.get_text()
            if cur == p.last_text:   # 프로그램이 넣은 글이 만든 알림은 무시
                return
            p.last_text = cur
            self.src_text, self.src_fmt = cur, p.mode
            self.fmt_var.set(p.mode)
            self.dirty = (cur != self.saved_text) or (p.mode != self.saved_fmt)
            self.update_title()
            self.pending = p
            if self.after_id:
                self.root.after_cancel(self.after_id)
            self.after_id = self.root.after(DEBOUNCE_MS if len(cur) < 100_000 else 400, self.apply_pending)

        def apply_pending(self):
            self.after_id = None
            skip, self.pending = self.pending, None
            self.update_all(skip)

        def flush(self):
            """기다리는 동기화가 있으면 지금 바로 한다(저장·모드 바꾸기 앞에서)."""
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.apply_pending()

        def key(self):
            return (self.src_text, self.src_fmt, self.title_stem())

        def ensure(self, modes):
            """기준 글을 읽은 결과와 요청한 문법들의 변환 결과를 (캐시에서, 없으면 지금) 돌려준다."""
            c = self._c
            if c["key"] != self.key():
                c = self._c = build_cache(self.src_text, self.src_fmt, self.title_stem(), modes)
            else:
                for m in modes:
                    if m not in c["out"]:
                        c["out"][m] = self.src_text if m == self.src_fmt else wikiconv.render(
                            wikiconv.parse(self.src_text, bulk.SYNTAX[self.src_fmt], self.title_stem()),
                            bulk.SYNTAX[m], self.title_stem())
            if len(c["out"]) > 6:
                c["out"] = {m: c["out"][m] for m in modes if m in c["out"]}
            self.raw = c["raw"]
            return c

        def update_all(self, skip=None):
            modes = [p.mode for p in self.panels if p is not skip and p.editable()]
            c = self._c
            big = len(self.src_text) > BIG_DOC
            if big and (c["key"] != self.key() or any(m not in c["out"] for m in modes)):
                self.gen += 1                      # 큰 글은 변환을 스레드에 맡기고 입력은 막지 않는다
                self.jobs.put((self.gen, self.key(), modes, skip))
                if not self.polling:
                    self.polling = True
                    self.root.after(30, self.poll_worker)
                self.update_status("변환 중…")
            else:
                self.apply_views(skip)
            self.update_title()
            self.update_status()

        def worker(self):
            while True:
                gen, key, modes, skip = self.jobs.get()
                while not self.jobs.empty():       # 더 새 작업이 있으면 그것만
                    gen, key, modes, skip = self.jobs.get_nowait()
                try:
                    self.results.put((gen, key, skip, build_cache(key[0], key[1], key[2], modes), None))
                except Exception as e:  # noqa: BLE001
                    self.results.put((gen, key, skip, None, str(e)))

        def poll_worker(self):
            try:
                while True:
                    gen, key, skip, c, err = self.results.get_nowait()
                    if gen != self.gen or key != self.key():
                        continue
                    if err:
                        self.update_status("변환 오류: " + err)
                    else:
                        self._c = c
                        self.raw = c["raw"]
                        self.apply_views(skip)
                        self.update_status()
            except queue.Empty:
                pass
            if self.gen and self._c["key"] != self.key():
                self.root.after(30, self.poll_worker)
            else:
                self.polling = False

        def apply_views(self, skip=None):
            self.skipped_urls = set()
            for p in self.panels:
                if p is not skip:
                    self.render_panel(p)

        def converted(self, dst):
            """기준 글을 dst 문법으로(캐시). (글, 원문 보존 수)"""
            c = self.ensure([dst])
            return c["out"][dst], c["raw"] if dst != self.src_fmt else c["raw"]

        def render_panel(self, p):
            try:
                if p.mode == "rendered":
                    self.render_view(p, self.ensure([])["segs"])
                else:
                    self.set_panel_text(p, self.ensure([p.mode])["out"][p.mode])
            except Exception as e:  # 읽기 도구가 못 읽은 글도 편집은 계속할 수 있게
                self.update_status("변환 오류: " + str(e))

        def set_panel_text(self, p, text):
            if p.get_text() == text:
                return
            top = p.text.yview()[0]
            p.last_text = text
            p.text.delete("1.0", "end")
            p.text.insert("1.0", text)
            p.text.edit_reset()
            p.text.edit_modified(False)
            p.text.yview_moveto(top)

        def render_view(self, p, segs):
            v = p.view
            top = v.yview()[0]
            v.config(state="normal")
            v.delete("1.0", "end")
            p.links.clear()
            p.images.clear()
            for t, tags, extra in segs:
                tg = list(tags)
                if "b" in tg and "i" in tg:
                    tg.append("bi")
                if extra and extra[0] == "link":
                    key = "L%d" % len(p.links)
                    p.links[key] = extra[1:]
                    tg.append(key)
                if extra and extra[0] == "img" and self.show_image(p, extra[1]):
                    continue
                v.insert("end", t, tuple(tg))
            v.config(state="disabled")
            v.yview_moveto(top)

        def show_image(self, p, src):
            """그림으로 보여 준다: 같은 폴더의 그림 파일, 또는 http(s) 외부 주소(옵션). 안 되면 글자 칩으로 남는다."""
            if re.match(r"(?i)^https?://", src):
                path = self.remote_image(src)
            elif not self.path or re.match(r"(?i)^[a-z][a-z0-9+.-]*:", src):
                return False
            else:
                base = os.path.dirname(self.path)
                path = os.path.join(base, src.replace("/", os.sep))
                if not os.path.isfile(path):
                    path = os.path.join(base, os.path.basename(src))
                if not os.path.isfile(path):
                    return False
                ext = os.path.splitext(path)[1].lower()
                if ext not in (".png", ".gif"):
                    if ext not in (".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
                        return False
                    try:
                        path = local_image_png(path)       # tkinter 가 못 읽는 형식은 PNG 로 바꿔서
                    except OSError:
                        return False
            if not path:
                return False
            try:
                img = tk.PhotoImage(file=path)
                w = img.width()
                if w > IMG_MAX_W:
                    img = img.subsample(math.ceil(w / IMG_MAX_W))
            except tk.TclError:
                return False
            p.images.append(img)
            p.view.image_create("end", image=img)
            return True

        # ---- 외부 이미지: 백그라운드에서 내려받고, 끝나면 렌더링 창만 다시 그린다
        def remote_image(self, url):
            if not self.ext_images.get():
                self.skipped_urls.add(url)
                return None
            st = self.imgs.get(url)
            if st is None:
                self.imgs[url] = ("pending",)

                def job():
                    try:
                        self.img_results.put((url, "ok", fetch_image(url)))
                    except Exception as e:  # noqa: BLE001  (연결 실패·시간 초과·형식 오류 …)
                        self.img_results.put((url, "fail", str(getattr(e, "reason", None) or e)))
                threading.Thread(target=job, daemon=True).start()
                self.root.after(120, self.poll_images)
                return None
            return st[1] if st[0] == "ok" else None

        def poll_images(self):
            changed, failed = False, []
            try:
                while True:
                    url, kind, val = self.img_results.get_nowait()
                    self.imgs[url] = (kind, val)
                    changed = True
                    if kind == "fail":
                        failed.append((url, val))
            except queue.Empty:
                pass
            if changed:
                self.skipped_urls = set()
                for p in self.panels:
                    if not p.editable():
                        self.render_view(p, self._c["segs"])
                self.update_status()
                if failed:
                    short = failed[0][0] if len(failed[0][0]) < 60 else failed[0][0][:57] + "…"
                    self.update_status(f"그림을 불러오지 못했습니다: {short} ({failed[0][1]})"
                                       + (f" 외 {len(failed) - 1}개" if len(failed) > 1 else ""))
            if any(v[0] == "pending" for v in self.imgs.values()):
                self.root.after(150, self.poll_images)

        def toggle_ext_images(self):
            self.save_ui_state()
            if self.ext_images.get():      # 켰으니 예전에 실패한 것도 다시 시도
                self.imgs = {u: v for u, v in self.imgs.items() if v[0] == "ok"}
            self.skipped_urls = set()
            for p in self.panels:
                if not p.editable():
                    self.render_view(p, self._c["segs"])
            self.update_status()

        def on_view_click(self, p, e):
            idx = p.view.index(f"@{e.x},{e.y}")
            for t in p.view.tag_names(idx):
                if t in p.links:
                    kind, target = p.links[t]
                    return self.follow(kind, target)

        def follow(self, kind, target):
            if kind == "url":
                if re.match(r"(?i)^(https?|ftp|mailto):", target):
                    webbrowser.open(target)
                return
            if not self.path:
                self.update_status("저장한 문서에서만 링크를 따라갈 수 있습니다")
                return
            found = find_note(os.path.dirname(self.path), target)
            if found:
                self.open_path(found)
            else:
                self.update_status(f"같은 폴더에 '{md_name(target)}' 파일이 없습니다")

        def reinterpret(self):
            """글은 그대로 두고 이 글의 문법을 다르게 읽는다."""
            self.flush()
            self.src_fmt = self.fmt_var.get()
            if self.path:
                self.file_fmt = self.src_fmt
            self.dirty = (self.src_text != self.saved_text) or (self.src_fmt != self.saved_fmt)
            self.update_all()

        # ------------------------------------------------ 글 상태
        def name(self):
            return os.path.basename(self.path) if self.path else "이름 없음"

        def title_stem(self):
            return os.path.splitext(os.path.basename(self.path))[0] if self.path else ""

        def update_title(self):
            self.root.title(f"{'* ' if self.dirty else ''}{self.name()} — {APP}")

        def update_status(self, msg=None):
            t = self.text
            s = f"{t.index('insert').split('.')[0]}줄 {int(t.index('insert').split('.')[1]) + 1}칸  ·  " if t is not None else ""
            s += f"{len(self.src_text):,}자  ·  원본 문법 {LABEL[self.src_fmt]}"
            if self.raw:
                s += f"  ·  옮길 수 없어 원문으로 남긴 곳 {self.raw}곳"
            pending = sum(1 for v in self.imgs.values() if v[0] == "pending")
            if pending:
                s += f"  ·  그림 {pending}개 불러오는 중…"
            if self.skipped_urls:
                s += f"  ·  외부 이미지 {len(self.skipped_urls)}개는 꺼져 있어 안 보입니다(보기 → 외부 이미지 불러오기)"
            self.status.config(text=msg or s)

        # ------------------------------------------------ 파일
        def load(self, text, fmt, path):
            """문서를 통째로 바꾼다. 패널 선택(드롭박스)은 사용자가 고른 그대로 둔다."""
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.after_id = self.pending = None
            self.src_text, self.src_fmt, self.path = text, fmt, path
            self.file_fmt = fmt if path else None
            self.saved_text, self.saved_fmt, self.dirty = text, fmt, False
            self._c["key"] = None
            self.fmt_var.set(fmt)
            for p in self.panels:
                if p.editable():
                    p.last_text = None
            self.update_all()
            for p in self.panels:
                if p.editable():
                    p.text.edit_reset()
                    p.text.edit_modified(False)
            self.apply_view_mode(persist=False)
            self.fill_sidebar()

        def confirm_discard(self):
            self.flush()
            if not self.dirty:
                return True
            a = messagebox.askyesnocancel(APP, f"'{self.name()}' 의 바뀐 내용을 저장할까요?")
            if a is None:
                return False
            return self.save() if a else True

        def new(self):
            if self.confirm_discard():
                self.enc, self.nl = "utf-8", "\n"
                self.load("", self.src_fmt, None)

        def open_dialog(self):
            if not self.confirm_discard():
                return
            p = filedialog.askopenfilename(filetypes=FILETYPES)
            if p:
                self.open_path(p, ask=False)

        def open_path(self, path, ask=True):
            if ask and not self.confirm_discard():
                return
            try:
                with open(path, "rb") as f:
                    text, self.enc, self.nl = decode_file(f.read())
            except OSError as e:
                messagebox.showerror(APP, f"파일을 열지 못했습니다:\n{e}")
                return
            self.load(text, detect_format(text, path), os.path.abspath(path))

        def save(self):
            self.flush()
            if not self.path:
                return self.save_as()
            return self.write(self.path)

        def save_as(self):
            self.flush()
            p = filedialog.asksaveasfilename(defaultextension=EXT[self.file_fmt or self.src_fmt], filetypes=FILETYPES,
                                             initialfile=os.path.basename(self.path) if self.path else "")
            return self.write(p) if p else False

        def write(self, path):
            fmt = self.file_fmt or self.src_fmt          # 파일은 자기 문법으로 저장(패널이 다른 문법이어도)
            try:
                text = self.converted(fmt)[0]
                with open(path, "wb") as f:
                    f.write(encode_file(text, self.enc, self.nl))
            except OSError as e:
                messagebox.showerror(APP, f"저장하지 못했습니다:\n{e}")
                return False
            self.path = os.path.abspath(path)
            self.file_fmt = fmt
            self.saved_text, self.saved_fmt, self.dirty = self.src_text, self.src_fmt, False
            self.update_title()
            self.update_status(f"저장했습니다 ({LABEL[fmt]} 문법)")
            return True

        def quit(self):
            if self.confirm_discard():
                self.save_ui_state(window=True)
                self.root.destroy()

        # ------------------------------------------------ 복사·내보내기 (이 문서는 그대로)
        def copy_as(self, dst):
            self.flush()
            try:
                out, raw = self.converted(dst)
            except Exception as e:
                messagebox.showerror(APP, f"바꾸지 못했습니다:\n{e}")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(out)
            self.update_status(f"{LABEL[dst]} 문법으로 바꿔 복사했습니다" + (f" · 원문으로 남긴 곳 {raw}곳" if raw else ""))

        def export_as(self, dst):
            self.flush()
            try:
                out, raw = self.converted(dst)
            except Exception as e:
                messagebox.showerror(APP, f"바꾸지 못했습니다:\n{e}")
                return
            stem = self.title_stem() or "문서"
            p = filedialog.asksaveasfilename(defaultextension=EXT[dst], filetypes=FILETYPES, initialfile=stem + EXT[dst])
            if not p:
                return
            try:
                with open(p, "wb") as f:
                    f.write(encode_file(out.rstrip("\n") + "\n", "utf-8", self.nl))
            except OSError as e:
                messagebox.showerror(APP, f"저장하지 못했습니다:\n{e}")
                return
            self.update_status(f"{LABEL[dst]} 문법으로 저장했습니다: {os.path.basename(p)}"
                               + (f" · 원문으로 남긴 곳 {raw}곳" if raw else ""))

        # ------------------------------------------------ 찾기
        def find_dialog(self):
            if not self.ready():
                return
            t = self.text
            top = tk.Toplevel(self.root)
            top.title("찾기")
            top.configure(bg=C["bg"])
            top.transient(self.root)
            top.resizable(False, False)
            ent = tk.Entry(top, width=30, relief="solid", bd=1)
            ent.pack(side="left", padx=8, pady=8)
            sel = t.get("sel.first", "sel.last") if t.tag_ranges("sel") else ""
            ent.insert(0, sel.split("\n")[0])
            ent.focus_set()
            ent.select_range(0, "end")

            def go(back=False):
                t.tag_remove("found", "1.0", "end")
                q = ent.get()
                if not q:
                    return
                start = t.index("sel.first" if back and t.tag_ranges("sel") else
                                "sel.last" if t.tag_ranges("sel") else "insert")
                kw = {"backwards": True, "stopindex": "1.0"} if back else {"stopindex": "end"}
                pos = t.search(q, start, nocase=True, **kw)
                if not pos:
                    pos = t.search(q, "end" if back else "1.0", nocase=True, **kw)
                if pos:
                    end = f"{pos}+{len(q)}c"
                    t.tag_remove("sel", "1.0", "end")
                    t.tag_add("sel", pos, end)
                    t.mark_set("insert", pos if back else end)
                    t.see(pos)
                else:
                    self.update_status("찾는 글이 없습니다")
            tk.Button(top, text="다음", command=go, relief="groove").pack(side="left", padx=2)
            tk.Button(top, text="이전", command=lambda: go(True), relief="groove").pack(side="left", padx=2)
            ent.bind("<Return>", lambda e: go())
            top.bind("<Escape>", lambda e: top.destroy())

        # ------------------------------------------------ 등록
        def register(self):
            try:
                register_open_with()
            except Exception as e:
                messagebox.showerror(APP, f"등록하지 못했습니다:\n{e}")
                return
            messagebox.showinfo(APP, "등록했습니다.\n탐색기에서 .md 파일을 오른쪽 눌러 [연결 프로그램] → [다른 앱 선택] 에서 "
                                     f"'{APP}' 를 고르고 [항상] 을 누르면 기본 프로그램이 됩니다.")

        def unregister(self):
            try:
                unregister_open_with()
            except Exception as e:
                messagebox.showerror(APP, f"해제하지 못했습니다:\n{e}")
                return
            messagebox.showinfo(APP, "등록을 해제했습니다.")

    try:  # 윈도에서 글씨가 흐리게 보이지 않게, 작업표시줄에서 파이썬이 아닌 이 프로그램으로 묶이게
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(PROG_ID)
    except Exception:
        pass
    argv = sys.argv[1:] if argv is None else argv
    root = tk.Tk()
    try:  # 창·작업표시줄 아이콘
        if sys.platform == "win32":
            root.iconbitmap(default=icon_path("icon.ico"))
        root.iconphoto(True, tk.PhotoImage(file=icon_path("icon.png")))
    except Exception:
        pass
    ed = Editor(root, argv[0] if argv else None)
    if loop:
        root.mainloop()
    return root, ed


def main(argv=None):
    run_gui(argv)


if __name__ == "__main__":
    main()
