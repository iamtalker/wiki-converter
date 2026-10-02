"""미디어위키(위키텍스트) 읽기·쓰기."""
import html
import re

from .tree import CAT, TPL, Doc, InlineScanner, build_lists, flatten_blocks, merge_text, raw_fallback

PH = "\x00"
PH_RE = re.compile(PH + r"(\d+)" + PH)
CAT_NS = ("category:", "분류:")
TPL_NS = ("template:", "틀:")
FILE_NS = ("file:", "image:", "파일:", "그림:")


def to_common(title):
    """미디어위키 제목 → 공통 제목(분류·틀 이름공간)."""
    t = title.strip().replace("_", " ")
    low = t.lower()
    for p in CAT_NS:
        if low.startswith(p):
            return CAT + t[len(p):].strip()
    for p in TPL_NS:
        if low.startswith(p):
            return TPL + t[len(p):].strip()
    return t[:1].upper() + t[1:] if t[:1].isascii() else t


def mw_title(t):
    """공통 제목 → 미디어위키 제목."""
    if t.startswith(CAT):
        t = "Category:" + t[len(CAT):]
    elif t.startswith(TPL):
        t = "Template:" + t[len(TPL):]
    return t.translate(str.maketrans("#<>[]|{}", "＃＜＞［］｜｛｝")).strip() or "_"


