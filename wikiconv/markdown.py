"""마크다운(GitHub 방식) 과 AWM(애니위키 마크다운) 읽기·쓰기.

AWM = 마크다운 + 확장. 사용자에게 보이지 않는 통역용 공용 언어라, 공통 구조를 잃지 않고 적는 것이 목적이다.
  머리말      --- / categories: ["가", "나"] / redirect: "문서" / ---
  위키 링크   [[문서#앵커|글]]            틀    {{이름|키=값|값}}      원문 보존(줄 안) {{#raw:형식:base64}}
  각주        [^이름] … [^이름]: 내용      수식  $…$ , $$ … $$        원문 보존(블록)  ```raw:형식
  목차·각주 자리  [toc] [refs]           접기  <details><summary>…</summary> … </details>
  표 칸 속성  칸 글 앞에 {.cs2 .rs3 .center .h .nh}  (합친 칸·정렬·머리칸)
일반 마크다운으로 쓸 때는 확장을 쓰지 않는다(위키 링크는 같은 폴더의 .md 파일 링크, 합친 칸이 있는 표는 HTML 표).
"""
import base64
import hashlib
import html
import json
import re
import urllib.parse

from .tree import CAT, Doc, InlineScanner, build_lists, cell_inline, flatten_blocks, merge_text, raw_fallback

MD_BAD = str.maketrans('\\/:*?"<>|#', "＼／：＊？＂＜＞｜＃")
MD_BACK = str.maketrans("＼／：＊？＂＜＞｜＃", '\\/:*?"<>|#')
WIN_RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}


def md_name(t):
    """문서 제목 → 마크다운 파일 이름(윈도에서 못 쓰는 글자는 전각 글자로)."""
    name = t.translate(MD_BAD).strip()
    bare = name.rstrip(".")
    name = (bare + "．" * (len(name) - len(bare))) if bare else "_"  # 윈도는 끝의 마침표를 못 쓰므로 전각 마침표로(읽을 때 되돌림)
    if name.lower().split(".")[0] in WIN_RESERVED:
        name += "_"
    if len(name.encode("utf-8")) > 200:
        name = name.encode("utf-8")[:180].decode("utf-8", "ignore") + "~" + hashlib.sha1(t.encode()).hexdigest()[:8]
    return name + ".md"


def md_title(name):
    """파일 이름 → 문서 제목(md_name 의 거꾸로, 줄인 이름은 그대로)."""
    name = name[:-3] if name.lower().endswith(".md") else name
    if name.endswith("_") and name[:-1].lower().split(".")[0] in WIN_RESERVED:
        name = name[:-1]
    name = name.translate(MD_BACK)
    bare = name.rstrip("．")
    return bare + "." * (len(name) - len(bare))


# ================================================================ 쓰기
ESC_RE = re.compile(r"([\\`*_\[\]<>~$|{}])")


