"""대량 변환 시험: 도쿠위키 zip → 네 형식, 그리고 나온 결과를 다시 읽어 서로 돌려 본다."""
import gzip
import html
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bulk  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32

DOKU_PAGES = {
    "data/pages/start.txt": "====== 첫 화면 ======\n\n**굵게** 와 //기울임// [[projects:삼겹살|삼겹살 문서]] 그리고 [[foo]]\n"
                            "  * 하나\n    * 둘\n  - 셋\n{{logo.png|로고}}\n",
    "data/pages/projects/%EC%82%BC%EA%B2%B9%EC%82%B4.txt": "====== 삼겹살 가격 ======\n\n^ 가게 ^ 가격 ^\n| A마트 | 1000 |\n"
                                                         "<code python>\nprint(1)\n</code>\n{{projects:사진.png}}\n"
                                                         "----\n분류: [[분류:음식|음식]]\n",
    "data/pages/foo.txt": "문서 foo 입니다((각주 내용)).\n",
    "data/pages/wiki/syntax.txt": "도쿠위키 기본 설명서",
    "data/pages/wiki/welcome.txt": "기본 환영 문서",
    "data/media/logo.png": PNG,
    "data/media/projects/사진.png": PNG + b"1",
    "data/attic/start.1234.txt.gz": "무시되어야 하는 옛 판",
}


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)


def read_out(path):
    return zipfile.ZipFile(path)


