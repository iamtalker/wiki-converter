"""도쿠위키 읽기·쓰기.

도쿠위키는 분류·넘겨주기·틀이 기본으로 없어서, 쓸 때는 문서 끝의 '분류:' 줄과 '넘겨줍니다' 문장으로 나타내고
읽을 때 그 모양을 알아본다(애니위키끼리 왕복해도 잃지 않게).
문서 ID(소문자·이름공간 a:b)와 제목은 엔진 쪽(engines/dokuwiki)이 이어 준다. 여기서는 제목 → ID 변환(doku_id)만 한다.
"""
import re
import urllib.parse

from .tree import CAT, TPL, Doc, InlineScanner, build_lists, cell_inline, merge_text, raw_fallback

PH = "\x00"
PH_RE = re.compile(PH + r"(\d+)" + PH)


INTERWIKI = {  # 도쿠위키가 기본으로 주는 인터위키 목록(conf/interwiki.conf)
    "wp": "https://en.wikipedia.org/wiki/", "wpko": "https://ko.wikipedia.org/wiki/",
    "wpfr": "https://fr.wikipedia.org/wiki/", "wpde": "https://de.wikipedia.org/wiki/",
    "wpes": "https://es.wikipedia.org/wiki/", "wppl": "https://pl.wikipedia.org/wiki/",
    "wpjp": "https://ja.wikipedia.org/wiki/", "wpmeta": "https://meta.wikipedia.org/wiki/",
    "doku": "https://www.dokuwiki.org/", "rfc": "https://tools.ietf.org/html/rfc",
    "man": "http://man.cx/", "phpfn": "https://secure.php.net/",
    "google": "https://www.google.com/search?q={URL}", "go": "https://www.google.com/search?q={URL}&btnI=lucky",
    "ddg": "https://duckduckgo.com/?q={URL}",
}
PAGE_COSMETIC = {"noheader", "nofooter", "noeditbtn", "noeditbutton", "nodate", "nouser", "nolink", "noindent", "indent",
                 "header", "footer", "editbtn", "date", "user", "link", "permalink", "nopermalink"}  # {{page>…&플래그}} 중 화면 표시만 바꾸는 것


def doku_id(t):
    """공통 제목 → 도쿠위키 문서 ID. 분류·틀은 이름공간으로."""
    ns = ""
    for p, name in ((CAT, "분류:"), (TPL, "틀:")):
        if t.startswith(p):
            ns, t = name, t[len(p):]
            break
    t = t.lower().replace(":", "_").replace("/", "_").replace(";", "_")
    t = re.sub(r"[\s\x00-\x1f!\"#$%&'()*+,<=>?@\[\\\]^`{|}~]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_.") or "_"
    if len(t.encode("utf-8")) > 180:  # 파일 이름 한도(255바이트) 안에 들게
        import hashlib
        t = t.encode("utf-8")[:160].decode("utf-8", "ignore").rstrip("_") + "_" + hashlib.sha1(t.encode()).hexdigest()[:8]
    return ns + t


def id_to_title(pid):
    """ID 만 알 때의 제목(엔진이 제목 표를 모를 때만 씀)."""
    pid = pid.strip(":")
    for ns, p in (("분류:", CAT), ("틀:", TPL), ("category:", CAT), ("template:", TPL)):
        if pid.lower().startswith(ns):
            return p + pid[len(ns):].replace("_", " ")
    return pid.replace("_", " ")