class Writer:
    def __init__(self, awm):
        self.awm = awm
        self.notes = []

    def esc(self, s):
        return ESC_RE.sub(r"\\\1", s)

    def inline(self, inl, cell=False):
        out = []
        for n in inl or []:
            k = n[0]
            if k == "t":
                out.append(self.esc(n[1]))
            elif k == "b":
                out.append("**" + self.inline(n[1], cell) + "**")
            elif k == "i":
                out.append("*" + self.inline(n[1], cell) + "*")
            elif k == "s":
                out.append("~~" + self.inline(n[1], cell) + "~~")
            elif k in ("u", "sup", "sub"):
                out.append(f"<{k}>" + self.inline(n[1], cell) + f"</{k}>")
            elif k == "code":
                t = n[1]
                fence = "``" if "`" in t else "`"
                out.append(f"{fence} {t} {fence}" if fence == "``" else f"`{t}`")
            elif k == "br":
                out.append("<br>" if cell else "\\\n")
            elif k == "math":
                out.append("$" + n[1] + "$")
            elif k == "link":
                anchor = n[3] if len(n) > 3 and n[3] else ""
                label = self.inline(n[2], cell) if n[2] else ""
                if self.awm:
                    target = (n[1] + ("#" + anchor if anchor else "")).replace("|", "\\|").replace("]]", "\\]\\]")
                    out.append(f"[[{target}|{label}]]" if label else f"[[{target}]]")
                else:
                    href = md_name(n[1]) + ("#" + anchor if anchor else "")
                    out.append(f"[{label or self.esc(n[1])}](<{href}>)")
            elif k == "url":
                label = self.inline(n[2], cell) if n[2] else self.esc(n[1])
                out.append(f"[{label}](<{n[1]}>)")
            elif k == "img":
                out.append(f"![{self.esc(n[2] or '')}](<{n[1]}>)")
            elif k == "fn":
                fid = re.sub(r"[^\w가-힣-]", "_", n[1]) if n[1] else str(len(self.notes) + 1)
                if any(f == fid for f, _ in self.notes):
                    fid = f"{fid}_{len(self.notes) + 1}"
                self.notes.append((fid, self.inline(n[2], True)))
                out.append(f"[^{fid}]")
            elif k == "tpl":
                out.append(self.tpl(n))
            elif k == "raw":
                if self.awm:
                    out.append("{{#raw:" + n[1] + ":" + base64.b64encode(n[2].encode()).decode() + "}}")
                else:
                    fb = raw_fallback(n[2])
                    out.append(self.esc(fb) if fb is not None else f"`` {n[2]} ``" if "`" in n[2] else f"`{n[2]}`")
        return "".join(out)

    def tpl(self, n):
        if self.awm:
            args = []
            for k, v in n[2]:
                v = v.replace("|", "\\|").replace("}}", "\\}\\}")
                args.append(f"{k}={v}" if k else v)
            return "{{" + "|".join([n[1]] + args) + "}}"
        name = n[1]  # 마크다운에는 끼워 넣기가 없어 그 문서로 가는 링크로 바꾼다
        return f"[{self.esc(name)}](<{md_name(name)}>)"

    def para(self, inl):
        s = self.inline(inl)
        # 줄 첫머리에서 목록·제목·인용으로 읽히지 않게
        return re.sub(r"(?m)^(\s*)([#>+-]|\d+\.)(?=\s)", r"\1\\\2", s)

    def block(self, b, indent=""):
        k = b[0]
        if k == "h":
            return "#" * b[1] + " " + self.inline(b[2], True)
        if k == "p":
            return self.para(b[1])
        if k == "list":
            return self.list(b, "")
        if k in ("code", "pre"):
            lang = b[1] if k == "code" else ""
            text = b[2] if k == "code" else b[1]
            fence = "````" if "```" in text else "```"
            return f"{fence}{lang}\n{text}\n{fence}"
        if k == "math":
            return "$$\n" + b[1] + "\n$$"
        if k == "quote":
            return "\n".join(("> " + ln) if ln else ">" for ln in self.blocks(b[1]).split("\n"))
        if k == "hr":
            return "---"
        if k == "toc":
            return "[toc]" if self.awm else ""
        if k == "refs":
            return "[refs]" if self.awm else ""
        if k == "table":
            return self.table(b)
        if k == "fold":
            return f"<details><summary>{self.inline(b[1], True)}</summary>\n\n{self.blocks(b[2])}\n\n</details>"
        if k == "tpl":
            return self.tpl(b)
        if k == "raw":
            if self.awm:
                text = b[2]
                fence = "````" if "```" in text else "```"
                return f"{fence}raw:{b[1]}\n{text}\n{fence}"
            return "```\n" + b[2] + "\n```"
        return ""

    def list(self, b, indent):
        out = []
        for n, item in enumerate(b[2], 1):
            mark = f"{n}. " if b[1] else "- "
            pad = indent + " " * len(mark)
            first = True
            for sub in item:
                if sub[0] == "list":
                    out.append(self.list(sub, pad))
                    continue
                text = self.block(sub)
                lines = text.split("\n")
                if first:
                    out.append(indent + mark + lines[0])
                    out.extend(pad + ln for ln in lines[1:])
                    first = False
                else:
                    out.extend(pad + ln for ln in lines)
            if first:
                out.append(indent + mark.rstrip())
        return "\n".join(out)

    def table(self, b):
        rows, caption = b[1], b[2]
        if not rows:
            return ""
        spans = any(c.get("cs", 1) > 1 or c.get("rs", 1) > 1 or c.get("b") for r in rows for c in r)
        cap = ("**" + self.inline(caption, True) + "**\n\n") if caption else ""
        if spans and not self.awm:
            out = ["<table>"]
            for r in rows:
                tds = []
                for c in r:
                    tag = "th" if c.get("h") else "td"
                    a = (f' colspan="{c["cs"]}"' if c.get("cs", 1) > 1 else "") + \
                        (f' rowspan="{c["rs"]}"' if c.get("rs", 1) > 1 else "") + \
                        (f' align="{c["al"]}"' if c.get("al") else "")
                    tds.append(f"<{tag}{a}>{blocks_html(c['b']) if c.get('b') else inline_html(c['c'])}</{tag}>")
                out.append("<tr>" + "".join(tds) + "</tr>")
            return cap + "\n".join(out + ["</table>"])
        width = max(sum(c.get("cs", 1) for c in r) for r in rows)
        lines = []
        for ri, r in enumerate(rows):
            cells = []
            for c in r:
                if c.get("b") and self.awm:  # 칸 안의 블록: 공용 언어 안에서만 쓰는 묶음(잃지 않게)
                    inner = Writer(True).blocks(c["b"])
                    txt = "{{#cell:" + base64.b64encode(inner.encode()).decode() + "}}"
                else:
                    txt = self.inline(cell_inline(c), True).replace("|", "\\|").replace("\n", " ")
                if self.awm:
                    attrs = []
                    if c.get("cs", 1) > 1:
                        attrs.append(f".cs{c['cs']}")
                    if c.get("rs", 1) > 1:
                        attrs.append(f".rs{c['rs']}")
                    if c.get("al"):
                        attrs.append("." + c["al"])
                    if ri == 0 and not c.get("h"):
                        attrs.append(".nh")
                    elif ri > 0 and c.get("h"):
                        attrs.append(".h")
                    if attrs:
                        txt = "{" + " ".join(attrs) + "} " + txt
                cells.append(txt)
            cells += [""] * (width - len(r)) if not self.awm else []
            lines.append("| " + " | ".join(cells) + " |")
            if ri == 0:
                lines.append("|" + "|".join([" --- "] * max(width, len(cells))) + "|")
        return cap + "\n".join(lines)

    def blocks(self, blocks):
        return "\n\n".join(x for x in (self.block(b) for b in blocks) if x != "")


