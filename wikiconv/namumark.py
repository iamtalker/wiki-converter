"""나무마크(openNAMU) 읽기·쓰기."""
import re

from .tree import CAT, Doc, InlineScanner, build_lists, flatten_blocks, merge_text, raw_fallback, text_of

PH = "\x00"
PH_RE = re.compile(PH + r"(\d+)" + PH)
CTRL_RE = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f]")


def resolve(cur, target):
    """나무마크 상대 링크(/하위, ../)를 풀고, 문단 앵커(#s-1)는 버린다. (문서, 앵커)."""
    anchor = ""
    if target.startswith("#"):
        return cur, target[1:]
    if "#" in target:
        target, anchor = target.split("#", 1)
    if target.startswith("../"):
        base = cur
        while target.startswith("../"):
            base, target = (base.rsplit("/", 1)[0] if "/" in base else base), target[3:]
        target = base + ("/" + target if target else "")
    elif target.startswith("/"):
        target = cur + target
    if anchor.startswith("s-"):
        anchor = ""
    return target.strip(), anchor


# ================================================================ 읽기
class Reader:
    def __init__(self, title):
        self.title = title
        self.store = []   # 자리표시에 맡겨 둔 노드: ("i", 인라인 노드) 또는 ("b", 블록 노드)
        self.cats = []

    def put(self, kind, node, src=""):
        self.store.append((kind, node, src))
        return f"{PH}{len(self.store) - 1}{PH}"

    def restore(self, s):
        """글자 그대로인 자리(코드·문자 그대로 블록)에서는 자리표시(\\ 이스케이프)를 원래 원문으로 되돌린다."""
        for _ in range(10):
            n = PH_RE.sub(lambda m: self.store[int(m.group(1))][2], s)
            if n == s:
                break
            s = n
        return s

    # ---------------- {{{ }}}
    def braces(self, text):
        out, i = [], 0
        while True:
            j = text.find("{{{", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            k, depth = j + 3, 1
            while depth and k < len(text):
                if text.startswith("{{{", k):
                    depth, k = depth + 1, k + 3
                elif text.startswith("}}}", k):
                    depth, k = depth - 1, k + 3
                else:
                    k += 1
            if depth:
                out.append(text[j:])
                return "".join(out)
            out.append(self.brace(text[j + 3:k - 3]))
            i = k

    def brace(self, inner):
        raw = self.restore(inner)
        multi = "\n" in inner
        head, _, body = inner.partition("\n") if multi else (inner, "", "")
        h = head.strip()
        low = h.lower()
        if low.startswith("#!syntax"):
            body = self.restore(body)
            return self.put("b", ["code", h[8:].strip(), body[:-1] if body.endswith("\n") else body])
        if low.startswith("#!html"):
            return self.put("b" if multi else "i", ["raw", "namumark", "{{{" + raw + "}}}"])
        if low.startswith("#!folding"):
            summary = self.inline(self.braces(h[9:].strip())) or [["t", "펼치기 · 접기"]]
            return self.put("b", ["fold", summary, self.blocks(self.braces(body))])
        if low.startswith("#!wiki"):
            return self.put("b", ["_group", self.blocks(self.braces(body))])
        m = re.match(r"([+-])([1-5])\s", inner)
        if m:  # 글자 크기: 크기는 옮기지 않고 내용만
            return self.put("i", ["_group", self.inline(self.braces(inner[m.end():]))])
        m = re.match(r"#(?:[0-9a-zA-Z]+)(?:,#?[0-9a-zA-Z]+)?\s", inner)
        if m:  # 글자 색: 색은 옮기지 않고 내용만
            rest = inner[m.end():]
            if "\n" in rest:
                return self.put("b", ["_group", self.blocks(self.braces(rest))])
            return self.put("i", ["_group", self.inline(self.braces(rest))])
        if multi:
            text = raw[1:] if raw.startswith("\n") else raw
            return self.put("b", ["pre", text[:-1] if text.endswith("\n") else text])
        return self.put("i", ["code", raw])

    # ---------------- [* 각주]
    def footnotes(self, text):
        out, i = [], 0
        while True:
            j = text.find("[*", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            k, depth = j + 1, 1
            while depth and k < len(text):
                depth += text[k] == "["
                depth -= text[k] == "]"
                k += 1
            if depth:
                out.append(text[j:])
                return "".join(out)
            inner = text[j + 2:k - 1]
            name, sp, body = inner.partition(" ")
            if not sp:
                name, body = inner, ""
            out.append(self.put("i", ["fn", name, self.inline(self.footnotes(body))]))
            i = k

    # ---------------- 줄 안
    def link(self, m):
        target, _, label = m.group(1).partition("|")
        target = target.strip()
        lab = self.flat(self.inline(label)) if label else None
        if re.match(r"(?i)https?://", target):
            return ["url", target, lab]
        colon = target.startswith(":")
        if colon:
            target = target[1:]
        if target.startswith(("파일:", "이미지:")):
            alt = "" if not label or re.fullmatch(r"\s*(\w+=[^&]*&?)+\s*", label) else text_of(lab)  # width=… 같은 옵션은 설명이 아님
            return ["img", target.split(":", 1)[1], alt]
        if target.startswith(CAT) and not colon:
            name = target[len(CAT):].split("#")[0].strip()
            if name and name not in self.cats:
                self.cats.append(name)
            return []
        page, anchor = resolve(self.title, target)
        return ["link", page, lab, anchor]

    VIDEOS = {"youtube": ("유튜브", "https://www.youtube.com/watch?v={}"),
              "nicovideo": ("니코니코 동화", "https://www.nicovideo.jp/watch/{}"),
              "vimeo": ("비메오", "https://vimeo.com/{}"),
              "kakaotv": ("카카오TV", "https://tv.kakao.com/v/{}"),
              "navertv": ("네이버TV", "https://tv.naver.com/v/{}")}

    def macro(self, m):
        name, arg = m.group(1).lower(), (m.group(3) or "").strip()
        if name in ("목차", "tableofcontents"):
            return ["_toc"]
        if name in ("각주", "footnote"):
            return ["_refs"]
        if name == "br":
            return ["br"]
        if name in ("clearfix", "pagecount"):
            return []
        if name == "include":
            parts = [p.strip() for p in arg.split(",")]
            args = []
            for p in parts[1:]:
                if not p:
                    continue
                k, eq, v = p.partition("=")
                args.append([k.strip(), v.strip()] if eq else [None, p])
            return ["tpl", parts[0], args]
        if name in self.VIDEOS:
            label, fmt = self.VIDEOS[name]
            return ["url", fmt.format(arg.split(",")[0].strip()), [["t", f"▶ {label}에서 보기"]]]
        if name == "math":
            return ["math", arg]
        if name == "ruby":
            base, _, rest = arg.partition(",")
            r = re.search(r"ruby\s*=\s*([^,]+)", rest)
            return ["t", f"{base}({r.group(1).strip()})" if r else base]
        return ["raw", "namumark", m.group(0)]

    def _wrap(self, kind):
        return lambda m: [kind, self.inline(m.group(1))]

    def inline(self, s):
        if not hasattr(self, "_scanner"):
            self._scanner = InlineScanner([
                (re.compile(PH + r"(\d+)" + PH), self._ph),
                (re.compile(r"<math>(.*?)</math>", re.S), lambda m: ["math", m.group(1)]),
                (re.compile(r"\[\[((?:\[\[[^\]]*\]\]|[^\[\]]|\[(?!\[)|\](?!\]))+?)\]\]"), self.link),
                (re.compile(r"\[([a-zA-Z가-힣]+)(\((.*?)\))?\]"), self.macro),
                (re.compile(r"'''(.+?)'''"), self._wrap("b")),
                (re.compile(r"''(.+?)''"), self._wrap("i")),
                (re.compile(r"__(.+?)__"), self._wrap("u")),
                (re.compile(r"~~(.+?)~~"), self._wrap("s")),
                (re.compile(r"(?<!-)--(?!-)(.+?)(?<!-)--(?!-)"), self._wrap("s")),
                (re.compile(r"\^\^(.+?)\^\^"), self._wrap("sup")),
                (re.compile(r",,(.+?),,"), self._wrap("sub")),
            ])
        out = []
        for n in self._scanner.scan(s):
            if n[0] == "_group":
                out.extend(n[1])
            else:
                out.append(n)
        return merge_text(out)

    @staticmethod
    def flat(inl):
        """링크 글 등 줄 안에 블록이 섞였으면(예: [[문서|{{{#!wiki …}}}]]) 블록의 글만 줄 안으로 편다."""
        out = []
        for n in inl:
            if n[0] == "_block":
                node = n[1]
                for b in (node[1] if node[0] == "_group" else [node]):
                    if b[0] == "p":
                        out.extend(b[1])
            else:
                out.append(n)
        return merge_text(out)

    def _ph(self, m):
        kind, node, _ = self.store[int(m.group(1))]
        if kind == "b":  # 줄 가운데 블록이 온 경우(드묾): 블록을 글자로 볼 수 없으니 원문 보존
            return ["_block", node]
        return node

    # ---------------- 표
    def table(self, lines):
        caption = []
        if lines and not lines[0].startswith("||"):
            m = re.match(r"\|([^|]*)\|(.*)", lines[0])
            if m:
                caption, lines[0] = self.inline(m.group(1)), "||" + m.group(2)
        rows_src, buf = [], ""
        for ln in lines:
            buf = buf + "\n" + ln if buf else ln
            if buf.rstrip().endswith("||") and len(buf.strip()) > 2:
                rows_src.append(buf.strip())
                buf = ""
        if buf:
            rows_src.append(buf.strip() + "||")
        rows = []
        for r in rows_src:
            cells, span = [], 1
            for p in r[2:-2].split("||"):
                if p == "":
                    span += 1
                    continue
                c = {"h": False, "cs": span, "rs": 1, "al": "", "c": []}
                span = 1
                while True:
                    m = re.match(r"\s*<([^<>\n]*)>", p)
                    if not m:
                        break
                    a = m.group(1).strip()
                    p = p[m.end():]
                    if re.fullmatch(r"-\d+", a):
                        c["cs"] = int(a[1:])
                    elif re.fullmatch(r"[\^v]?\|\d+", a):
                        c["rs"] = int(a.split("|")[1])
                    elif a in (":", "(", ")"):
                        c["al"] = {":": "center", "(": "left", ")": "right"}[a]
                body = p.strip()
                if any(self.store[int(x)][0] == "b" for x in PH_RE.findall(body)):
                    c["b"] = self.blocks(body)  # 칸 안에 접기·표 같은 블록이 있음
                    c["c"] = flatten_blocks(c["b"])
                else:
                    inl = []
                    for k, part in enumerate(body.split("\n")):
                        if k:
                            inl.append(["br"])
                        inl.extend(self.flat(self.inline(part)))
                    c["c"] = merge_text(inl)
                cells.append(c)
            rows.append(cells)
        return ["table", rows, caption]

    # ---------------- 줄 단위
    LIST_RE = re.compile(r"^(\s+)(\*|1\.|a\.|A\.|i\.|I\.)(?:#\d+)?\s?(.*)$")
    HEAD_RE = re.compile(r"^(={1,6})(#?)\s*(.+?)\s*\2\1\s*$")

    def blocks(self, text):
        lines = text.split("\n")
        out, para, i, n = [], [], 0, len(lines)

        def flush():
            if para:
                inl = []
                for k, ln in enumerate(para):
                    if k:
                        inl.append(["br"])
                    inl.extend(self.inline(ln))
                self.emit_para(out, merge_text(inl))
                para.clear()

        while i < n:
            ln = lines[i]
            s = ln.strip()
            if ln.startswith("||") or re.match(r"^\|[^|]+\|\|", ln):
                flush()
                j, buf = i + 1, [ln]
                while j < n and (not buf[-1].rstrip().endswith("||") or lines[j].startswith("||")):
                    buf.append(lines[j])
                    j += 1
                out.append(self.table(buf))
                i = j
                continue
            m = self.HEAD_RE.match(ln)
            if m:
                flush()
                out.append(["h", len(m.group(1)), self.inline(m.group(3))])
                i += 1
                continue
            if re.fullmatch(r"-{4,9}", s):
                flush()
                out.append(["hr"])
                i += 1
                continue
            m = self.LIST_RE.match(ln)
            if m:
                flush()
                items = []
                while i < n:
                    m = self.LIST_RE.match(lines[i])
                    if not m:
                        break
                    items.append((max(1, len(m.group(1))), m.group(2) != "*", m.group(3)))
                    i += 1
                out.extend(self.build_list(items))
                continue
            m = re.match(r"^>+\s?(.*)$", ln)
            if m:
                flush()
                buf = []
                while i < n and re.match(r"^>", lines[i]):
                    buf.append(re.sub(r"^>\s?", "", lines[i]))
                    i += 1
                out.append(["quote", self.blocks("\n".join(buf))])
                continue
            if PH_RE.fullmatch(s) and self.store[int(PH_RE.fullmatch(s).group(1))][0] == "b":
                flush()
                node = self.store[int(PH_RE.fullmatch(s).group(1))][1]
                out.extend(node[1] if node[0] == "_group" else [node])
                i += 1
                continue
            if not s:
                flush()
                i += 1
                continue
            para.append(ln.strip() if ln.startswith(" ") else ln)
            i += 1
        flush()
        return out

    def emit_para(self, out, inl):
        """문단 안의 특수 노드(목차·각주 자리·블록 자리표시)를 블록으로 꺼낸다."""
        cur = []
        for node in inl:
            if node[0] in ("_toc", "_refs", "_block"):
                if any(x[0] != "br" and (x[0] != "t" or x[1].strip()) for x in cur):
                    out.append(["p", self.trim(cur)])
                cur = []
                if node[0] == "_block" and node[1][0] == "_group":
                    out.extend(node[1][1])
                else:
                    out.append(["toc"] if node[0] == "_toc" else ["refs"] if node[0] == "_refs" else node[1])
            else:
                cur.append(node)
        if any(x[0] != "br" and (x[0] != "t" or x[1].strip()) for x in cur):
            out.append(["p", self.trim(cur)])

    @staticmethod
    def trim(inl):
        while inl and inl[0][0] == "br":
            inl = inl[1:]
        while inl and inl[-1][0] == "br":
            inl = inl[:-1]
        return inl

    def build_list(self, items):
        return build_lists(items, lambda body: ["p", self.inline(body)])


def read(text, title=""):
    r = Reader(title)
    text = CTRL_RE.sub("", (text or "").replace("\r\n", "\n"))
    m = re.match(r"\s*#redirect\s+(.+)", text, re.I)
    if m:
        page, anchor = resolve(title, m.group(1).split("\n")[0].strip())
        return Doc([], [], page + ("#" + anchor if anchor else ""))
    text = re.sub(r"(?m)^##.*\n?", "", text)
    text = re.sub(r"\\(.)", lambda m: r.put("i", ["t", m.group(1)], m.group(0)), text)
    text = r.braces(text)
    text = r.footnotes(text)
    return Doc(r.blocks(text), r.cats, None)


# ================================================================ 쓰기
ESC_RE = re.compile(r"'''|''|__|~~|--|\^\^|,,|\{\{\{|\}\}\}|\[\[|\]\]|\[\*|\[[a-zA-Z가-힣]+\]|\[(?=[a-zA-Z가-힣]+\()|<math>")
LINE_START_RE = re.compile(r"^(\s|=|\|\||>|-{4}|##|#redirect)", re.I)


def esc(s):
    s = s.replace("\\", "\\\\")

    def one(m):
        g = m.group(0)
        if len(g) > 2 and g[0] == "[" and g[-1] == "]" and not g.startswith(("[[", "[*")):
            return "\\[" + g[1:-1] + "\\]"  # [NTR] 처럼 닫는 대괄호도 막아야 각주 안에서 각주가 끝나 버리지 않는다
        return "\\" + g
    return ESC_RE.sub(one, s)


class Writer:
    def __init__(self, title):
        self.title = title

    def inline(self, inl):
        out = []
        for n in inl or []:
            k = n[0]
            if k == "t":
                out.append(esc(n[1]))
            elif k in ("b", "i", "u", "s", "sup", "sub"):
                mark = {"b": "'''", "i": "''", "u": "__", "s": "~~", "sup": "^^", "sub": ",,"}[k]
                out.append(mark + self.inline(n[1]) + mark)
            elif k == "code":
                out.append("{{{" + n[1].replace("}}}", "} }}") + "}}}")
            elif k == "br":
                out.append("[br]")
            elif k == "math":
                out.append("<math>" + n[1] + "</math>")
            elif k == "link":
                target = n[1] + ("#" + n[3] if len(n) > 3 and n[3] else "")
                if n[1].startswith(CAT):
                    target = ":" + target
                label = self.inline(n[2]) if n[2] else ""
                out.append(f"[[{target}|{label}]]" if label and label != n[1] else f"[[{target}]]")
            elif k == "url":
                label = self.inline(n[2]) if n[2] else ""
                out.append(f"[[{n[1]}|{label}]]" if label else f"[[{n[1]}]]")
            elif k == "img":
                out.append(f"[[파일:{n[1]}]]" if not re.match(r"https?://", n[1]) else f"[[{n[1]}]]")
            elif k == "fn":
                name = n[1] if n[1] and not n[1].isdigit() else ""
                out.append(f"[*{name} {self.inline(n[2])}]")
            elif k == "tpl":
                out.append(self.tpl(n))
            elif k == "raw":
                fb = None if n[1] == "namumark" else raw_fallback(n[2])
                out.append(n[2] if n[1] == "namumark" else esc(fb) if fb is not None
                           else "{{{" + n[2].replace("}}}", "} }}") + "}}}")
        return "".join(out)

    @staticmethod
    def tpl(n):
        name = n[1]  # 이름에 '틀:' 이 있으면 틀, 없으면 일반 문서를 끼워 넣는 것(공통 구조의 약속)
        args = [(f"{k}={v}" if k else v).replace(",", "\\,") for k, v in n[2]]
        return "[include(" + ", ".join([name] + args) + ")]"

    def para(self, inl):
        lines = self.inline(inl).split("[br]")
        return "\n".join("\\" + ln if LINE_START_RE.match(ln) else ln for ln in lines)

    def block(self, b, depth=0):
        k = b[0]
        if k == "h":
            eq = "=" * b[1]
            return f"{eq} {self.inline(b[2])} {eq}"
        if k == "p":
            return self.para(b[1])
        if k == "list":
            return self.list(b, 1)
        if k == "code":
            return "{{{#!syntax " + (b[1] or "text") + "\n" + b[2] + "\n}}}"
        if k == "pre":
            return "{{{\n" + b[1].replace("}}}", "} }}") + "\n}}}"
        if k == "math":
            return "<math>" + b[1] + "</math>"
        if k == "quote":
            return "\n".join("> " + ln for ln in self.blocks(b[1]).split("\n"))
        if k == "hr":
            return "----"
        if k == "toc":
            return "[목차]"
        if k == "refs":
            return "[각주]"
        if k == "table":
            return self.table(b)
        if k == "fold":
            return "{{{#!folding " + self.inline(b[1]) + "\n" + self.blocks(b[2]) + "\n}}}"
        if k == "tpl":
            return self.tpl(b)
        if k == "raw":
            return b[2] if b[1] == "namumark" else "{{{\n" + b[2].replace("}}}", "} }}") + "\n}}}"
        return ""

    def list(self, b, depth):
        out = []
        for item in b[2]:
            first = True
            for sub in item:
                if sub[0] == "list":
                    out.append(self.list(sub, depth + 1))
                elif first:
                    mark = "1." if b[1] else "*"
                    out.append(" " * depth + mark + " " + (self.inline(sub[1]) if sub[0] == "p"
                                                          else self.block(sub).replace("\n", " ")))
                    first = False
                else:
                    out.append(" " * (depth + 1) + (self.inline(sub[1]) if sub[0] == "p" else self.block(sub)))
        return "\n".join(out)

    def table(self, b):
        out = []
        rows, caption = b[1], b[2]
        for r, cells in enumerate(rows):
            line = "||"
            if r == 0 and caption:
                line = "|" + self.inline(caption) + "||"
            for c in cells:
                attrs = ""
                if c.get("cs", 1) > 1:
                    attrs += f"<-{c['cs']}>"
                if c.get("rs", 1) > 1:
                    attrs += f"<|{c['rs']}>"
                if c.get("al"):
                    attrs += {"center": "<:>", "left": "<(>", "right": "<)>"}.get(c["al"], "")
                if c.get("b"):
                    txt = "{{{#!wiki\n" + self.blocks(c["b"]) + "\n}}}"
                else:
                    txt = self.inline(c["c"]).replace("||", "\\||")
                if c.get("h"):
                    txt = "'''" + txt + "'''" if txt else txt
                line += f"{attrs} {txt} ||"
            out.append(line)
        return "\n".join(out)

    def blocks(self, blocks):
        return "\n\n".join(x for x in (self.block(b) for b in blocks) if x != "")


def write(doc, title=""):
    if doc.redirect:
        return "#redirect " + doc.redirect
    w = Writer(title)
    body = w.blocks(doc.blocks)
    if doc.cats:
        body += "\n\n" + "\n".join(f"[[{CAT}{c}]]" for c in doc.cats)
    return body.strip("\n")