# ================================================================ 읽기
class Reader:
    def __init__(self, title, resolve=None):
        self.title = title
        self.resolve = resolve or id_to_title
        self.store = []

    def put(self, kind, node, src=""):
        self.store.append((kind, node, src))
        return f"{PH}{len(self.store) - 1}{PH}"

    def restore(self, s):
        """글자 그대로인 자리(코드·미리 서식)에서는 자리표시를 원래 원문으로 되돌린다."""
        for _ in range(10):
            n = PH_RE.sub(lambda m: self.store[int(m.group(1))][2], s)
            if n == s:
                break
            s = n
        return s

    def protect(self, text):
        text = re.sub(r"<(code|file)(?:\s+([\w+#-]+))?[^>]*>\n?(.*?)\n?</\1>",
                      lambda m: self.put("b", ["code", m.group(2), m.group(3)] if m.group(2) and m.group(2) != "-"
                                         else ["pre", m.group(3)]), text, flags=re.S)
        text = re.sub(r"<nowiki>(.*?)</nowiki>", lambda m: self.put("i", ["t", m.group(1)], m.group(0)), text, flags=re.S)
        text = re.sub(r"%%(.*?)%%", lambda m: self.put("i", ["t", m.group(1)], m.group(0)), text, flags=re.S)
        text = re.sub(r"''(.+?)''", lambda m: self.put("i", ["code", self.unwrap(m.group(1))], m.group(0)), text)
        text = re.sub(r"<html>.*?</html>|<HTML>.*?</HTML>", lambda m: self.put("b", ["raw", "dokuwiki", m.group(0)]),
                      text, flags=re.S)
        text = re.sub(r"\$\$(.+?)\$\$", lambda m: self.put("i", ["math", m.group(1)]), text, flags=re.S)
        text = self.footnotes(text)
        return text

    def unwrap(self, s):
        """''%%글%%'' 처럼 글자 그대로 표시 안에 든 자리표시는 그 글자로."""
        return PH_RE.sub(lambda m: self.store[int(m.group(1))][1][1] if self.store[int(m.group(1))][1][0] == "t"
                         else self.store[int(m.group(1))][2], s)

    def footnotes(self, text):
        out, i = [], 0
        while True:
            j = text.find("((", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            k = text.find("))", j + 2)
            if k < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            out.append(self.put("i", ["fn", "", self.inline(text[j + 2:k])]))
            i = k + 2

    def link(self, m):
        target, _, label = m.group(1).partition("|")
        target = target.strip()
        lab = self.inline(label) if label else None
        if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", target) or target.startswith("mailto:"):
            return ["url", target, lab]
        iw = re.match(r"^(\w+)>(.+)$", target)
        if iw:  # 인터위키(wp>…): 도쿠위키 기본 목록에 있는 것은 주소로 풀고, 이 위키에만 있는 이름은 원문으로
            base = INTERWIKI.get(iw.group(1).lower())
            if base:
                name = iw.group(2).strip()
                if "{URL}" in base:  # 검색 주소 같은 것
                    url = base.replace("{URL}", urllib.parse.quote(name, safe=""))
                else:
                    url = base + urllib.parse.quote(name.replace(" ", "_"), safe="/#:()_-.,'!~*")
                return ["url", url, lab or [["t", name]]]
            return ["raw", "dokuwiki", m.group(0)]
        page, _, anchor = target.partition("#")
        return ["link", self.resolve(page) if page else self.title, lab, anchor]

    def media(self, m):
        inner = m.group(1)
        inc = re.fullmatch(r"page>\s*([^#&|?]+?)\s*(?:&([^#|?]*))?", inner.strip())
        if inc:  # {{page>문서}} 문서 끼워 넣기: 화면 표시용 플래그만 있을 때 문서 포함으로 옮긴다(섹션 지정·내용을 바꾸는 플래그는 원문으로)
            flags = [f.strip().lower() for f in (inc.group(2) or "").split("&") if f.strip()]
            if all(f in PAGE_COSMETIC for f in flags):
                return ["tpl", self.resolve(inc.group(1)), []]
            return ["raw", "dokuwiki", m.group(0)]
        if re.match(r"\w+>", inner.strip()):  # {{gallery>…}} 같은 그 밖의 플러그인 문법
            return ["raw", "dokuwiki", m.group(0)]
        src, _, alt = inner.strip().partition("|")
        src = src.split("?")[0].strip()
        if not re.match(r"(?:[a-z][a-z0-9+.-]*:)?//", src, re.I) and not re.search(r"\.\w{2,5}$", src):
            return ["raw", "dokuwiki", m.group(0)]  # {{top}}·{{TOC}} 같은 플러그인 문법(파일 이름이 아님)
        return ["img", src, alt.strip()]

    def inline(self, s):
        if not hasattr(self, "_sc"):
            w = lambda k: (lambda m: [k, self.inline(m.group(1))])  # noqa: E731
            self._sc = InlineScanner([
                (re.compile(PH + r"(\d+)" + PH), self._ph),
                (re.compile(r"\[\[(.+?)\]\]"), self.link),
                (re.compile(r"\{\{(.+?)\}\}"), self.media),
                (re.compile(r"\\\\(?:\s|$)"), lambda m: ["br"]),
                (re.compile(r"\*\*(.+?)\*\*"), w("b")),
                (re.compile(r"(?<!:)//(.+?)(?<!:)//"), w("i")),
                (re.compile(r"__(.+?)__"), w("u")),
                (re.compile(r"<del>(.*?)</del>", re.S), w("s")),
                (re.compile(r"<sup>(.*?)</sup>", re.S), w("sup")),
                (re.compile(r"<sub>(.*?)</sub>", re.S), w("sub")),
                (re.compile(r"(?<![\w/])https?://[^\s\[\]|<>]+"), lambda m: ["url", m.group(0), None]),
                (re.compile(r"~~[A-Z]+~~"), lambda m: []),
            ])
        return self._sc.scan(s)

    def _ph(self, m):
        kind, node, _ = self.store[int(m.group(1))]
        return ["_block", node] if kind == "b" else node

    # ---------------- 표
    def table(self, lines):
        rows = []
        for ln in lines:
            # 링크·미디어 안의 | 는 칸 구분이 아니므로 잠시 숨긴다
            ln = re.sub(r"\[\[.*?\]\]|\{\{.*?\}\}", lambda m: m.group(0).replace("|", "\x01"), ln.rstrip())
            toks = re.split(r"(\^|\|)", ln)
            cells = []
            for j in range(1, len(toks) - 2, 2):
                sep, t = toks[j], toks[j + 1].replace("\x01", "|")
                if t == "" and cells:
                    if "cs" in cells[-1]:
                        cells[-1]["cs"] += 1
                    continue
                if t.strip() == ":::":
                    cells.append({"_merge": True})
                    continue
                al = ""
                if t.startswith("  ") and t.endswith("  "):
                    al = "center"
                elif t.startswith("  "):
                    al = "right"
                elif t.endswith("  "):
                    al = "left"
                cells.append({"h": sep == "^", "cs": 1, "rs": 1, "al": al, "c": self.inline(t.strip())})
            rows.append(cells)
        # ::: 로 표시한 세로 합침을 위 칸의 rs 로
        width = max((len(r) for r in rows), default=0)
        for col in range(width):
            owner = None
            for r in rows:
                if col >= len(r):
                    continue
                c = r[col]
                if c.get("_merge") and owner:
                    owner["rs"] += 1
                elif not c.get("_merge"):
                    owner = c
        rows = [[c for c in r if not c.get("_merge")] for r in rows]
        return ["table", rows, []]

    # ---------------- 줄 단위
    def blocks(self, text):
        lines = text.split("\n")
        out, para, i, n = [], [], 0, len(lines)

        def flush():
            if para:
                inl = self.inline(" ".join(x.strip() for x in para))
                cur = []
                for node in inl:
                    if node[0] == "_block":
                        if any(x[0] != "t" or x[1].strip() for x in cur):
                            out.append(["p", merge_text(cur)])
                        cur = []
                        out.append(node[1])
                    else:
                        cur.append(node)
                if any(x[0] != "t" or x[1].strip() for x in cur):
                    out.append(["p", merge_text(cur)])
                para.clear()

        while i < n:
            ln = lines[i]
            s = ln.strip()
            m = re.match(r"^\s*(={2,6})\s*(.+?)\s*={2,6}\s*$", ln)
            if m:
                flush()
                out.append(["h", max(1, 7 - len(m.group(1))), self.inline(m.group(2))])
                i += 1
                continue
            if re.fullmatch(r"-{4,}", s):
                flush()
                out.append(["hr"])
                i += 1
                continue
            if PH_RE.fullmatch(s) and self.store[int(PH_RE.fullmatch(s).group(1))][0] == "b":
                flush()
                out.append(self.store[int(PH_RE.fullmatch(s).group(1))][1])
                i += 1
                continue
            if s[:1] in ("^", "|") and ln.lstrip()[:1] in ("^", "|"):
                flush()
                buf = []
                while i < n and lines[i].strip()[:1] in ("^", "|"):
                    buf.append(lines[i].strip())
                    i += 1
                out.append(self.table(buf))
                continue
            m = re.match(r"^( {2,}|\t+)([*-])\s*(.*)$", ln)
            if m:
                flush()
                items = []
                while i < n:
                    mm = re.match(r"^( {2,}|\t+)([*-])\s*(.*)$", lines[i])
                    if not mm:
                        break
                    items.append((len(mm.group(1).expandtabs(2)) // 2, mm.group(2) == "-", mm.group(3)))
                    i += 1
                out.extend(self.build_list(items))
                continue
            if ln.startswith(">"):
                flush()
                buf = []
                while i < n and lines[i].startswith(">"):
                    buf.append(re.sub(r"^>+\s?", "", lines[i]))
                    i += 1
                out.append(["quote", self.blocks("\n".join(buf))])
                continue
            if re.match(r"^( {2,}|\t)\S", ln):
                flush()
                buf = []
                while i < n and re.match(r"^( {2,}|\t)", lines[i]) and lines[i].strip():
                    buf.append(lines[i][2:] if lines[i].startswith("  ") else lines[i][1:])
                    i += 1
                out.append(["pre", self.restore("\n".join(buf))])
                continue
            if not s:
                flush()
                i += 1
                continue
            para.append(ln)
            i += 1
        flush()
        return out

    def build_list(self, items):
        return build_lists(items, lambda body: ["p", self.inline(body)])


def read(text, title="", resolve=None):
    text = (text or "").replace("\r\n", "\n")
    r = Reader(title, resolve)
    m = re.match(r"\s*이 문서는 \[\[([^\]|]+)(?:\|[^\]]*)?\]\] 문서로 넘겨줍니다\.\s*$", text)
    if m:
        page, _, anchor = m.group(1).partition("#")
        return Doc([], [], r.resolve(page) + ("#" + anchor if anchor else ""))
    cats = []
    m = re.search(r"\n-{4,}\n분류: (.+?)\s*$", text)
    if m:
        cats = [re.sub(r"^.*\|", "", x).strip() for x in re.findall(r"\[\[([^\]]+)\]\]", m.group(1))]
        text = text[:m.start()]
    body = r.protect(text)
    return Doc(r.blocks(body), cats, None)


# ================================================================ 쓰기
ESC_RE = re.compile(r"\*\*|//|__|''|\[\[|\]\]|\{\{|\}\}|\(\(|\)\)|\\\\|<(?=[a-zA-Z/])|~~|%%|\^|\||\$\$")


def esc(s):
    """문법으로 읽힐 조각만 %%…%% 로 감싼다(%% 자체는 <nowiki> 로)."""
    return ESC_RE.sub(lambda m: "<nowiki>%%</nowiki>" if m.group(0) == "%%" else f"%%{m.group(0)}%%", s)


class Writer:
    def __init__(self, title):
        self.title = title

    def inline(self, inl):
        out = []
        for n in inl or []:
            k = n[0]
            if k == "t":
                out.append(esc(n[1]))
            elif k == "b":
                out.append("**" + self.inline(n[1]) + "**")
            elif k == "i":
                out.append("//" + self.inline(n[1]) + "//")
            elif k == "u":
                out.append("__" + self.inline(n[1]) + "__")
            elif k == "s":
                out.append("<del>" + self.inline(n[1]) + "</del>")
            elif k in ("sup", "sub"):
                out.append(f"<{k}>" + self.inline(n[1]) + f"</{k}>")
            elif k == "code":
                out.append("''%%" + n[1].replace("%%", "% %") + "%%''")
            elif k == "br":
                out.append("\\\\ ")
            elif k == "math":
                out.append("$$" + n[1] + "$$")
            elif k == "link":
                anchor = n[3] if len(n) > 3 and n[3] else ""
                label = self.inline(n[2]) if n[2] else n[1]
                out.append(f"[[{doku_id(n[1])}{'#' + anchor if anchor else ''}|{label}]]")
            elif k == "url":
                out.append(f"[[{n[1]}|{self.inline(n[2])}]]" if n[2] else f"[[{n[1]}]]")
            elif k == "img":
                out.append("{{" + n[1] + ("|" + n[2] if n[2] else "") + "}}")
            elif k == "fn":
                out.append("((" + self.inline(n[2]) + "))")
            elif k == "tpl":
                out.append(self.tpl(n))
            elif k == "raw":
                fb = None if n[1] == "dokuwiki" else raw_fallback(n[2])
                out.append(n[2] if n[1] == "dokuwiki" else esc(fb) if fb is not None else "''%%" + n[2] + "%%''")
        return "".join(out)

    @staticmethod
    def tpl(n):
        name = n[1]
        if not name.startswith(TPL) and not n[2]:  # 문서 끼워 넣기는 page 플러그인 문법으로
            return "{{page>" + doku_id(name) + "}}"
        args = ", ".join(f"{k}={v}" if k else v for k, v in n[2])
        return f"[[{doku_id(name)}|{name}{'(' + args + ')' if args else ''}]]"

    def block(self, b):
        k = b[0]
        if k == "h":
            eq = "=" * max(2, 7 - b[1])
            return f"{eq} {self.inline(b[2])} {eq}"
        if k == "p":
            s = self.inline(b[1])
            return re.sub(r"^(\s+|>|\^|\||-{4})", lambda m: "%%" + m.group(1) + "%%", s)
        if k == "list":
            return self.list(b, 1)
        if k == "code":
            return f"<code {re.sub(r'[^a-z0-9+#-]', '', (b[1] or 'text').lower()) or 'text'}>\n{b[2]}\n</code>"
        if k == "pre":
            return "<code>\n" + b[1] + "\n</code>"
        if k == "math":
            return "$$" + b[1] + "$$"
        if k == "quote":
            return "\n".join("> " + ln for ln in self.blocks(b[1]).split("\n"))
        if k == "hr":
            return "----"
        if k in ("toc", "refs"):
            return ""
        if k == "table":
            return self.table(b)
        if k == "fold":
            return "**" + self.inline(b[1]) + "**\n\n" + self.blocks(b[2])
        if k == "tpl":
            return self.tpl(b)
        if k == "raw":
            return b[2] if b[1] == "dokuwiki" else "<code>\n" + b[2] + "\n</code>"
        return ""

    def list(self, b, depth):
        out = []
        mark = "-" if b[1] else "*"
        for item in b[2]:
            first = True
            for sub in item:
                if sub[0] == "list":
                    out.append(self.list(sub, depth + 1))
                elif first:
                    out.append("  " * depth + mark + " " +
                               (self.inline(sub[1]) if sub[0] == "p" else self.block(sub).replace("\n", " ")))
                    first = False
                else:
                    out.append("  " * depth + mark + " " +
                               (self.inline(sub[1]) if sub[0] == "p" else self.block(sub).replace("\n", " ")))
        return "\n".join(out)

    def table(self, b):
        out = ["**" + self.inline(b[2]) + "**"] if b[2] else []
        carry = {}  # 열 → (남은 줄 수, 폭)
        for cells in b[1]:
            row, col, pending = [], 0, list(cells)
            while pending or any(k >= col for k in carry):
                if col in carry:
                    left, w = carry[col]
                    row.append(("|", ":::"))
                    row.extend([("|", "")] * (w - 1))
                    if left - 1:
                        carry[col] = (left - 1, w)
                    else:
                        del carry[col]
                    col += w
                    continue
                if not pending:
                    col += 1
                    if col > 200:
                        break
                    continue
                c = pending.pop(0)
                t = self.inline(cell_inline(c)) or " "
                t = {"center": f"  {t}  ", "right": f"  {t} ", "left": f" {t}  "}.get(c.get("al"), f" {t} ")
                sep = "^" if c.get("h") else "|"
                row.append((sep, t))
                row.extend([(sep, "")] * (c.get("cs", 1) - 1))
                if c.get("rs", 1) > 1:
                    carry[col] = (c["rs"] - 1, c.get("cs", 1))
                col += c.get("cs", 1)
            line = "".join(sep + t for sep, t in row) + (row[-1][0] if row else "|")
            out.append(line)
        return "\n".join(out)

    def blocks(self, blocks):
        return "\n\n".join(x for x in (self.block(b) for b in blocks) if x != "")


def write(doc, title=""):
    if doc.redirect:
        page, _, anchor = doc.redirect.partition("#")
        return f"이 문서는 [[{doku_id(page)}{'#' + anchor if anchor else ''}|{page}]] 문서로 넘겨줍니다."
    w = Writer(title)
    body = w.blocks(doc.blocks)
    if doc.cats:
        body += "\n\n----\n분류: " + ", ".join(f"[[{doku_id(CAT + c)}|{c}]]" for c in doc.cats)
    return body.strip("\n") + "\n"
