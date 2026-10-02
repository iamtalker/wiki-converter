"""공통 구조 → HTML (내장 마크다운 엔진의 화면, 마크다운 표 안의 HTML 칸에 쓴다)."""
import html
import re
import urllib.parse

from .tree import CAT, raw_fallback, text_of


def default_link(page):
    return "/w/" + urllib.parse.quote(page, safe="")


def slug(s):
    return re.sub(r"[^\w가-힣-]+", "-", s).strip("-").lower() or "s"


class Writer:
    def __init__(self, title, link=default_link, exists=None):
        self.title = title
        self.link = link
        self.exists = exists  # 문서가 있는지 알려 주는 함수(없으면 빨간 링크)
        self.notes = []
        self.heads = []

    def inline(self, inl):
        out = []
        e = html.escape
        for n in inl or []:
            k = n[0]
            if k == "t":
                out.append(e(n[1]))
            elif k in ("b", "i", "u", "s", "sup", "sub"):
                tag = {"b": "strong", "i": "em", "s": "del"}.get(k, k)
                out.append(f"<{tag}>{self.inline(n[1])}</{tag}>")
            elif k == "code":
                out.append(f"<code>{e(n[1])}</code>")
            elif k == "br":
                out.append("<br>")
            elif k == "math":
                out.append(f'<span class="math">\\({e(n[1])}\\)</span>')
            elif k == "link":
                anchor = n[3] if len(n) > 3 and n[3] else ""
                href = self.link(n[1]) + ("#" + urllib.parse.quote(slug(anchor)) if anchor else "")
                cls = "" if self.exists is None or self.exists(n[1]) else ' class="new"'
                out.append(f'<a href="{e(href)}"{cls}>{self.inline(n[2]) if n[2] else e(n[1])}</a>')
            elif k == "url":
                safe = n[1] if re.match(r"(?i)^(https?|mailto|ftp):", n[1]) else "#"
                out.append(f'<a href="{e(safe)}" rel="nofollow noopener" target="_blank">'
                           f'{self.inline(n[2]) if n[2] else e(n[1])}</a>')
            elif k == "img":
                src = n[1] if re.match(r"(?i)^https?://", n[1]) else "/_file/" + urllib.parse.quote(n[1])
                out.append(f'<img src="{e(src)}" alt="{e(n[2] or "")}" loading="lazy">')
            elif k == "fn":
                self.notes.append(self.inline(n[2]))
                i = len(self.notes)
                out.append(f'<sup class="fn"><a id="fnref{i}" href="#fn{i}">[{e(n[1]) if n[1] and not n[1].isdigit() else i}]</a></sup>')
            elif k == "tpl":
                out.append(self.tpl(n))
            elif k == "raw":
                fb = raw_fallback(n[2])
                out.append(e(fb) if fb is not None else f'<code class="raw" title="{e(n[1])}">{e(n[2])}</code>')
        return "".join(out)

    def tpl(self, n):
        name = n[1]
        args = ", ".join(f"{k}={v}" if k else v for k, v in n[2])
        return (f'<span class="tpl"><a href="{html.escape(self.link(name))}">{html.escape(name)}</a>'
                f'{"(" + html.escape(args) + ")" if args else ""}</span>')

    def block(self, b):
        k = b[0]
        e = html.escape
        if k == "h":
            t = text_of(b[2])
            sid = slug(t)
            self.heads.append((b[1], t, sid))
            return f'<h{b[1]} id="{e(sid)}">{self.inline(b[2])}</h{b[1]}>'
        if k == "p":
            return f"<p>{self.inline(b[1])}</p>"
        if k == "list":
            tag = "ol" if b[1] else "ul"
            items = "".join("<li>" + "".join(self.block(x) if x[0] != "p" else self.inline(x[1]) for x in item) + "</li>"
                            for item in b[2])
            return f"<{tag}>{items}</{tag}>"
        if k == "code":
            return f'<pre><code class="language-{e(b[1] or "text")}">{e(b[2])}</code></pre>'
        if k == "pre":
            return f"<pre>{e(b[1])}</pre>"
        if k == "math":
            return f'<div class="math">\\[{e(b[1])}\\]</div>'
        if k == "quote":
            return f"<blockquote>{self.blocks(b[1])}</blockquote>"
        if k == "hr":
            return "<hr>"
        if k == "toc":
            return "\x00TOC\x00"
        if k == "refs":
            return "\x00REFS\x00"
        if k == "table":
            rows = []
            for r in b[1]:
                cells = []
                for c in r:
                    tag = "th" if c.get("h") else "td"
                    a = (f' colspan="{c["cs"]}"' if c.get("cs", 1) > 1 else "") + \
                        (f' rowspan="{c["rs"]}"' if c.get("rs", 1) > 1 else "") + \
                        (f' style="text-align:{c["al"]}"' if c.get("al") else "")
                    inner = self.blocks(c["b"]) if c.get("b") else self.inline(c["c"])
                    cells.append(f"<{tag}{a}>{inner}</{tag}>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            cap = f"<caption>{self.inline(b[2])}</caption>" if b[2] else ""
            return f'<div class="table"><table>{cap}{"".join(rows)}</table></div>'
        if k == "fold":
            return f"<details><summary>{self.inline(b[1])}</summary>{self.blocks(b[2])}</details>"
        if k == "tpl":
            return f"<p>{self.tpl(b)}</p>"
        if k == "raw":
            return f'<pre class="raw" title="{e(b[1])}">{e(b[2])}</pre>'
        return ""

    def blocks(self, blocks):
        return "\n".join(self.block(b) for b in blocks)

    def notes_html(self):
        if not self.notes:
            return ""
        items = "".join(f'<li id="fn{i}">{t} <a href="#fnref{i}">↩</a></li>' for i, t in enumerate(self.notes, 1))
        return f'<ol class="footnotes">{items}</ol>'

    def toc_html(self):
        if not self.heads:
            return ""
        items = "".join(f'<li class="l{lv}"><a href="#{html.escape(sid)}">{html.escape(t)}</a></li>'
                        for lv, t, sid in self.heads)
        return f'<nav class="toc"><b>목차</b><ul>{items}</ul></nav>'


def write(doc, title="", link=default_link, exists=None):
    w = Writer(title, link, exists)
    if doc.redirect:
        page = doc.redirect.split("#")[0]
        return f'<p class="redirect">➜ <a href="{html.escape(link(page))}">{html.escape(doc.redirect)}</a></p>'
    body = w.blocks(doc.blocks)
    notes = w.notes_html()
    body = body.replace("\x00TOC\x00", w.toc_html())
    body = body.replace("\x00REFS\x00", notes, 1) if "\x00REFS\x00" in body else body + notes
    body = body.replace("\x00REFS\x00", "")
    if doc.cats:
        body += '<div class="cats">분류: ' + " · ".join(
            f'<a href="{html.escape(link(CAT + c))}">{html.escape(c)}</a>' for c in doc.cats) + "</div>"
    return body
