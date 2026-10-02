"""공용 언어 변환기 시험: 네 형식 읽기·쓰기, AWM 왕복이 정확한지, 다른 형식을 거쳐도 핵심 내용이 남는지."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wikiconv  # noqa: E402
from wikiconv.tree import text_of  # noqa: E402

NAMU = """= 개요 =
'''굵게''' 와 ''기울임'' 그리고 __밑줄__ ~~취소~~ ^^위^^ ,,아래,, {{{코드}}}
[[다른 문서|링크]] [[https://example.com|외부]][* 각주 내용]
둘째 줄
 * 하나
  * 둘
 1. 셋
|| 가 || 나 ||
||<-2> 합침 ||
{{{#!syntax python
print(1)
}}}
{{{#!folding 접기
안쪽 글
}}}
> 인용문
----
[include(틀:정보, 이름=값)]
<math>E=mc^2</math>
[[분류:시험]]
[[분류:예시]]"""

MEDIAWIKI = """== 개요 ==
'''굵게''' 와 ''기울임'' [[다른 문서|링크]] [https://example.com 외부]<ref>각주 내용</ref>
* 하나
** 둘
# 셋
{| class="wikitable"
! 가 !! 나
|-
| colspan="2" | 합침
|}
<syntaxhighlight lang="python">
print(1)
</syntaxhighlight>
{{정보|이름=값}}
[[Category:시험]]"""

DOKU = """===== 개요 =====
**굵게** 와 //기울임// [[다른_문서|링크]] [[https://example.com|외부]]((각주 내용))
  * 하나
    * 둘
  - 셋
^ 가 ^ 나 ^
| 합침 ||
<code python>
print(1)
</code>
----
분류: [[분류:시험|시험]]
"""

MARKDOWN = """## 개요
**굵게** 와 *기울임* [링크](<다른 문서.md>) [외부](https://example.com)[^1]

- 하나
  - 둘
1. 셋

| 가 | 나 |
| --- | --- |
| 다 | 라 |

```python
print(1)
```

[^1]: 각주 내용
"""


def features(doc):
    f = {"heads": [], "links": set(), "urls": set(), "fns": [], "codes": [], "cats": list(doc.cats), "tpls": set(),
         "tables": [], "depth": 0, "texts": []}

    def inl(nodes):
        for n in nodes or []:
            k = n[0]
            if k == "link":
                f["links"].add(n[1])
                inl(n[2])
            elif k == "url":
                f["urls"].add(n[1])
            elif k == "fn":
                f["fns"].append(text_of(n[2]))
            elif k == "tpl":
                f["tpls"].add(n[1])
            elif k in ("b", "i", "u", "s", "sup", "sub"):
                inl(n[1])

    def blk(bs, depth=0):
        for b in bs:
            k = b[0]
            if k == "h":
                f["heads"].append(text_of(b[2]))
                inl(b[2])
            elif k == "p":
                f["texts"].append(text_of(b[1]))
                inl(b[1])
            elif k == "list":
                f["depth"] = max(f["depth"], depth + 1)
                for item in b[2]:
                    blk(item, depth + 1)
            elif k == "code":
                f["codes"].append((b[1], b[2].strip()))
            elif k == "table":
                f["tables"].append([[(text_of(c["c"]), c.get("cs", 1)) for c in r] for r in b[1]])
                for r in b[1]:
                    for c in r:
                        inl(c["c"])
            elif k == "tpl":
                f["tpls"].add(b[1])
            elif k in ("quote", "fold"):
                blk(b[-1], depth)
    blk(doc.blocks)
    return f


def check(src_text, src_fmt, title="시험"):
    doc = wikiconv.parse(src_text, src_fmt, title)
    base = features(doc)
    # AWM 왕복은 정확해야 한다
    awm = wikiconv.render(doc, "awm", title)
    back = wikiconv.parse(awm, "awm", title)
    assert back.blocks == doc.blocks and back.cats == doc.cats, (src_fmt, doc.blocks, back.blocks, awm)
    for fmt in wikiconv.FORMATS:
        out = wikiconv.render(doc, fmt, title)
        got = features(wikiconv.parse(out, fmt, title))
        for key in ("heads", "urls", "fns", "codes", "cats"):
            assert got[key] == base[key], (src_fmt, fmt, key, base[key], got[key], out)
        links = base["links"] | ({"틀:정보"} if fmt in ("dokuwiki", "markdown") and base["tpls"] else set())
        assert got["links"] >= base["links"] and got["links"] <= links, (src_fmt, fmt, base["links"], got["links"], out)
        if fmt in ("namumark", "mediawiki"):
            assert got["tpls"] == base["tpls"], (src_fmt, fmt, base["tpls"], got["tpls"], out)
        assert got["depth"] == base["depth"], (src_fmt, fmt, base["depth"], got["depth"], out)
        assert [[c for c, _ in r] for t in got["tables"] for r in t] == [[c for c, _ in r] for t in base["tables"] for r in t], \
            (src_fmt, fmt, base["tables"], got["tables"], out)
        assert [s for t in got["tables"] for r in t for _, s in r] == [s for t in base["tables"] for r in t for _, s in r], \
            (src_fmt, fmt, out)
    return base


def main():
    f = check(NAMU, "namumark")
    assert f["heads"] == ["개요"] and "다른 문서" in f["links"] and f["cats"] == ["시험", "예시"] and f["depth"] == 2
    assert f["fns"] == ["각주 내용"] and f["codes"] == [("python", "print(1)")] and f["tpls"] == {"틀:정보"}
    f = check(MEDIAWIKI, "mediawiki")
    assert f["links"] == {"다른 문서"} and f["cats"] == ["시험"] and f["tables"][0][1] == [("합침", 2)]
    f = check(DOKU, "dokuwiki")
    assert f["links"] == {"다른 문서"} and f["cats"] == ["시험"] and f["depth"] == 2, f
    f = check(MARKDOWN, "markdown")
    assert f["links"] == {"다른 문서"} and f["fns"] == ["각주 내용"] and f["depth"] == 2, f
    # 넘겨주기
    for fmt in wikiconv.FORMATS:
        d = wikiconv.parse(wikiconv.render(wikiconv.Doc([], [], "대상 문서"), fmt), fmt)
        assert d.redirect == "대상 문서", (fmt, d)
    # 위험한 글자가 문법으로 바뀌지 않는지
    tricky = wikiconv.Doc([["p", [["t", "별 ** 두 개와 '' 따옴표, [[괄호]] {{중괄호}} | 막대 ^ ~~ %% $5 그리고 50% //"]]]])
    for fmt in wikiconv.FORMATS + ("awm",):
        d = wikiconv.parse(wikiconv.render(tricky, fmt), fmt)
        assert text_of(d.blocks[0][1]) == text_of(tricky.blocks[0][1]), (fmt, wikiconv.render(tricky, fmt), d)
    print("test_wikiconv: 통과")


if __name__ == "__main__":
    main()