# ================================================================ 읽기
class Reader:
    def __init__(self, title):
        self.title = title
        self.store = []
        self.cats = []
        self.refs = {}

    def put(self, kind, node):
        self.store.append((kind, node))
        return f"{PH}{len(self.store) - 1}{PH}"

    def unwrap(self, s):
        """글자 그대로인 자리(pre·코드) 안의 자리표시는 그 글자로(<nowiki> 는 벗긴다)."""
        def one(m):
            kind, node = self.store[int(m.group(1))]
            return node[1] if node[0] in ("t", "code", "math") and isinstance(node[1], str) else ""
        return PH_RE.sub(one, s)

    # ---------------- 먼저 빼 두는 것들
    def protect(self, text):
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"<code><nowiki>(.*?)</nowiki></code>", lambda m: self.put("i", ["code", html.unescape(m.group(1))]),
                      text, flags=re.S | re.I)
        text = re.sub(r"<nowiki>(.*?)</nowiki>", lambda m: self.put("i", ["t", html.unescape(m.group(1))]), text,
                      flags=re.S | re.I)
        text = re.sub(r"<pre[^>]*>(.*?)</pre>", lambda m: self.put("b", ["pre", html.unescape(self.unwrap(m.group(1).strip("\n")))]),
                      text, flags=re.S | re.I)
        text = re.sub(r'<(syntaxhighlight|source)(?:\s+[^>]*?lang="?([\w+#-]+)"?)?[^>]*>(.*?)</\1>',
                      lambda m: self.put("b", ["code", m.group(2) or "", self.unwrap(m.group(3).strip("\n"))]), text,
                      flags=re.S | re.I)
        text = re.sub(r"<math[^>]*>(.*?)</math>", lambda m: self.put("i", ["math", m.group(1).strip()]), text,
                      flags=re.S | re.I)
        text = re.sub(r"<code>(.*?)</code>", lambda m: self.put("i", ["code", html.unescape(self.unwrap(m.group(1)))]), text,
                      flags=re.S | re.I)
        text = re.sub(r"<ref(\s[^>]*)?/>", lambda m: self.ref(m.group(1), None), text, flags=re.I)
        text = re.sub(r"<ref(\s[^>]*)?>(.*?)</ref>", lambda m: self.ref(m.group(1), m.group(2)), text, flags=re.S | re.I)
        text = re.sub(r"<references\s*/>|<references>.*?</references>|\{\{(?:reflist|각주)[^}]*\}\}",
                      lambda m: self.put("b", ["refs"]), text, flags=re.S | re.I)
        text = re.sub(r"__(NO)?TOC__|__FORCETOC__", lambda m: "" if m.group(1) else self.put("b", ["toc"]), text)
        text = re.sub(r"__[A-Z]+__", "", text)
        text = self.templates(text)
        text = self.tables(text)
        text = self.folds(text)
        return text

    def ref(self, attrs, body):
        m = re.search(r'name\s*=\s*"?([^"/>]+)"?', attrs or "")
        name = m.group(1).strip() if m else ""
        if body is None:
            return self.put("i", ["fn", name, self.refs.get(name, [])])
        inl = self.inline(self.templates(body))
        if name:
            self.refs[name] = inl
        return self.put("i", ["fn", name, inl])

    def templates(self, text):
        out, i = [], 0
        while True:
            j = text.find("{{", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            k, depth = j + 2, 1
            while depth and k < len(text):
                if text.startswith("{{", k):
                    depth, k = depth + 1, k + 2
                elif text.startswith("}}", k):
                    depth, k = depth - 1, k + 2
                else:
                    k += 1
            if depth:
                out.append(text[j:])
                return "".join(out)
            inner = text[j + 2:k - 2]
            if not inner.lstrip().startswith(":") and (inner.startswith("{") or inner.lstrip().startswith("#")
                                                       or re.match(r"\s*[A-Z]+[:|]", inner) or inner.strip().isupper()):
                out.append(self.put("i", ["raw", "mediawiki", "{{" + inner + "}}"]))
            else:
                parts = self.split_args(inner)
                name = parts[0].strip()
                args = []
                for p in parts[1:]:
                    k2, eq, v = p.partition("=")
                    args.append([k2.strip(), v.strip()] if eq and "[" not in k2 and "{" not in k2 else [None, p.strip()])
                if name.startswith(":"):
                    name = name[1:]
                elif not re.match(r"(?i)(template|틀):", name):
                    name = TPL + name
                else:
                    name = to_common(name)
                out.append(self.put("i", ["tpl", name, args]))
            i = k

    @staticmethod
    def split_args(inner):
        parts, depth, cur, i = [], 0, [], 0
        while i < len(inner):
            two = inner[i:i + 2]
            if two in ("{{", "[["):
                depth += 1
                cur.append(two)
                i += 2
                continue
            if two in ("}}", "]]"):
                depth -= 1
                cur.append(two)
                i += 2
                continue
            if inner[i] == "|" and depth == 0:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(inner[i])
            i += 1
        parts.append("".join(cur))
        return parts

    def tables(self, text):
        out, i = [], 0
        while True:
            m = re.search(r"(?m)^\s*\{\|", text[i:])
            if not m:
                out.append(text[i:])
                return "".join(out)
            j = i + m.start()
            out.append(text[i:j])
            k, depth = j + len(m.group(0)), 1
            while depth and k < len(text):
                mm = re.search(r"(?m)^\s*(\{\||\|\})", text[k:])
                if not mm:
                    k = len(text)
                    break
                depth += 1 if mm.group(1) == "{|" else -1
                k += mm.end()
            out.append("\n" + self.put("b", self.table(text[j:k])) + "\n")
            i = k

    def table(self, src):
        lines = src.strip().split("\n")[1:]
        if lines and lines[-1].strip().startswith("|}"):
            lines = lines[:-1]
        rows, caption, cur = [], [], None
        for ln in lines:
            s = ln.strip()
            if s.startswith("|+"):
                caption = self.inline(s[2:].strip())
                continue
            if s.startswith("|-"):
                cur = []
                rows.append(cur)
                continue
            if s.startswith("!") or s.startswith("|"):
                if cur is None:
                    cur = []
                    rows.append(cur)
                head = s.startswith("!")
                body = s[1:]
                sep = r"!!|\|\|" if head else r"\|\|"
                for cell in re.split(sep, body):
                    attrs, content = "", cell
                    parts = self.split_args(cell)
                    if len(parts) > 1 and "=" in parts[0] and "[[" not in parts[0]:
                        attrs, content = parts[0], "|".join(parts[1:])
                    cs = re.search(r'colspan\s*=\s*"?(\d+)', attrs)
                    rs = re.search(r'rowspan\s*=\s*"?(\d+)', attrs)
                    al = re.search(r"text-align:\s*(\w+)", attrs) or re.search(r'align\s*=\s*"?(\w+)', attrs)
                    cur.append({"h": head, "cs": int(cs.group(1)) if cs else 1, "rs": int(rs.group(1)) if rs else 1,
                                "al": al.group(1) if al else "", "_src": [content.strip()]})
            elif cur:  # 칸 내용이 여러 줄
                cur[-1].setdefault("_src", []).append(ln)
        for r in rows:
            for c in r:
                src = "\n".join(c.pop("_src", [])).strip("\n")
                if "\n" in src or any(self.store[int(x)][0] == "b" for x in PH_RE.findall(src)):
                    c["b"] = self.blocks(src)  # 칸 안에 목록·표 같은 블록
                    c["c"] = flatten_blocks(c["b"])
                else:
                    c["c"] = self.inline(src)
        return ["table", [r for r in rows if r], caption]

    def folds(self, text):
        rx = re.compile(r'<div class="mw-collapsible[^"]*">\s*(.*?)\s*<div class="mw-collapsible-content">(.*?)</div>\s*</div>',
                        re.S)
        return rx.sub(lambda m: "\n" + self.put("b", ["fold", self.inline(m.group(1)),
                                                        self.blocks(m.group(2))]) + "\n", text)

    # ---------------- 줄 안
    def link(self, m):
        inner = m.group(1)
        parts = self.split_args(inner)
        target, label = parts[0].strip(), "|".join(parts[1:]) if len(parts) > 1 else None
        colon = target.startswith(":")
        if colon:
            target = target[1:]
        low = target.lower()
        if low.startswith(FILE_NS) and not colon:
            opts = [p for p in parts[1:] if not re.fullmatch(r"\s*(thumb|thumbnail|frame|frameless|left|right|center|none|"
                                                              r"upright|border|\d+px|x\d+px|\d+x\d+px)\s*", p)]
            return ["img", target.split(":", 1)[1].strip(), opts[-1].strip() if opts else ""]
        if low.startswith(CAT_NS) and not colon:
            name = target.split(":", 1)[1].split("|")[0].strip()
            if name and name not in self.cats:
                self.cats.append(name)
            return []
        page, _, anchor = target.partition("#")
        page = to_common(page) if page else self.title
        lab = self.inline(label) if label else None
        return ["link", page, lab, anchor.replace("_", " ")]

    def inline(self, s):
        if not hasattr(self, "_sc"):
            b = lambda m: ["b", self.inline(m.group(1))]  # noqa: E731
            self._sc = InlineScanner([
                (re.compile(PH + r"(\d+)" + PH), self._ph),
                (re.compile(r"\[\[((?:[^\[\]]|\[\[[^\]]*\]\])+?)\]\]"), self.link),
                (re.compile(r"\[((?:https?|ftp|mailto):[^\s\]]+)(?:\s+([^\]]*))?\]"),
                 lambda m: ["url", m.group(1), self.inline(m.group(2)) if m.group(2) else None]),
                (re.compile(r"(?<![\"'=\[])\bhttps?://[^\s<>\[\]\"']+"), lambda m: ["url", m.group(0), None]),
                (re.compile(r"'''''(.+?)'''''"), lambda m: ["b", [["i", self.inline(m.group(1))]]]),
                (re.compile(r"'''(.+?)'''"), b),
                (re.compile(r"''(.+?)''"), lambda m: ["i", self.inline(m.group(1))]),
                (re.compile(r"<br\s*/?>", re.I), lambda m: ["br"]),
                (re.compile(r"<(?:u|ins)>(.*?)</(?:u|ins)>", re.I | re.S), lambda m: ["u", self.inline(m.group(1))]),
                (re.compile(r"<(?:s|del|strike)>(.*?)</(?:s|del|strike)>", re.I | re.S),
                 lambda m: ["s", self.inline(m.group(1))]),
                (re.compile(r"<sup>(.*?)</sup>", re.I | re.S), lambda m: ["sup", self.inline(m.group(1))]),
                (re.compile(r"<sub>(.*?)</sub>", re.I | re.S), lambda m: ["sub", self.inline(m.group(1))]),
                (re.compile(r"<(?:b|strong)>(.*?)</(?:b|strong)>", re.I | re.S), b),
                (re.compile(r"<(?:i|em)>(.*?)</(?:i|em)>", re.I | re.S), lambda m: ["i", self.inline(m.group(1))]),
                (re.compile(r"</?(?:span|div|font|center|small|big)[^>]*>", re.I), lambda m: []),
                (re.compile(r"&(#\d+|#x[0-9a-fA-F]+|\w+);"), lambda m: ["t", html.unescape(m.group(0))]),
            ])
        out = []
        for n in self._sc.scan(s):
            if n[0] == "_block":
                out.append(n)
            else:
                out.append(n)
        return merge_text(out)

    def _ph(self, m):
        kind, node = self.store[int(m.group(1))]
        return ["_block", node] if kind == "b" else node

    # ---------------- 줄 단위
    def blocks(self, text):
        lines = text.split("\n")
        out, para, i, n = [], [], 0, len(lines)

        def flush():
            if para:
                self.emit_para(out, self.inline(" ".join(x.strip() for x in para)))
                para.clear()

        while i < n:
            ln = lines[i]
            s = ln.strip()
            m = re.match(r"^(={1,6})\s*(.+?)\s*\1\s*$", s)
            if m:
                flush()
                out.append(["h", len(m.group(1)), self.inline(m.group(2))])
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
            m = re.match(r"^([*#:;]+)\s*(.*)$", ln)
            if m:
                flush()
                items = []
                while i < n:
                    mm = re.match(r"^([*#:;]+)\s*(.*)$", lines[i])
                    if not mm:
                        break
                    items.append((mm.group(1), mm.group(2)))
                    i += 1
                out.extend(self.build_lists(items))
                continue
            if ln.startswith(" ") and s:
                flush()
                buf = []
                while i < n and lines[i].startswith(" ") and lines[i].strip():
                    buf.append(lines[i][1:])
                    i += 1
                out.append(["pre", "\n".join(buf)])
                continue
            if not s:
                flush()
                i += 1
                continue
            para.append(ln)
            i += 1
        flush()
        return out

    def emit_para(self, out, inl):
        cur = []
        for node in inl:
            if node[0] == "_block":
                if any(x[0] != "t" or x[1].strip() for x in cur):
                    out.append(["p", cur])
                cur = []
                out.append(node[1])
            else:
                cur.append(node)
        if any(x[0] != "t" or x[1].strip() for x in cur):
            out.append(["p", merge_text(cur)])

    def build_lists(self, items):
        """[(표시, 글)] → 목록·들여쓰기(인용)·정의."""
        out = []
        k = 0
        while k < len(items):
            mark = items[k][0]
            if mark[0] in ":;":
                buf = []
                while k < len(items) and items[k][0][0] in ":;":
                    m2, body = items[k]
                    inl = self.inline(body)
                    buf.append(["p", [["b", inl]] if m2[0] == ";" else inl])
                    k += 1
                out.append(["quote", buf] if any(x[0] == "p" for x in buf) else buf[0])
                continue
            grp = []
            while k < len(items) and items[k][0][0] in "*#":
                grp.append((len(items[k][0]), items[k][0][-1] == "#", items[k][1]))
                k += 1
            out.extend(build_lists(grp, lambda body: ["p", self.inline(body)]))
        return out


def read(text, title=""):
    text = (text or "").replace("\r\n", "\n")
    m = re.match(r"\s*#(?:redirect|넘겨주기)\s*\[\[([^\]|]+)", text, re.I)
    if m:
        page, _, anchor = m.group(1).strip().partition("#")
        return Doc([], [], to_common(page) + ("#" + anchor if anchor else ""))
    r = Reader(title)
    body = r.protect(text)
    return Doc(r.blocks(body), r.cats, None)


# ================================================================ 쓰기
ESC_RE = re.compile(r"''|\[\[|\]\]|\{\{|\}\}|\[(?=https?:)|<(?=[a-zA-Z/!])|~~~|__[A-Z]+__")


def esc(s):
    return ESC_RE.sub(lambda m: "<nowiki>" + m.group(0) + "</nowiki>", s)


class Writer:
    def __init__(self, title):
        self.title = title
        self.refs = 0
        self.refs_placed = False

    def inline(self, inl):
        out = []
        for n in inl or []:
            k = n[0]
            if k == "t":
                out.append(esc(n[1]))
            elif k == "b":
                out.append("'''" + self.inline(n[1]) + "'''")
            elif k == "i":
                out.append("''" + self.inline(n[1]) + "''")
            elif k in ("u", "sup", "sub"):
                out.append(f"<{k}>" + self.inline(n[1]) + f"</{k}>")
            elif k == "s":
                out.append("<s>" + self.inline(n[1]) + "</s>")
            elif k == "code":
                out.append("<code><nowiki>" + n[1].replace("</nowiki>", "&lt;/nowiki>") + "</nowiki></code>")
            elif k == "br":
                out.append("<br />")
            elif k == "math":
                out.append("<math>" + n[1] + "</math>")
            elif k == "link":
                t = mw_title(n[1])
                if t.startswith("Category:"):
                    t = ":" + t
                anchor = n[3] if len(n) > 3 and n[3] else ""
                label = self.inline(n[2]) if n[2] else ""
                target = t + ("#" + anchor if anchor else "")
                out.append(f"[[{target}|{label}]]" if label else f"[[{target}]]")
            elif k == "url":
                out.append(f"[{n[1]} {self.inline(n[2])}]" if n[2] else f"[{n[1]}]" if " " in n[1] else n[1])
            elif k == "img":
                out.append(f"[[File:{n[1]}{'|' + n[2] if n[2] else ''}]]" if not re.match(r"https?://", n[1])
                           else f"[{n[1]} {n[2] or n[1]}]")
            elif k == "fn":
                self.refs += 1
                nm = f' name="{html.escape(n[1])}"' if n[1] and not n[1].isdigit() else ""
                body = self.inline(n[2])
                out.append(f"<ref{nm}>{body}</ref>" if body else f"<ref{nm} />")
            elif k == "tpl":
                out.append(self.tpl(n))
            elif k == "raw":
                fb = None if n[1] == "mediawiki" else raw_fallback(n[2])
                out.append(n[2] if n[1] == "mediawiki" else esc(fb) if fb is not None
                           else "<code><nowiki>" + n[2] + "</nowiki></code>")
        return "".join(out)

    @staticmethod
    def tpl(n):
        name = n[1]
        name = name[len(TPL):] if name.startswith(TPL) else ":" + mw_title(name)
        args = [(f"{k}={v}" if k else v) for k, v in n[2]]
        return "{{" + "|".join([name] + args) + "}}"

    def para(self, inl):
        s = self.inline(inl)
        s = re.sub(r"^([*#:;=\s]|----)", lambda m: "<nowiki>" + m.group(1) + "</nowiki>", s)
        return s

    def block(self, b):
        k = b[0]
        if k == "h":
            eq = "=" * max(1, b[1])
            return f"{eq} {self.inline(b[2])} {eq}"
        if k == "p":
            return self.para(b[1])
        if k == "list":
            return self.list(b, "")
        if k == "code":
            return f'<syntaxhighlight lang="{html.escape(b[1] or "text")}">\n{b[2]}\n</syntaxhighlight>'
        if k == "pre":
            return "<pre>" + html.escape(b[1], quote=False) + "</pre>"
        if k == "math":
            return "<math display=\"block\">" + b[1] + "</math>"
        if k == "quote":
            return "<blockquote>\n" + self.blocks(b[1]) + "\n</blockquote>"
        if k == "hr":
            return "----"
        if k == "toc":
            return "__TOC__"
        if k == "refs":
            self.refs_placed = True
            return "<references />"
        if k == "table":
            return self.table(b)
        if k == "fold":
            return (f'<div class="mw-collapsible mw-collapsed">\n{self.inline(b[1])}\n'
                    f'<div class="mw-collapsible-content">\n{self.blocks(b[2])}\n</div></div>')
        if k == "tpl":
            return self.tpl(b)
        if k == "raw":
            return b[2] if b[1] == "mediawiki" else "<pre>" + html.escape(b[2], quote=False) + "</pre>"
        return ""

    def list(self, b, prefix):
        out = []
        mark = prefix + ("#" if b[1] else "*")
        for item in b[2]:
            first = True
            for sub in item:
                if sub[0] == "list":
                    out.append(self.list(sub, mark))
                elif first:
                    out.append(mark + " " + (self.inline(sub[1]) if sub[0] == "p" else self.block(sub).replace("\n", " ")))
                    first = False
                else:
                    out.append(mark.replace("*", ":").replace("#", ":") + ": " +
                               (self.inline(sub[1]) if sub[0] == "p" else self.block(sub).replace("\n", " ")))
        return "\n".join(out)

    def table(self, b):
        out = ['{| class="wikitable"']
        if b[2]:
            out.append("|+ " + self.inline(b[2]))
        for r in b[1]:
            out.append("|-")
            for c in r:
                attrs = []
                if c.get("cs", 1) > 1:
                    attrs.append(f'colspan="{c["cs"]}"')
                if c.get("rs", 1) > 1:
                    attrs.append(f'rowspan="{c["rs"]}"')
                if c.get("al"):
                    attrs.append(f'style="text-align:{c["al"]}"')
                mark = "!" if c.get("h") else "|"
                txt = ("\n" + self.blocks(c["b"])) if c.get("b") else self.inline(c["c"]).replace("||", "<nowiki>||</nowiki>")
                out.append(f"{mark} {' '.join(attrs)} | {txt}" if attrs else f"{mark} {txt}")
        out.append("|}")
        return "\n".join(out)

    def blocks(self, blocks):
        return "\n\n".join(x for x in (self.block(b) for b in blocks) if x != "")


def write(doc, title=""):
    if doc.redirect:
        page, _, anchor = doc.redirect.partition("#")
        return f"#REDIRECT [[{mw_title(page)}{'#' + anchor if anchor else ''}]]"
    w = Writer(title)
    body = w.blocks(doc.blocks)
    if w.refs and not w.refs_placed:
        body += "\n\n<references />"
    if doc.cats:
        body += "\n\n" + "\n".join(f"[[Category:{mw_title(CAT + c)[9:]}]]" for c in doc.cats)
    return body.strip("\n")
