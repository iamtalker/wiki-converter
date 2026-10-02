"""애니위키 공용 언어 변환기 (표준 라이브러리만 사용).

모든 위키 문법을 한 번 '공통 구조(AST)' 로 읽고, 그 구조를 원하는 문법으로 쓴다.
공통 구조를 글로 적은 것이 AWM(애니위키 마크다운: 마크다운 + 확장)이다. 사용자는 볼 필요가 없다.

    from wikiconv import convert, parse, render
    convert(text, "namumark", "mediawiki", title="문서 이름")

형식: namumark(openNAMU) · mediawiki · dokuwiki · markdown · awm · html(쓰기만)
옮길 수 없는 문법은 ("raw", 원래 형식, 원문) 으로 보존해서, 같은 형식으로 돌아갈 때는 그대로 되살린다.
"""
from .tree import Doc, tidy
from . import namumark, mediawiki, dokuwiki, markdown, html_writer

READERS = {"namumark": namumark.read, "mediawiki": mediawiki.read, "dokuwiki": dokuwiki.read,
           "markdown": markdown.read, "awm": markdown.read}
WRITERS = {"namumark": namumark.write, "mediawiki": mediawiki.write, "dokuwiki": dokuwiki.write,
           "markdown": lambda d, title="": markdown.write(d, title, awm=False),
           "awm": lambda d, title="": markdown.write(d, title, awm=True), "html": html_writer.write}
FORMATS = ("namumark", "mediawiki", "dokuwiki", "markdown")
__all__ = ["Doc", "parse", "render", "convert", "FORMATS"]


def parse(text, fmt, title=""):
    # \x00 은 읽기 도구들이 자리표시로 쓰므로 원문에서 뺀다
    return tidy(READERS[fmt]((text or "").replace("\x00", ""), title))


def render(doc, fmt, title=""):
    # 안전망: 읽기 도구의 자리표시(\x00)가 어디선가 남았더라도 결과에는 나가지 않게
    return WRITERS[fmt](doc, title).replace("\x00", "")


def convert(text, src, dst, title=""):
    if src == dst:
        return text
    return render(parse(text, src, title), dst, title)
