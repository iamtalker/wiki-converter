"""대량 변환: 위키 데이터(압축 파일·폴더) 통째로 → 다른 위키의 데이터 형식 (표준 라이브러리만 사용).

읽는 것(입력)
  dokuwiki  : data/pages 가 들어 있는 zip 또는 폴더(미디어는 data/media)
  mediawiki : 가져오기/내보내기 XML(.xml · .xml.gz · .xml.bz2), 또는 그것이 들어 있는 zip·폴더(이미지 폴더 포함 가능)
  opennamu  : data.db(SQLite), 또는 그것이 들어 있는 zip·폴더
  markdown  : .md 파일이 들어 있는 zip 또는 폴더
쓰는 것(출력): 항상 zip 하나. 안에 그 위키가 받는 형식의 데이터 + 미디어 + 「읽어 주세요.txt」.

원칙: 각 문서의 최신 판만 옮긴다(문서 이력은 옮기지 않는다). 문서 하나가 이상해도 전체를 멈추지 않고,
옮길 수 없는 문법은 원문을 남긴다. 원본 파일은 건드리지 않는다.
"""
import bz2
import calendar
import gzip
import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

import wikiconv
from wikiconv import tree

FORMATS = ("dokuwiki", "mediawiki", "opennamu", "markdown")
SYNTAX = {"dokuwiki": "dokuwiki", "mediawiki": "mediawiki", "opennamu": "namumark", "markdown": "markdown"}
LABEL = {"dokuwiki": "도쿠위키", "mediawiki": "미디어위키", "opennamu": "오픈나무(openNAMU)", "markdown": "마크다운"}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".avif",
             ".pdf", ".mp3", ".ogg", ".oga", ".wav", ".flac", ".mp4", ".webm", ".ogv", ".mov", ".zip", ".7z"}
URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^(?:https?|ftp|mailto|data):", re.I)


# ================================================================ 기본 자료형
class Page:
    """문서 하나(최신 판). modified 는 UTC 'YYYY-MM-DD HH:MM:SS'."""
    __slots__ = ("title", "text", "modified", "author", "summary", "resolve", "sid", "h1")

    def __init__(self, title, text, modified="", author="", summary="", resolve=None, sid="", h1=None):
        self.title, self.text, self.modified = title, text, modified or ""
        self.author, self.summary, self.resolve, self.sid, self.h1 = author or "", summary or "", resolve, sid, h1


class Media:
    """미디어 파일 하나. ref 는 원래 위치(폴더 구분은 ':'), opener() 는 읽기용 파일 객체를 준다."""
    __slots__ = ("ref", "base", "opener", "flat")

    def __init__(self, ref, opener):
        self.ref = ref.strip("/").replace("/", ":")
        self.base = self.ref.rsplit(":", 1)[-1]
        self.opener, self.flat = opener, ""


class Source:
    def __init__(self, fmt, pages, media=(), total=None, skipped=None, closer=None):
        self.fmt, self.pages, self.media = fmt, pages, list(media)
        self.total, self.skipped = total, skipped if skipped is not None else Counter()
        self._closer = closer

    def close(self):
        if self._closer:
            self._closer()


def utc_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def local_to_utc(s):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))))
    except (ValueError, OverflowError):
        return ""


def utc_to_local(s):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))))
    except (ValueError, OverflowError):
        return s