def blocks_html(blocks):
    from .html_writer import Writer as H
    return H("", link=lambda p: urllib.parse.quote(md_name(p))).blocks(blocks)


def inline_html(inl):
    """HTML 표 칸 안: 링크는 마크다운과 같이 같은 폴더의 .md 파일로."""
    from .html_writer import Writer as H
    return H("", link=lambda p: urllib.parse.quote(md_name(p))).inline(inl)


def write(doc, title="", awm=True):
    w = Writer(awm)
    head = []
    if awm and (doc.cats or doc.redirect):
        head = ["---"]
        if doc.cats:
            head.append("categories: " + json.dumps(doc.cats, ensure_ascii=False))
        if doc.redirect:
            head.append("redirect: " + json.dumps(doc.redirect, ensure_ascii=False))
        head += ["---", ""]
    if doc.redirect and not awm:
        page, _, anchor = doc.redirect.partition("#")
        return f"이 문서는 [{w.esc(page)}](<{md_name(page)}{'#' + anchor if anchor else ''}>) 문서로 넘겨줍니다."
    body = w.blocks(doc.blocks)
    if w.notes:
        body += "\n\n" + "\n".join(f"[^{fid}]: {t}" for fid, t in w.notes)
    if doc.cats and not awm:
        body += "\n\n---\n\n분류: " + ", ".join(f"[{w.esc(c)}](<{md_name(CAT + c)}>)" for c in doc.cats)
    return ("\n".join(head) + body).strip("\n") + "\n"