def main():
    tmp = tempfile.mkdtemp(prefix="wikiconv-test-")
    src = os.path.join(tmp, "doku.zip")
    make_zip(src, DOKU_PAGES)

    # --- 도쿠위키 → 미디어위키
    out = os.path.join(tmp, "mw.zip")
    rep = bulk.convert_archive(src, "dokuwiki", "mediawiki", out)
    assert rep.pages == 3, rep.as_dict()          # 기본 설명서 2개는 빠짐
    assert rep.skipped["도쿠위키 기본 설명 문서"] == 2, rep.skipped
    assert rep.media_total == 2 and not rep.media_missing, rep.as_dict()
    z = read_out(out)
    names = z.namelist()
    assert "mediawiki-import.xml" in names and "images/logo.png" in names and "images/사진.png" in names, names
    xml = html.unescape(z.read("mediawiki-import.xml").decode("utf-8"))
    assert "<title>첫 화면</title>" in xml and "<title>삼겹살 가격</title>" in xml, xml
    assert "[[삼겹살 가격|삼겹살 문서]]" in xml, xml          # 도쿠위키 ID 링크가 제목으로 풀린다
    assert "[[File:logo.png|로고]]" in xml and "[[File:사진.png]]" in xml, xml
    assert "[[Category:음식]]" in xml and "'''굵게'''" in xml and "<ref>각주 내용</ref>" in xml, xml
    assert "옛 판" not in xml                                # attic 은 옮기지 않는다

    # --- 미디어위키 결과를 다시 읽어 오픈나무로, 거기서 마크다운으로
    out2 = os.path.join(tmp, "namu.zip")
    rep = bulk.convert_archive(out, "mediawiki", "opennamu", out2)
    assert rep.pages == 3 and rep.media_total == 2, rep.as_dict()
    z = read_out(out2)
    dbfile = os.path.join(tmp, "data.db")
    open(dbfile, "wb").write(z.read("data.db"))
    db = sqlite3.connect(dbfile)
    titles = {t for (t,) in db.execute("select title from data")}
    assert titles == {"첫 화면", "삼겹살 가격", "Foo"}, titles
    body = dict(db.execute("select title, data from data"))
    assert "[[분류:음식]]" in body["삼겹살 가격"] and "[[파일:logo.png|로고]]" in body["첫 화면"] or "파일:logo.png" in body["첫 화면"], body
    assert db.execute("select count(*) from back where type='cat'").fetchone()[0] == 1
    db.close()

    out3 = os.path.join(tmp, "md.zip")
    rep = bulk.convert_archive(out2, "opennamu", "markdown", out3)   # zip 안의 data.db 를 찾아 읽는다
    assert rep.pages == 3, rep.as_dict()
    z = read_out(out3)
    names = z.namelist()
    assert "wiki-markdown/삼겹살 가격.md" in names and "wiki-markdown/logo.png" in names, names
    md = z.read("wiki-markdown/삼겹살 가격.md").decode("utf-8")
    assert "| **가게** | **가격** |" in md and "```python" in md, md

    # --- 마크다운 → 도쿠위키(폴더 입력도 된다)
    folder = os.path.join(tmp, "mdfolder")
    os.makedirs(os.path.join(folder, "문서"))
    open(os.path.join(folder, "문서", "인사.md"), "w", encoding="utf-8").write("# 인사\n**안녕** [[다른]]\n")
    open(os.path.join(folder, "문서", "그림.png"), "wb").write(PNG)
    out4 = os.path.join(tmp, "doku-out.zip")
    rep = bulk.convert_archive(folder, "markdown", "dokuwiki", out4)
    z = read_out(out4)
    assert rep.pages == 1 and "data/media/그림.png" in z.namelist(), (rep.as_dict(), z.namelist())
    page = [n for n in z.namelist() if n.startswith("data/pages/")][0]
    assert "**안녕**" in z.read(page).decode("utf-8")

    # --- 미디어위키 XML(.xml.gz) 단독 파일 → 도쿠위키, 다른 이름공간은 건너뜀
    xmlgz = os.path.join(tmp, "dump.xml.gz")
    with gzip.open(xmlgz, "wt", encoding="utf-8") as f:
        f.write('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
                "<page><title>사과</title><ns>0</ns><revision><timestamp>2024-01-01T00:00:00Z</timestamp>"
                "<text>옛 글</text></revision><revision><timestamp>2024-02-01T00:00:00Z</timestamp>"
                "<contributor><username>김</username></contributor><text>'''사과'''는 과일. [[Category:과일]]</text></revision></page>"
                "<page><title>Talk:사과</title><ns>1</ns><revision><timestamp>2024-02-01T00:00:00Z</timestamp>"
                "<text>토론</text></revision></page></mediawiki>")
    out5 = os.path.join(tmp, "doku2.zip")
    rep = bulk.convert_archive(xmlgz, "mediawiki", "dokuwiki", out5)
    z = read_out(out5)
    txt = z.read([n for n in z.namelist() if n.startswith("data/pages/")][0]).decode("utf-8")
    assert rep.pages == 1 and "**사과**는 과일." in txt and "옛 글" not in txt, (rep.as_dict(), txt)
    assert sum(rep.skipped.values()) == 1, rep.skipped

    # --- 실제 위키에서 나온 경우: 플러그인 {{top}} 은 그림이 아니다, 확장자가 낯선 미디어도 옮긴다, 보이지 않는 글자가 낀 그림 이름
    odd = os.path.join(tmp, "odd.zip")
    make_zip(odd, {"data/pages/a.txt": "{{top}}\n{{pic:가﻿_나.jpg}} {{책.epub}}\n", "data/media/pic/가_나.jpg": PNG,
                   "data/media/책.epub": b"epub", "data/media/.htaccess": "x"})
    out6 = os.path.join(tmp, "odd-out.zip")
    rep = bulk.convert_archive(odd, "dokuwiki", "mediawiki", out6)
    assert rep.media_total == 2 and not rep.media_missing and rep.raw == 1, rep.as_dict()
    names = read_out(out6).namelist()
    assert "images/가_나.jpg" in names and "images/책.epub" in names, names

    # --- 오픈나무 [include(…)] 의 쉼표: 이름·인자에 쉼표가 있어도 왕복한다(이스케이프 찌꺼기 \x00 이 남지 않는다)
    s, _ = bulk.convert_text("{{page>50대,60대 글}}", "dokuwiki", "opennamu")
    assert s == "[include(50대\\,60대 글)]", s
    d = bulk.convert_text(s, "opennamu", "mediawiki")[0]
    assert d == "{{:50대,60대 글}}" and "\x00" not in d, d
    d2 = bulk.convert_text("[include(틀:정보, 이름=가\\,나)]", "opennamu", "mediawiki")[0]
    assert d2 == "{{정보|이름=가,나}}", d2

    # --- 도쿠위키 인터위키 [[wp>…]]: 기본 목록에 있는 것은 주소로, 이 위키에만 있는 이름은 원문으로
    got = bulk.convert_text("[[wp>Wiki]] [[wpko>대한민국|한국]] [[google>가 나]] [[내위키>문서]]", "dokuwiki", "mediawiki")
    assert "[https://en.wikipedia.org/wiki/Wiki Wiki]" in got[0] and "[https://ko.wikipedia.org/wiki/%EB%8C%80%ED%95%9C%EB%AF%BC%EA%B5%AD 한국]" in got[0], got
    assert "https://www.google.com/search?q=%EA%B0%80%20%EB%82%98" in got[0] and got[1] == 1, got

    # --- 문서 끼워 넣기 {{page>문서}}: 형식마다 같은 뜻으로, 섹션 지정·내용을 바꾸는 플래그는 원문으로 남긴다
    inc = "앞\n\n{{page>수태음폐경}}\n\n{{page>다른 글&noheader&nofooter}}\n\n뒤"
    got = {f: bulk.convert_text(inc, "dokuwiki", f)[0] for f in ("mediawiki", "opennamu", "markdown")}
    assert "{{:수태음폐경}}" in got["mediawiki"] and "{{:다른 글}}" in got["mediawiki"], got
    assert "[include(수태음폐경)]" in got["opennamu"] and "[include(다른 글)]" in got["opennamu"], got
    assert "[수태음폐경](<수태음폐경.md>)" in got["markdown"], got
    for f, syn in (("mediawiki", "mediawiki"), ("opennamu", "opennamu")):
        back, raw = bulk.convert_text(got[f], f, "dokuwiki")
        assert "{{page>수태음폐경}}" in back and "{{page>다른_글}}" in back and raw == 0, (f, back)
    _, raw = bulk.convert_text("{{page>글#섹션}}\n\n{{page>글&firstseconly}}\n\n{{page>글&noheader}}", "dokuwiki", "mediawiki")
    assert raw == 2, raw
    # 대문자로만 된 문서 이름({{:GDP}})을 {{PAGENAME}} 같은 매직 워드로 착각하지 않는다
    mw, raw = bulk.convert_text("{{:GDP}} {{:AI필터}}", "mediawiki", "opennamu")
    assert "[include(GDP)]" in mw and "[include(AI필터)]" in mw and raw == 0, (mw, raw)
    # 문서를 끼워 넣는 것(틀: 없음)과 틀(틀:)은 구분된다
    mw, _ = bulk.convert_text("{{:문서}} {{정보|이름=값}}", "mediawiki", "opennamu")
    assert "[include(문서)]" in mw and "[include(틀:정보, 이름=값)]" in mw, mw

    # --- 각주 안의 [대괄호] 때문에 각주가 일찍 끝나 뒤의 글이 사라지지 않는다(실제 위키에서 나온 경우)
    s, _ = bulk.convert_text("앞 글((남캐 장르라면...[NTR]? 끝)) 뒤 글입니다.", "dokuwiki", "opennamu")
    back, _ = bulk.convert_text(s, "opennamu", "dokuwiki")
    assert "[NTR]? 끝))" in back and back.rstrip().endswith("뒤 글입니다."), (s, back)

    # --- 제목 끝의 마침표는 마크다운을 거쳐도 남는다
    from wikiconv.markdown import md_name, md_title
    for t in ("시민이여 깨어나라.", "죽음에 가까이..", "a.b", "분류:가"):
        assert md_title(md_name(t)) == t, (t, md_name(t))
    # --- 파일을 못 찾은 그림은 파일 이름만 남긴다
    miss = os.path.join(tmp, "miss.zip")
    make_zip(miss, {"data/pages/a.txt": "{{pic:야짤:없는그림.jpg}}\n"})
    rep = bulk.convert_archive(miss, "dokuwiki", "mediawiki", os.path.join(tmp, "miss-out.zip"))
    assert rep.media_missing == {"pic:야짤:없는그림.jpg"} and "[[File:없는그림.jpg]]" in html.unescape(
        read_out(os.path.join(tmp, "miss-out.zip")).read("mediawiki-import.xml").decode("utf-8")), rep.as_dict()

    # --- 실패 처리와 잘못된 입력
    for bad in ((src, "markdown", "dokuwiki"), (src, "dokuwiki", "dokuwiki")):
        try:
            bulk.convert_archive(*bad[:1], bad[1], bad[2], os.path.join(tmp, "x.zip"))
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert not os.path.exists(os.path.join(tmp, "x.zip")) and not os.path.exists(os.path.join(tmp, "x.zip.part"))

    # --- 붙여넣기 변환·형식 짐작
    r, raw = bulk.convert_text("== 제목 ==\n'''굵게''' [[링크]]", "mediawiki", "namumark")
    assert r.startswith("== 제목 ==") and "'''굵게'''" in r and "[[링크]]" in r, r
    assert bulk.guess_format("====== 제목 ======\n**굵게** //기울임//")[0] == "dokuwiki"
    assert bulk.guess_format("== 제목 ==\n'''굵게''' [[Category:가]]")[0] == "mediawiki"
    assert bulk.guess_format("= 개요 =\n||가||나||\n[[분류:가]]")[0] == "opennamu"
    assert bulk.guess_format("# 제목\n**굵게** [링크](a.md)\n```py\nx\n```")[0] == "markdown"
    assert bulk.guess_format("그냥 글")[0] is None
    print("test_bulk: 통과")


if __name__ == "__main__":
    main()