def utc_secs(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return int(time.time())


# ================================================================ 압축 파일·폴더 읽기
def _fix_name(info):
    """zip 안 파일 이름의 글자 깨짐을 바로잡는다(UTF-8 표시가 없는 한글 이름)."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return name


class Archive:
    """zip 이나 폴더를 같은 방식으로 읽는다. 이름은 항상 '/' 로 구분한 상대 경로."""

    def __init__(self, path):
        self.path = path
        self.isdir = os.path.isdir(path)
        self.zip, self.infos = None, {}
        if not self.isdir:
            if not zipfile.is_zipfile(path):
                raise ValueError("zip 파일이 아닙니다: " + os.path.basename(path))
            self.zip = zipfile.ZipFile(path)
            for i in self.zip.infolist():
                if not i.is_dir():
                    self.infos[_fix_name(i).replace("\\", "/")] = i

    def names(self):
        if self.zip:
            return list(self.infos)
        out = []
        for dp, _, files in os.walk(self.path):
            for f in files:
                out.append(os.path.relpath(os.path.join(dp, f), self.path).replace(os.sep, "/"))
        return out

    def open(self, name):
        return self.zip.open(self.infos[name]) if self.zip else open(os.path.join(self.path, *name.split("/")), "rb")

    def read(self, name):
        with self.open(name) as f:
            return f.read()

    def text(self, name):
        return self.read(name).decode("utf-8", "replace").lstrip("﻿")

    def mtime_utc(self, name):
        if self.zip:
            d = self.infos[name].date_time
            return local_to_utc("%04d-%02d-%02d %02d:%02d:%02d" % d)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.path.getmtime(os.path.join(self.path, *name.split("/")))))

    def size(self, name):
        return self.infos[name].file_size if self.zip else os.path.getsize(os.path.join(self.path, *name.split("/")))

    def close(self):
        if self.zip:
            self.zip.close()


def _media_from(arc, names, strip="", any_ext=False, decode=False):
    """미디어 파일들. any_ext 는 미디어 전용 폴더(도쿠위키 data/media)일 때: 확장자와 상관없이 모두(숨김 파일 제외)."""
    out = []
    for n in names:
        base = os.path.basename(n)
        if (not base.startswith(".")) if any_ext else os.path.splitext(n)[1].lower() in MEDIA_EXT:
            ref = n[len(strip):] if strip and n.startswith(strip) else n
            out.append(Media(urllib.parse.unquote(ref) if decode else ref, lambda n=n: arc.open(n)))
    return out


# ================================================================ 도쿠위키 읽기
H1_RE = re.compile(r"^\s*======\s*(.+?)\s*======[ \t]*\n?")
DOKU_SKIP_DIRS = ("attic/", "meta/", "cache/", "index/", "locks/", "tmp/", "media_attic/", "media_meta/")


def _doku_clean_id(s):
    return re.sub(r"\s+", "_", s.strip()).lower()


def _doku_resolver(cur_id, id_title):
    ns = cur_id.rsplit(":", 1)[0] if ":" in cur_id else ""

    def resolve(page):
        p = page.strip().replace(" ", "_")
        if p.endswith(":"):
            p += "start"
        cands = []
        if p.startswith(":"):
            cands.append(p[1:])
        elif p.startswith(("..", ".")):
            base = ns.split(":") if ns else []
            for part in p.replace(".:", "./").split(":"):
                if part == "..":
                    base = base[:-1]
                elif part in (".", ""):
                    continue
                else:
                    base.append(part)
            cands.append(":".join(base))
        else:
            cands += [(ns + ":" + p) if ns else p, p]
        for c in cands:
            t = id_title.get(_doku_clean_id(c))
            if t:
                return t
        return tree_id_to_title(cands[0])
    return resolve


def tree_id_to_title(pid):
    return wikiconv.dokuwiki.id_to_title(pid)


def read_dokuwiki(path, skip_default=True):
    arc = Archive(path)
    names = arc.names()
    root = None
    for pat in (r"^(?:.*/)?data/pages/", r"^(?:.*/)?pages/"):
        for n in names:
            m = re.match(pat, n)
            if m and n.endswith(".txt"):
                root = m.group(0)
                break
        if root is not None:
            break
    if root is None:
        root = ""
    page_names = [n for n in names if n.startswith(root) and n.endswith(".txt")
                  and not (root == "" and any(("/" + n).find("/" + d) >= 0 for d in DOKU_SKIP_DIRS))]
    if not page_names:
        arc.close()
        raise ValueError("도쿠위키 문서(data/pages 폴더의 .txt)를 찾지 못했습니다")
    media_root = (root[:-len("pages/")] + "media/") if root.endswith("pages/") else "media/"
    media = _media_from(arc, [n for n in names if n.startswith(media_root)], media_root, any_ext=True, decode=True)
    titles_map = {}
    for n in names:
        if n == "anywiki_titles.json" or n.endswith("/anywiki_titles.json"):
            try:
                titles_map = json.loads(arc.text(n))
            except ValueError:
                pass
    ids = {}
    for n in page_names:
        rel = n[len(root):-4]
        pid = ":".join(urllib.parse.unquote(p) for p in rel.split("/"))
        ids[pid] = n
    skipped = Counter()
    if skip_default and "wiki:syntax" in ids:  # 도쿠위키가 기본으로 딸려 보내는 설명서는 위키 문서가 아니다
        for pid in [p for p in ids if p.startswith("wiki:") or p == "playground:playground"]:
            del ids[pid]
            skipped["도쿠위키 기본 설명 문서"] += 1
    texts = {pid: arc.text(n) for pid, n in ids.items()}
    id_title, used = {}, set()
    for pid in sorted(ids):
        # 제목은 ID 에서 만든다(도쿠위키에서 문서의 정체성은 ID 라서, 첫 제목줄을 제목으로 삼으면 ID·링크가 바뀐다).
        # 이 변환기가 만든 압축 파일(anywiki_titles.json)은 거기 적힌 제목을 쓴다.
        title = titles_map.get(pid) or tree_id_to_title(pid)
        if title.lower() in used:
            title = f"{title} ({pid})"
        used.add(title.lower())
        id_title[pid.lower()] = title

    def same(a, b):
        norm = lambda s: re.sub(r"[\s_]+", " ", s).strip().lower()  # noqa: E731
        return norm(a) == norm(b)

    def pages():
        for pid in sorted(ids):
            text, title = texts[pid], id_title[pid.lower()]
            h = H1_RE.match(text)
            had = False
            if h and same(h.group(1), title):  # 제목과 같은 첫 제목줄은 본문에서 뺀다(되돌릴 때 다시 붙인다)
                text = text[h.end():].lstrip("\n")
                had = True
            # 제목과 다른 제목줄은 본문 내용이므로 그대로 둔다
            yield Page(title, text, arc.mtime_utc(ids[pid]), resolve=_doku_resolver(pid, id_title), sid=pid,
                       h1=True if had else None)
    return Source("dokuwiki", pages, media, len(ids), skipped, arc.close)


# ================================================================ 미디어위키 읽기
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _mw_time(ts):
    return ts.replace("T", " ").rstrip("Z")[:19] if ts else ""


def iter_mediawiki(stream, skipped):
    for _, el in ET.iterparse(stream, events=("end",)):
        if _local(el.tag) != "page":
            continue
        title = (_child(el, "title").text or "").strip() if _child(el, "title") is not None else ""
        ns_el = _child(el, "ns")
        ns = int(ns_el.text) if ns_el is not None and (ns_el.text or "").lstrip("-").isdigit() else 0
        best = None
        for rev in el:
            if _local(rev.tag) != "revision":
                continue
            ts = (_child(rev, "timestamp").text or "") if _child(rev, "timestamp") is not None else ""
            if best is None or ts >= best[0]:
                best = (ts, rev)
        if title and best is not None:
            if ns not in (0, 10, 14):
                skipped["다른 이름공간(토론·사용자·파일 설명 등)"] += 1
            else:
                rev = best[1]
                text_el = _child(rev, "text")
                who = _child(rev, "contributor")
                name = ""
                if who is not None:
                    u = _child(who, "username") if _child(who, "username") is not None else _child(who, "ip")
                    name = (u.text or "") if u is not None else ""
                cm = _child(rev, "comment")
                yield Page(wikiconv.mediawiki.to_common(title), (text_el.text or "") if text_el is not None else "",
                           _mw_time(best[0]), name, (cm.text or "") if cm is not None else "")
        el.clear()


def _open_xml(path_or_file, name=""):
    low = (name or path_or_file if isinstance(path_or_file, str) else name).lower()
    if isinstance(path_or_file, str):
        if low.endswith(".gz"):
            return gzip.open(path_or_file, "rb")
        if low.endswith(".bz2"):
            return bz2.open(path_or_file, "rb")
        return open(path_or_file, "rb")
    if low.endswith(".gz"):
        return gzip.GzipFile(fileobj=path_or_file)
    if low.endswith(".bz2"):
        return bz2.BZ2File(path_or_file)
    return path_or_file


def read_mediawiki(path):
    skipped = Counter()
    if os.path.isfile(path) and path.lower().endswith((".xml", ".xml.gz", ".xml.bz2")):
        def pages():
            with _open_xml(path) as f:
                yield from iter_mediawiki(f, skipped)
        return Source("mediawiki", pages, [], None, skipped)
    arc = Archive(path)
    xmls = [n for n in arc.names() if n.lower().endswith((".xml", ".xml.gz", ".xml.bz2"))]
    if not xmls:
        arc.close()
        raise ValueError("미디어위키 XML 파일(.xml, .xml.gz)을 찾지 못했습니다")
    media = _media_from(arc, arc.names())

    def pages():
        for n in sorted(xmls):
            with arc.open(n) as raw:
                with _open_xml(raw, n) as f:
                    yield from iter_mediawiki(f, skipped)
    return Source("mediawiki", pages, media, None, skipped, arc.close)


# ================================================================ 오픈나무 읽기
def from_db_title(t):
    return "분류:" + t[9:] if t.startswith("category:") else t


def to_db_title(t):
    return "category:" + t[3:] if t.startswith("분류:") else t


def read_opennamu(path):
    skipped = Counter()
    cleanup = []
    arc = None
    media = []
    if os.path.isfile(path) and path.lower().endswith((".db", ".sqlite", ".sqlite3")):
        dbpath = path
    else:
        arc = Archive(path)
        dbs = [n for n in arc.names() if n.lower().endswith((".db", ".sqlite", ".sqlite3"))]
        if not dbs:
            arc.close()
            raise ValueError("openNAMU 데이터 파일(data.db)을 찾지 못했습니다")
        dbs.sort(key=lambda n: (os.path.basename(n).lower() != "data.db", len(n)))
        if arc.isdir:
            dbpath = os.path.join(arc.path, *dbs[0].split("/"))
        else:
            tmpdir = tempfile.mkdtemp(prefix="wikiconv-")
            cleanup.append(tmpdir)
            dbpath = os.path.join(tmpdir, "data.db")
            with arc.open(dbs[0]) as src, open(dbpath, "wb") as dst:
                shutil.copyfileobj(src, dst)
        media = _media_from(arc, arc.names())
    probe = sqlite3.connect(dbpath)
    try:
        if not probe.execute("select 1 from sqlite_master where type='table' and name='data'").fetchone():
            raise ValueError("openNAMU 의 data 표가 없는 파일입니다")
        total = probe.execute("select count(*) from data").fetchone()[0]
    finally:
        probe.close()

    def pages():
        db = sqlite3.connect(dbpath)
        try:
            last = {}
            try:
                for t, d, ip, send in db.execute("select title, date, ip, send from history group by title having max(cast(id as integer))"):
                    last[t] = (d, ip, send)
            except sqlite3.Error:
                pass
            for title, data in db.execute("select title, data from data"):
                if title.startswith(("file:", "user:")):
                    skipped["파일·사용자 문서"] += 1
                    continue
                d, ip, send = last.get(title, ("", "", ""))
                yield Page(from_db_title(title), data or "", local_to_utc(d), ip, send)
        finally:
            db.close()

    def closer():
        if arc:
            arc.close()
        for d in cleanup:
            shutil.rmtree(d, ignore_errors=True)
    return Source("opennamu", pages, media, total, skipped, closer)


# ================================================================ 마크다운 읽기
def read_markdown(path):
    arc = Archive(path)
    names = arc.names()
    mds = sorted(n for n in names if n.lower().endswith(".md") and not os.path.basename(n).startswith("."))
    if not mds:
        arc.close()
        raise ValueError(".md 파일을 찾지 못했습니다")
    top = {n.split("/")[0] for n in mds if "/" in n}
    strip = ""
    if len(top) == 1 and all("/" in n for n in mds):  # 모두 폴더 하나 안에 있으면 그 폴더는 제목에 넣지 않는다
        strip = next(iter(top)) + "/"
    media = _media_from(arc, names, strip)

    def pages():
        for n in mds:
            title = wikiconv.markdown.md_title(n[len(strip):] if n.startswith(strip) else n)
            yield Page(title, arc.text(n), arc.mtime_utc(n))
    return Source("markdown", pages, media, len(mds), None, arc.close)


READERS = {"dokuwiki": read_dokuwiki, "mediawiki": read_mediawiki, "opennamu": read_opennamu,
           "markdown": read_markdown}


ALIASES = {"namumark": "opennamu", "namu": "opennamu", "opennamu": "opennamu", "doku": "dokuwiki", "mw": "mediawiki",
           "md": "markdown"}


def norm_fmt(fmt):
    f = ALIASES.get((fmt or "").strip().lower(), (fmt or "").strip().lower())
    if f not in FORMATS:
        raise ValueError("알 수 없는 형식: " + str(fmt))
    return f


def open_source(path, fmt, **kw):
    fmt = norm_fmt(fmt)
    if fmt not in READERS:
        raise ValueError("알 수 없는 형식: " + fmt)
    if not os.path.exists(path):
        raise ValueError("파일이나 폴더가 없습니다: " + path)
    return READERS[fmt](path, **kw)


# ================================================================ 미디어 이름 정리·그림 링크 고치기
def _san(name):
    name = name.translate(INVISIBLE)
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name.strip().replace(" ", "_")).strip("._") or "_"


INVISIBLE = dict.fromkeys(map(ord, "﻿​‌‍⁠"))  # 보이지 않는 글자(원문에 섞여 있는 경우가 있음)


def _norm(ref):
    r = ref.translate(INVISIBLE).strip().lstrip(":").replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    return r.replace("/", ":").replace(" ", "_").lower()


class MediaIndex:
    """출력에 쓸 미디어 파일 이름(flat)을 정하고, 문서 안 그림 이름을 찾는다."""

    def __init__(self, media):
        self.media = media
        cnt = Counter(m.base.lower() for m in media)
        seen, self.by_key, self.by_base = set(), {}, {}
        for m in media:
            flat = _san(m.base) if cnt[m.base.lower()] == 1 else _san(m.ref)
            base, k = flat, 2
            while flat.lower() in seen:
                root, ext = os.path.splitext(base)
                flat, k = f"{root}_{k}{ext}", k + 1
            seen.add(flat.lower())
            m.flat = flat
            self.by_key.setdefault(_norm(m.ref), m)
            self.by_base.setdefault(_norm(m.base), m)

    def lookup(self, ref):
        k = _norm(ref)
        return self.by_key.get(k) or self.by_base.get(k.rsplit(":", 1)[-1])


def fix_images(node, idx, used, missing):
    """문서 구조 안의 그림 이름을 출력용 이름으로 바꾼다(틀 인자는 건드리지 않는다)."""
    if isinstance(node, list):
        if node and node[0] == "tpl":
            return
        if len(node) >= 3 and node[0] == "img" and isinstance(node[1], str):
            src = node[1]
            if not URL_RE.match(src):
                m = idx.lookup(src)
                if m:
                    node[1] = m.flat
                    used.add(m.flat)
                else:
                    missing.add(src)
                    node[1] = _san(src.replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
            return
        for x in node:
            fix_images(x, idx, used, missing)
    elif isinstance(node, dict):
        for v in node.values():
            fix_images(v, idx, used, missing)


def count_raw(node):
    if isinstance(node, list):
        if node and node[0] == "tpl":
            return 0
        if len(node) == 3 and node[0] == "raw":
            return 1
        return sum(count_raw(x) for x in node)
    if isinstance(node, dict):
        return sum(count_raw(v) for v in node.values())
    return 0


class Report:
    def __init__(self):
        self.pages = 0
        self.failed = []
        self.raw = 0
        self.raw_pages = 0
        self.media_total = 0
        self.media_missing = set()
        self.skipped = Counter()
        self.out = ""
        self.seconds = 0.0

    def as_dict(self):
        return {"pages": self.pages, "failed": self.failed[:50], "failed_count": len(self.failed), "raw": self.raw,
                "raw_pages": self.raw_pages, "media": self.media_total, "media_missing": sorted(self.media_missing)[:50],
                "media_missing_count": len(self.media_missing), "skipped": dict(self.skipped), "out": self.out,
                "seconds": round(self.seconds, 1)}


def convert_page(p, src, dst, idx, rep, used):
    try:
        if src == "dokuwiki" and p.resolve:
            doc = tree.tidy(wikiconv.dokuwiki.read(p.text.replace("\x00", ""), p.title, p.resolve))
        else:
            doc = wikiconv.parse(p.text, SYNTAX[src], p.title)
        fix_images(doc.blocks, idx, used, rep.media_missing)
        n = count_raw(doc.blocks)
        if n:
            rep.raw += n
            rep.raw_pages += 1
        text = wikiconv.render(doc, SYNTAX[dst], p.title)
    except Exception as e:  # 문서 하나가 이상해도 전체는 계속한다(내용은 잃지 않게 원문을 남긴다)
        rep.failed.append(f"{p.title}: {e}")
        text = wikiconv.render(wikiconv.Doc([["raw", SYNTAX[src], p.text]]), SYNTAX[dst], p.title)
    return Page(p.title, text, p.modified, p.author, p.summary, h1=p.h1)


# ================================================================ 쓰기
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
OPENNAMU_SCHEMA = [
    "create table data (test text default '', title text default '', data text default '', type text default '')",
    "create table history (test text default '', id text default '', title text default '', data text default '', "
    "date text default '', ip text default '', send text default '', leng text default '', hide text default '', "
    "type text default '')",
    "create table data_set (test text default '', doc_name text default '', doc_rev text default '', "
    "set_name text default '', set_data text default '')",
    "create table back (test text default '', title text default '', link text default '', type text default '', "
    "data text default '')",
    "create table yourwiki_pack (k text primary key, v text)",
]
CAT_RE = re.compile(r"\[\[분류:([^\]|#]+)")
CREATOR = "위키 변환기"


def _zinfo(name, modified):
    t = time.gmtime(utc_secs(modified)) if modified else time.gmtime()
    y = max(t.tm_year, 1980)
    zi = zipfile.ZipInfo(name, (y, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec))
    zi.compress_type = zipfile.ZIP_DEFLATED
    return zi


def _write_media(z, media, folder, fn=lambda s: s):
    for m in media:
        zi = _zinfo(folder + fn(m.flat), "")
        with m.opener() as src, z.open(zi, "w", force_zip64=True) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)


def _urlenc(s):
    """PHP 의 urlencode 와 같은 결과(도쿠위키 fnencode=url 의 파일 이름)."""
    return urllib.parse.quote_plus(s, safe="").replace("~", "%7E")


def _id_path(pid, fnencode="url"):
    """문서 ID → 파일 경로. fnencode: 도쿠위키 설정(conf['fnencode'])과 같아야 도쿠위키가 파일을 찾는다.
    url(기본값)은 한글 등을 %XX 로, utf-8 은 한글 이름 그대로."""
    return "/".join(p if fnencode == "utf-8" else _urlenc(p) for p in pid.split(":"))


def _readme(dst, n, has_media):
    head = f"위키 변환기로 만든 {LABEL[dst]} 데이터입니다. 문서 {n:,}개(문서마다 최신 판만 옮김)."
    how = {
        "dokuwiki": "1. 이 압축 파일의 data/ 폴더를 도쿠위키 폴더에 덮어 풉니다.\n"
                    "2. 검색 색인을 만드세요: php bin/indexer.php\n"
                    "3. 미디어는 data/media/ 에 들어 있습니다(문서 안 그림 이름은 이에 맞춰 바꿔 두었습니다).",
        "mediawiki": "1. 문서: php maintenance/run.php importDump 파일.xml 을 실행한 뒤\n"
                     "   php maintenance/run.php rebuildrecentchanges 를 실행합니다.\n"
                     "2. 미디어: images/ 폴더의 파일은 php maintenance/run.php importImages images 로 올립니다\n"
                     "   (업로드 설정이 켜져 있어야 합니다).",
        "opennamu": "1. openNAMU 를 끄고, 이 압축 파일의 data.db 를 openNAMU 의 data.db 대신 두거나\n"
                    "   (새 위키라면) 그대로 사용합니다. 없는 표는 openNAMU 가 켤 때 만듭니다.\n"
                    "2. 미디어는 images/ 폴더에 들어 있으니 위키의 파일 올리기로 다시 올려 주세요.",
        "markdown": "문서마다 .md 파일 하나입니다. 같은 폴더에 미디어 파일이 있습니다.",
    }[dst]
    notes = ("\n\n주의\n- 문서 이력(옛 판)·사용자 계정·토론은 옮기지 않습니다.\n"
             "- 옮길 수 없는 문법은 원문을 남겨 두었습니다. 옮긴 뒤 몇 개 문서를 직접 확인해 보세요.\n"
             "- 원본 위키 데이터는 건드리지 않았습니다. 새 위키에 넣기 전에 백업을 해 두세요.")
    return head + "\n\n" + how + notes + "\n"


def write_dokuwiki(pages, media, out, fnencode="url"):
    from wikiconv.dokuwiki import doku_id, id_to_title
    if fnencode not in ("url", "utf-8"):
        raise ValueError("도쿠위키 파일 이름 방식은 url 이나 utf-8 이어야 합니다")
    enc = (lambda s: s) if fnencode == "utf-8" else _urlenc
    seen, titles, n = set(), {}, 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for p in pages:
            pid = base = doku_id(p.title)
            k = 2
            while pid in seen:
                pid, k = f"{base}_{k}", k + 1
            seen.add(pid)
            titles[pid] = p.title
            # 제목 줄은 ID 로 제목을 되살릴 수 없을 때만 붙인다(ID 는 소문자·밑줄이라 대소문자·기호가 사라짐).
            # 미디어위키가 첫 글자를 대문자로 바꾼 것(satisfactory → Satisfactory)은 정보가 아니므로 무시한다.
            derived = id_to_title(pid)
            need_h1 = p.h1 is True or (p.h1 is None and p.title not in (derived, derived[:1].upper() + derived[1:]))
            z.writestr(_zinfo("data/pages/" + _id_path(pid, fnencode) + ".txt", p.modified),
                       (f"====== {p.title} ======\n\n" if need_h1 else "") + p.text)
            n += 1
        z.writestr("anywiki_titles.json", json.dumps(titles, ensure_ascii=False))
        _write_media(z, media, "data/media/", enc)
        z.writestr("읽어 주세요.txt", _readme("dokuwiki", n, bool(media)) +
                   f"\n도쿠위키 파일 이름 방식: {fnencode}  (도쿠위키 설정 fnencode 와 같아야 문서를 찾습니다. "
                   "다르면 변환할 때 다시 고르세요)\n")
    return n


def write_mediawiki(pages, media, out):
    from wikiconv.mediawiki import mw_title
    tmp = out + ".xml.part"
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11" xml:lang="ko">\n'
                "<siteinfo><sitename>위키 변환기</sitename><case>first-letter</case></siteinfo>\n")
        for p in pages:
            t = mw_title(p.title)
            ns = 14 if t.startswith("Category:") else 10 if t.startswith("Template:") else 0
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(utc_secs(p.modified))) if p.modified \
                else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            f.write(f"<page><title>{html.escape(t)}</title><ns>{ns}</ns><revision><timestamp>{ts}</timestamp>"
                    f"<contributor><username>{html.escape(p.author or CREATOR)}</username></contributor>"
                    f"<comment>{html.escape(p.summary or CREATOR + '로 옮김')}</comment>"
                    f"<model>wikitext</model><format>text/x-wiki</format>"
                    f'<text xml:space="preserve">{html.escape(CTRL.sub("", p.text), quote=False)}</text></revision></page>\n')
            n += 1
        f.write("</mediawiki>\n")
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.write(tmp, "mediawiki-import.xml")
            _write_media(z, media, "images/")
            z.writestr("읽어 주세요.txt", _readme("mediawiki", n, bool(media)))
    finally:
        os.remove(tmp)
    return n


def write_opennamu(pages, media, out):
    tmpdir = tempfile.mkdtemp(prefix="wikiconv-")
    dbpath = os.path.join(tmpdir, "data.db")
    n = 0
    try:
        db = sqlite3.connect(dbpath)
        for s in OPENNAMU_SCHEMA:
            db.execute(s)
        for p in pages:
            t = to_db_title(p.title)
            when = utc_to_local(p.modified) if p.modified else time.strftime("%Y-%m-%d %H:%M:%S")
            db.execute("insert into data (title, data, type) values (?, ?, '')", (t, p.text))
            db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) values "
                       "('1', ?, ?, ?, ?, ?, ?, '', 'r1')", (t, p.text, when, p.author or CREATOR, p.summary or "",
                                                              str(len(p.text))))
            db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                           [(t, "last_edit", when), (t, "length", str(len(p.text)))])
            db.executemany("insert into back (link, title, type, data) values (?, ?, 'cat', '')",
                           [(t, "category:" + c.strip()) for c in dict.fromkeys(CAT_RE.findall(p.text))])
            n += 1
        db.executemany("insert into yourwiki_pack values (?, ?)",
                       [("format", "anywiki-opennamu-1"), ("range", "all"),
                        ("created", time.strftime("%Y-%m-%d %H:%M:%S")), ("docs", str(n))])
        db.execute("create index history_title_id_index on history (title, id)")
        db.execute("create index data_title_index on data (title)")
        db.commit()
        db.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.write(dbpath, "data.db")
            _write_media(z, media, "images/")
            z.writestr("읽어 주세요.txt", _readme("opennamu", n, bool(media)))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return n


def write_markdown(pages, media, out):
    from wikiconv.markdown import md_name
    folder, seen, n = "wiki-markdown/", set(), 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for p in pages:
            name = base = md_name(p.title)
            k = 2
            while name.lower() in seen:
                name, k = f"{base[:-3]} ({k}).md", k + 1
            seen.add(name.lower())
            z.writestr(_zinfo(folder + name, p.modified), p.text)
            n += 1
        _write_media(z, media, folder)
        z.writestr("읽어 주세요.txt", _readme("markdown", n, bool(media)))
    return n


WRITERS = {"dokuwiki": write_dokuwiki, "mediawiki": write_mediawiki, "opennamu": write_opennamu,
           "markdown": write_markdown}


# ================================================================ 전체 흐름
def convert_archive(path, src, dst, out, progress=None, doku_fnencode="url", **kw):
    """path(zip·폴더·파일) 를 src 형식으로 읽어 dst 형식의 zip(out) 으로 만든다. Report 를 돌려준다."""
    src, dst = norm_fmt(src), norm_fmt(dst)
    if src == dst:
        raise ValueError("읽는 형식과 만들 형식이 같습니다")
    t0 = time.time()
    rep = Report()
    source = open_source(path, src, **kw)
    try:
        idx = MediaIndex(source.media)
        used = set()
        rep.media_total = len(source.media)
        last = [time.time()]

        def stream():
            for p in source.pages():
                q = convert_page(p, src, dst, idx, rep, used)
                rep.pages += 1
                if progress and (time.time() - last[0] > 0.5):
                    last[0] = time.time()
                    progress(rep.pages, source.total)
                yield q
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        part = out + ".part"
        try:
            WRITERS[dst](stream(), source.media, part, **({"fnencode": doku_fnencode} if dst == "dokuwiki" else {}))
            os.replace(part, out)
        finally:
            if os.path.exists(part):
                os.remove(part)
        rep.skipped = source.skipped
        rep.out = out
    finally:
        source.close()
    rep.seconds = time.time() - t0
    if progress:
        progress(rep.pages, rep.pages)
    return rep


# ================================================================ 문서 하나 변환(붙여넣기)
def convert_text(text, src, dst, title=""):
    """붙여넣은 글 하나를 변환한다. (결과, 옮기지 못해 원문으로 남긴 곳 수) 를 돌려준다."""
    src, dst = norm_fmt(src), norm_fmt(dst)
    if src == dst:
        return text, 0
    doc = wikiconv.parse(text, SYNTAX[src], title)
    return wikiconv.render(doc, SYNTAX[dst], title), count_raw(doc.blocks)


def guess_format(text):
    """붙여넣은 글이 어느 위키 문법인지 짐작한다. (형식, 점수들) — 확실하지 않으면 형식은 None."""
    def n(rx, flags=re.M):
        return len(re.findall(rx, text, flags))
    s = {
        "dokuwiki": 6 * n(r"^={6} .+ ={6}\s*$") + 2 * n(r"^  +[*-] ") + 2 * n(r"//[^/\n]+//") + 2 * n(r"^\^ ")
        + 3 * n(r"<code[ >]") + 3 * n(r"\(\([^)]+\)\)") + 2 * n(r"\{\{[^}]+\.(?:png|jpe?g|gif)[^}]*\}\}"),
        "mediawiki": 3 * n(r"^(={2,5})[^=\n].*\1\s*$") + 3 * n(r"'''") + 3 * n(r"^\{\|")
        + 3 * n(r"\[\[(?:Category|분류):") + 2 * n(r"\{\{[^}]+\}\}") + 3 * n(r"<ref[ >]")
        + 3 * n(r"^#REDIRECT", re.M | re.I) + 2 * n(r"^\* ") + 2 * n(r"\[\[(?:File|파일|Image):"),
        "opennamu": 3 * n(r"^=+ .+ =+\s*$") + 3 * n(r"\[\[분류:") + 3 * n(r"\|\|") + 3 * n(r"\{\{\{")
        + 3 * n(r"\[\* ") + 3 * n(r"\[include\(") + 3 * n(r"\[\[파일:") + 2 * n(r"^ \* ") + 2 * n(r"^> ")
        + 2 * n(r"~~[^~]+~~") + 3 * n(r"\[목차\]|\[각주\]|\[br\]"),
        "markdown": 3 * n(r"^#{1,6} ") + 3 * n(r"^```") + 2 * n(r"\*\*[^*\n]+\*\*") + 3 * n(r"\[[^\]]+\]\([^)]+\)")
        + 2 * n(r"^[-*] ") + 3 * n(r"^\|?\s*-{3,}\s*\|") + 2 * n(r"^\d+\. "),
    }
    ranked = sorted(s.items(), key=lambda kv: kv[1], reverse=True)
    if ranked[0][1] == 0 or ranked[0][1] == ranked[1][1]:
        return None, s
    return ranked[0][0], s