# ================================================================ 읽기
class Reader:
    def __init__(self, title):
        self.title = title
        self.notes = {}

    # ---------------- 줄 안
    def inline(self, s):
        if not hasattr(self, "_sc"):
            self._sc = InlineScanner([
                (re.compile(r"\\\n"), lambda m: ["br"]),
                (re.compile(r"\\([\\`*_\[\]<>~$|{}#+\-.!()])"), lambda m: ["t", m.group(1)]),
                (re.compile(r"``\s?(.+?)\s?``"), lambda m: ["code", m.group(1)]),
                (re.compile(r"`([^`\n]+)`"), lambda m: ["code", m.group(1)]),
                (re.compile(r"\{\{#raw:(\w+):([A-Za-z0-9+/=]*)\}\}"),
                 lambda m: ["raw", m.group(1), base64.b64decode(m.group(2)).decode("utf-8", "replace")]),
                (re.compile(r"\{\{((?:[^{}\\]|\\.|\{[^{])+?)\}\}"), lambda m: self.tpl(m.group(1))),
                (re.compile(r"\[\[((?:\\.|[^\]\\]|\](?!\]))+?)\]\]"), self.wikilink),
                (re.compile(r"\[\^([^\]\s]+)\](?!:)"),
                 lambda m: ["fn", "" if m.group(1).isdigit() else m.group(1), self.notes.get(m.group(1)) or []]),
                (re.compile(r"!\[([^\]]*)\]\((?:<([^>]*)>|([^)\s]*))\)"),
                 lambda m: ["img", m.group(2) if m.group(2) is not None else m.group(3), m.group(1)]),
                (re.compile(r"\[((?:\\.|!\[[^\]]*\]\((?:<[^>]*>|[^)\s]*)\)|[^\[\]\\])*)\]\((?:<([^>]*)>|([^)\s]*))\)"),
                 self.link),
                (re.compile(r"<(https?://[^>\s]+)>"), lambda m: ["url", m.group(1), None]),
                (re.compile(r"\$([^$\n]+?)\$"), lambda m: ["math", m.group(1)]),
                (re.compile(r"<br\s*/?>", re.I), lambda m: ["br"]),
                (re.compile(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S), self.html_link),
                (re.compile(r'<img\s[^>]*?src="([^"]*)"(?:[^>]*?alt="([^"]*)")?[^>]*>', re.I),
                 lambda m: ["img", html.unescape(m.group(1)), html.unescape(m.group(2) or "")]),
                (re.compile(r"(?<![\\\w])\*\*(.+?)\*\*"), lambda m: ["b", self.inline(m.group(1))]),
                (re.compile(r"(?<![\\\w])__(.+?)__(?!\w)"), lambda m: ["b", self.inline(m.group(1))]),
                (re.compile(r"(?<![\\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)"), lambda m: ["i", self.inline(m.group(1))]),
                (re.compile(r"(?<![\\\w])_(?!\s)(.+?)(?<!\s)_(?!\w)"), lambda m: ["i", self.inline(m.group(1))]),
                (re.compile(r"~~(.+?)~~"), lambda m: ["s", self.inline(m.group(1))]),
                (re.compile(r"<(?:u|ins)>(.*?)</(?:u|ins)>", re.I | re.S), lambda m: ["u", self.inline(m.group(1))]),
                (re.compile(r"<(?:s|del|strike)>(.*?)</(?:s|del|strike)>", re.I | re.S),
                 lambda m: ["s", self.inline(m.group(1))]),
                (re.compile(r"<sup>(.*?)</sup>", re.I | re.S), lambda m: ["sup", self.inline(m.group(1))]),
                (re.compile(r"<sub>(.*?)</sub>", re.I | re.S), lambda m: ["sub", self.inline(m.group(1))]),
                (re.compile(r"<(?:b|strong)>(.*?)</(?:b|strong)>", re.I | re.S), lambda m: ["b", self.inline(m.group(1))]),
                (re.compile(r"<(?:i|em)>(.*?)</(?:i|em)>", re.I | re.S), lambda m: ["i", self.inline(m.group(1))]),
                (re.compile(r"<code>(.*?)</code>", re.I | re.S), lambda m: ["code", html.unescape(m.group(1))]),
                (re.compile(r"&(#\d+|#x[0-9a-fA-F]+|\w+);"), lambda m: ["t", html.unescape(m.group(0))]),
            ])
        out = []
        for n in self._sc.scan(s):
            if n[0] == "t":
                n = ["t", n[1].replace("\n", " ")]
            out.append(n)
        return merge_text(out)

    def tpl(self, inner):
        parts = re.split(r"(?<!\\)\|", inner)
        name = parts[0].strip()
        args = []
        for p in parts[1:]:
            p = re.sub(r"\\(.)", r"\1", p)
            k, eq, v = p.partition("=")
            args.append([k.strip(), v] if eq and k.strip() and not re.search(r"[=\[\]{}<>\n]", k) else [None, p])
        return ["tpl", name, args]

    def wikilink(self, m):
        parts = re.split(r"(?<!\\)\|", m.group(1), maxsplit=1)
        target, label = parts[0], (parts[1] if len(parts) > 1 else "")
        target = re.sub(r"\\(.)", r"\1", target).strip()
        page, _, anchor = target.partition("#")
        return ["link", page, self.inline(label) if label else None, anchor]

    def html_link(self, m):
        href, label = html.unescape(m.group(1)), m.group(2)
        lab = self.inline(label) if label else None
        if href.startswith("/w/"):
            return ["link", urllib.parse.unquote(href[3:]), lab, ""]
        path, _, anchor = href.partition("#")
        if path.lower().endswith(".md"):
            return ["link", md_title(urllib.parse.unquote(path).rsplit("/", 1)[-1]), lab, anchor]
        return ["url", href, lab]

    def link(self, m):
        label, href = m.group(1), (m.group(2) if m.group(2) is not None else m.group(3)) or ""
        lab = self.inline(label) if label else None
        if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", href) and not re.match(r"(?i)^(분류|틀|category|template):", href):
            return ["url", href, lab]
        path, _, anchor = href.partition("#")
        path = urllib.parse.unquote(path)
        if path.lower().endswith(".md") or not path:
            page = md_title(path.rsplit("/", 1)[-1]) if path else self.title
            return ["link", page, lab, anchor]
        return ["url", href, lab]

    # ---------------- 블록
    def blocks(self, lines):
        out, i, n = [], 0, len(lines)
        para = []

        def flush():
            if para:
                text = "\n".join(para)
                text = re.sub(r" {2,}\n", "\\\n", text)
                out.append(["p", self.inline(text)])
                para.clear()

        while i < n:
            ln = lines[i]
            s = ln.strip()
            m = re.match(r"^(\s*)(`{3,}|~{3,})\s*([^`\s]*)\s*$", ln)
            if m:
                flush()
                fence, info = m.group(2), m.group(3)
                j, buf = i + 1, []
                while j < n and not lines[j].strip().startswith(fence):
                    buf.append(lines[j])
                    j += 1
                text = "\n".join(buf)
                if info.startswith("raw:"):
                    out.append(["raw", info[4:], text])
                elif info:
                    out.append(["code", info, text])
                else:
                    out.append(["pre", text])
                i = j + 1
                continue
            if s == "$$":
                flush()
                j, buf = i + 1, []
                while j < n and lines[j].strip() != "$$":
                    buf.append(lines[j])
                    j += 1
                out.append(["math", "\n".join(buf)])
                i = j + 1
                continue
            m = re.match(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$", ln)  # 마크다운 규칙: # 앞에 공백 3칸까지 허용
            if m:
                flush()
                out.append(["h", len(m.group(1)), self.inline(m.group(2))])
                i += 1
                continue
            if re.fullmatch(r"(\*\s*){3,}|(-\s*){3,}|(_\s*){3,}", s):
                flush()
                out.append(["hr"])
                i += 1
                continue
            if s.lower() in ("[toc]", "[refs]"):
                flush()
                out.append(["toc"] if s.lower() == "[toc]" else ["refs"])
                i += 1
                continue
            if s.startswith("{{") and s.endswith("}}") and not s.startswith("{{#raw:") and s.count("{{") == 1:
                flush()
                out.append(self.tpl(s[2:-2]))
                i += 1
                continue
            if ln.startswith(">"):
                flush()
                buf = []
                while i < n and lines[i].startswith(">"):
                    buf.append(re.sub(r"^>\s?", "", lines[i]))
                    i += 1
                out.append(["quote", self.blocks(buf)])
                continue
            if re.match(r"^\s*<details", ln, re.I):
                flush()
                depth, j, buf = 0, i, []
                while j < n:
                    depth += len(re.findall(r"<details", lines[j], re.I)) - len(re.findall(r"</details>", lines[j], re.I))
                    buf.append(lines[j])
                    j += 1
                    if depth <= 0:
                        break
                inner = "\n".join(buf)
                sm = re.search(r"<summary>(.*?)</summary>", inner, re.I | re.S)
                body = re.sub(r"^\s*<details[^>]*>", "", inner, flags=re.I)
                body = re.sub(r"</details>\s*$", "", body, flags=re.I)
                if sm:
                    body = body.replace(sm.group(0), "", 1)
                out.append(["fold", self.inline(sm.group(1)) if sm else [], self.blocks(body.split("\n"))])
                i = j
                continue
            if re.match(r"^\s*<table", ln, re.I):
                flush()
                j, buf = i, []
                while j < n:
                    buf.append(lines[j])
                    j += 1
                    if re.search(r"</table>", lines[j - 1], re.I):
                        break
                out.append(self.html_table("\n".join(buf)))
                i = j
                continue
            if "|" in ln and i + 1 < n and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*", lines[i + 1]):
                flush()
                rows = [ln]
                aligns = [("center" if a.strip().startswith(":") and a.strip().endswith(":") else
                           "right" if a.strip().endswith(":") else "left" if a.strip().startswith(":") else "")
                          for a in self.cells(lines[i + 1])]
                j = i + 2
                while j < n and "|" in lines[j] and lines[j].strip():
                    rows.append(lines[j])
                    j += 1
                out.append(self.gfm_table(rows, aligns))
                i = j
                continue
            m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", ln)
            if m:
                flush()
                j = i
                items = []
                while j < n:
                    mm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[j])
                    if mm:
                        items.append([len(mm.group(1).expandtabs(4)), not mm.group(2)[0] in "-*+",
                                      mm.group(3), len(mm.group(1)) + len(mm.group(2)) + 1])
                        j += 1
                        continue
                    if lines[j].strip() and items and len(lines[j]) - len(lines[j].lstrip()) >= items[-1][3]:
                        items[-1][2] += "\n" + lines[j].strip()
                        j += 1
                        continue
                    break
                out.extend(self.build_list(items))
                i = j
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
        return build_lists(items, lambda body: ["p", self.inline(re.sub(r" {2,}\n|\\\n", "\\\n", body))])

    @staticmethod
    def cells(line):
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|") and not s.endswith("\\|"):
            s = s[:-1]
        return [c.replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]

    def gfm_table(self, rows, aligns):
        out = []
        for ri, r in enumerate(rows):
            cells = []
            for ci, raw in enumerate(self.cells(r)):
                c = {"h": ri == 0, "cs": 1, "rs": 1, "al": aligns[ci] if ci < len(aligns) else "", "c": []}
                raw = raw.strip()
                m = re.match(r"\{((?:\.[\w-]+\s*)+)\}\s?", raw)
                if m:
                    for a in m.group(1).split():
                        a = a[1:]
                        if a.startswith("cs") and a[2:].isdigit():
                            c["cs"] = int(a[2:])
                        elif a.startswith("rs") and a[2:].isdigit():
                            c["rs"] = int(a[2:])
                        elif a in ("left", "center", "right"):
                            c["al"] = a
                        elif a == "h":
                            c["h"] = True
                        elif a == "nh":
                            c["h"] = False
                    raw = raw[m.end():]
                cm = re.fullmatch(r"\{\{#cell:([A-Za-z0-9+/=]*)\}\}", raw)
                if cm:
                    c["b"] = self.blocks(base64.b64decode(cm.group(1)).decode("utf-8", "replace").split("\n"))
                    c["c"] = flatten_blocks(c["b"])
                else:
                    c["c"] = self.inline(raw)
                cells.append(c)
            out.append(cells)
        # 합친 칸 뒤의 빈 칸(일반 마크다운 표의 자리 채움)은 버린다
        return ["table", out, []]

    def html_table(self, src):
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", src, re.I | re.S):
            cells = []
            for tag, attrs, body in re.findall(r"<(t[hd])([^>]*)>(.*?)</t[hd]>", tr, re.I | re.S):
                cs = re.search(r'colspan="?(\d+)', attrs)
                rs = re.search(r'rowspan="?(\d+)', attrs)
                al = re.search(r'align="?(\w+)', attrs)
                cells.append({"h": tag.lower() == "th", "cs": int(cs.group(1)) if cs else 1,
                              "rs": int(rs.group(1)) if rs else 1, "al": al.group(1) if al else "",
                              "c": self.inline(body.strip())})
            rows.append(cells)
        cap = re.search(r"<caption>(.*?)</caption>", src, re.I | re.S)
        return ["table", rows, self.inline(cap.group(1)) if cap else []]


def read(text, title=""):
    text = (text or "").replace("\r\n", "\n")
    cats, redirect = [], None
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if m:
        for ln in m.group(1).split("\n"):
            k, _, v = ln.partition(":")
            try:
                val = json.loads(v.strip())
            except ValueError:
                val = v.strip()
            if k.strip() == "categories" and isinstance(val, list):
                cats = [str(x) for x in val]
            elif k.strip() == "redirect" and val:
                redirect = str(val)
        text = text[m.end():]
    m = re.match(r"\s*이 문서는 \[[^\]]*\]\(<?([^)>]+)>?\) 문서로 넘겨줍니다\.\s*$", text)
    if m:
        path, _, anchor = m.group(1).partition("#")
        return Doc([], [], md_title(urllib.parse.unquote(path)) + ("#" + anchor if anchor else ""))
    # 일반 마크다운으로 내보낼 때 붙인 분류 줄
    m = re.search(r"\n---\n\n분류: (.+?)\s*$", text)
    if m and not cats:
        cats = [x for x in re.findall(r"\[((?:[^\]\\]|\\.)*)\]\(<?[^)]*\)", m.group(1))]
        cats = [re.sub(r"\\(.)", r"\1", c) for c in cats]
        text = text[:m.start()]
    r = Reader(title)
    lines = text.split("\n")
    # 각주 정의를 먼저 모은다
    body = []
    for ln in lines:
        fm = re.match(r"^\[\^([^\]\s]+)\]:\s?(.*)$", ln)
        if fm:
            r.notes[fm.group(1)] = None
            r._pending = getattr(r, "_pending", []) + [(fm.group(1), fm.group(2))]
        else:
            body.append(ln)
    for fid, t in getattr(r, "_pending", []):
        r.notes[fid] = r.inline(t)
    return Doc(r.blocks(body), cats, redirect)
