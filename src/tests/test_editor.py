"""편집기 시험: 파일 읽기·쓰기 도우미, 미리보기 배치, 창을 실제로 띄워 열기·변환·저장·링크 따라가기."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import editor  # noqa: E402
import preview  # noqa: E402
import wikiconv  # noqa: E402

SAMPLE = """# 삼겹살

본문 **굵게** *기울임* [돼지](<돼지고기.md>) [외부](https://example.com) ![사진](<사진.png>)[^1]

[^1]: 인라인 각주

> 인용 안

- 하나
  - 둘
1. 셋

| 가 | 나 |
|---|---|
| 한글 | b |

```python
print(1)
```
"""


def tagged(segs, tag):
    return "".join(t for t, tags, _ in segs if tag in tags)


def main():
    # --- 파일 도우미
    assert editor.decode_file("가나\r\n다".encode("utf-8-sig")) == ("가나\n다", "utf-8", "\r\n")
    assert editor.decode_file("가나".encode("cp949"))[:2] == ("가나", "cp949")
    assert editor.encode_file("가\n나", "utf-8", "\r\n") == "가\r\n나".encode("utf-8")
    assert editor.detect_format("====== 제목 ======\n**굵게** //기울임//", "a.md") == "markdown"   # .md 는 마크다운
    assert editor.detect_format("# 제목\n**굵게**", "a.md") == "markdown"
    assert editor.detect_format("====== 제목 ======\n**굵게** //기울임//", "a.txt") == "dokuwiki"
    assert editor.detect_format("그냥 글", "a.txt") == "markdown"
    cmd = editor.launcher_command()
    assert cmd.startswith('"') and cmd.endswith('"%1"') and "위키나루.pyw" in cmd, cmd

    # --- 서식 도구(순수 함수)
    assert editor.wrap_text("글", "**", "**", "굵은 글") == ("**글**", 2, 3)
    assert editor.wrap_text("", "**", "**", "굵은 글") == ("**굵은 글**", 2, 6)
    assert editor.wrap_text("**글**", "**", "**", "굵은 글") == ("글", 0, 1)   # 이미 감싸져 있으면 벗김
    assert editor.wrap_text("문서", "[", "](주소)", "링크 글") == ("[문서](주소)", 5, 7)  # 주소 자리를 골라 줌
    assert editor.toggle_prefix(["가", "", "나"], "{n}. ", r"^\d+\. ") == ["1. 가", "", "2. 나"]
    assert editor.toggle_prefix(["- 가", "- 나"], "- ", r"^[-*+] ") == ["가", "나"]
    assert editor.set_heading("본문", 2, "markdown") == "## 본문"
    assert editor.set_heading("## 본문", 0, "markdown") == "본문"
    assert editor.set_heading("본문", 1, "dokuwiki") == "====== 본문 ======"
    assert editor.set_heading("====== 본문 ======", 3, "dokuwiki") == "==== 본문 ===="
    assert editor.set_heading("본문", 2, "mediawiki") == "== 본문 =="
    # 각 문법의 표기가 그 문법으로 읽히는지(서식 버튼이 만든 글을 다시 읽어 구조가 나와야 함)
    for fmt in editor.LABEL:
        syn = {"opennamu": "namumark"}.get(fmt, fmt)
        b0, b1 = editor.WRAP[fmt]["b"]
        doc = wikiconv.parse(f"앞 {b0}굵게{b1} 뒤", syn)
        assert any(n[0] == "b" for n in doc.blocks[0][1]), (fmt, doc)
        for kind in ("ul", "ol"):
            tpl, _ = editor.LINE[fmt][kind]
            d = wikiconv.parse(tpl.format(n=1) + "항목\n" + tpl.format(n=2) + "둘째", syn)
            assert d.blocks[0][0] == "list" and len(d.blocks[0][2]) == 2, (fmt, kind, d)
        d = wikiconv.parse(editor.set_heading("제목", 2, fmt) + "\n\n본문", syn)
        assert d.blocks[0][0] == "h", (fmt, d)
        d = wikiconv.parse(editor.BLOCK["table"][fmt], syn)
        assert d.blocks[0][0] == "table" and len(d.blocks[0][1]) == 2, (fmt, d)
        d = wikiconv.parse("앞\n\n" + editor.BLOCK["codeblock"][fmt].replace("{}", "x = 1", 1) + "\n", syn)
        assert any(b[0] in ("code", "pre") for b in d.blocks), (fmt, d)

    # --- 사이드바 목록: 문법마다 항목이 있고, 보여 주는 모양이 그 문법으로 읽힌다
    for fmt in editor.LABEL:
        syn = {"opennamu": "namumark"}.get(fmt, fmt)
        ents = editor.syntax_entries(fmt)
        names = [n for _, n, _, _ in ents]
        for want in ("제목 1", "굵게", "기울임", "글머리 목록", "번호 목록", "인용", "링크", "그림", "표", "가로줄", "코드 블록"):
            assert want in names, (fmt, want, names)
        by = {n: (ex, act) for _, n, ex, act in ents}
        assert wikiconv.parse(by["제목 2"][0], syn).blocks[0][0] == "h", fmt
        assert any(n[0] == "b" for n in wikiconv.parse("가 " + by["굵게"][0] + " 나", syn).blocks[0][1]), fmt
        assert all(act[0] in ("wrap", "heading", "line", "block") for _, _, _, act in ents)

    # --- 미리보기 배치
    segs = preview.layout(wikiconv.parse(SAMPLE, "markdown", "삼겹살"))
    assert "삼겹살" in tagged(segs, "h1") and tagged(segs, "b").startswith("굵게")
    assert tagged(segs, "i") == "기울임" and "돼지" in tagged(segs, "link") and "외부" in tagged(segs, "link")
    links = {ex[1:] for _, _, ex in segs if ex and ex[0] == "link"}
    assert ("page", "돼지고기") in links and ("url", "https://example.com") in links, links
    assert any(ex and ex[0] == "img" and ex[1] == "사진.png" for _, _, ex in segs)
    text = "".join(t for t, _, _ in segs)
    assert "• 하나" in text and "3. 셋" not in text and "1. 셋" in text and "[1]" in text and "1. 인라인 각주" in text
    assert "│ 한글 │ b" in text.replace("  ", " ") or "한글" in text
    assert "print(1)" in tagged(segs, "codeblock")
    assert any("M1" in tags and "quote" in tags for _, tags, _ in segs)
    assert any(tag.startswith("B") for _, tags, _ in segs for tag in tags)
    # 모르는 구조가 와도 멈추지 않는다
    assert preview.layout(wikiconv.Doc([["raw", "dokuwiki", "{{gallery>x}}"], ["toc"], ["hr"]]))

    # --- 창을 띄워 본다 (듀얼 패널)
    tmp = tempfile.mkdtemp()
    note = os.path.join(tmp, "삼겹살.md")
    other = os.path.join(tmp, "돼지고기.md")
    with open(note, "w", encoding="utf-8", newline="") as f:
        f.write(SAMPLE.replace("\n", "\r\n"))
    with open(other, "w", encoding="utf-8") as f:
        f.write("돼지고기 문서입니다.\n")
    editor.SETTINGS_PATH = os.path.join(tmp, "settings.json")   # 사용자 폴더를 건드리지 않게
    root, ed = editor.run_gui([note], loop=False)

    def pick(panel, label):
        panel.combo.set(label)
        panel.combo.event_generate("<<ComboboxSelected>>")
        root.update()

    def type_into(panel, text, at="end"):
        panel.text.insert(at, text)
        root.update()
        ed.flush()
        root.update()

    try:
        root.withdraw()
        root.update()
        L, R = ed.panels
        # 기본: 왼쪽 마크다운(편집), 오른쪽 렌더링(읽기 전용)
        assert [p.mode for p in ed.panels] == ["markdown", "rendered"], [p.mode for p in ed.panels]
        # 드롭박스: 렌더링이 맨 위, 구분선, 그다음 문법들(좌우 순서가 같다) + 렌더링은 모양도 다르다
        want = ["◉ 렌더링 (읽기 전용)", "─" * 16, "마크다운", "오픈나무(나무마크)", "도쿠위키", "미디어위키"]
        assert list(L.combo.cget("values")) == want and list(R.combo.cget("values")) == want
        assert R.combo.get() == want[0] and L.combo.get() == "마크다운"
        assert str(R.combo.cget("style")) == str(L.combo.cget("style"))   # 색(스타일)으로 구별하지 않는다
        L.combo.set("─" * 16)                      # 구분선을 고르면 원래대로
        L.combo.event_generate("<<ComboboxSelected>>")
        root.update()
        assert L.mode == "markdown" and L.combo.get() == "마크다운"
        assert ed.src_fmt == "markdown" and ed.nl == "\r\n" and not ed.dirty
        assert "삼겹살" in L.get_text() and "삼겹살" in R.view.get("1.0", "end") and "• 하나" in R.view.get("1.0", "end")
        assert str(L.edit_frame) in str(L.edit_frame.pack_info()) or L.edit_frame.winfo_manager() == "pack"
        assert R.edit_frame.winfo_manager() == "" and R.view_frame.winfo_manager() == "pack"
        assert "삼겹살.md" in root.title() and not root.title().startswith("*")

        # 1) 왼쪽에서 쓰면 오른쪽 렌더링이 실시간으로 따라온다(150ms 뒤, flush 로 바로)
        type_into(L, "\n추가한 글입니다.\n")
        assert ed.dirty and root.title().startswith("*")
        assert "추가한 글입니다." in R.view.get("1.0", "end")

        # 2) 왼쪽 렌더링 + 오른쪽 마크다운: 오른쪽에서 편집하고 왼쪽이 읽기 모드
        pick(L, "렌더링")
        pick(R, "마크다운")
        assert [p.mode for p in ed.panels] == ["rendered", "markdown"]
        assert ed.panel is R and ed.text is R.text and ed.fmt == "markdown"
        assert "추가한 글입니다." in R.get_text()
        type_into(R, "오른쪽에서 쓴 글\n")
        assert "오른쪽에서 쓴 글" in L.view.get("1.0", "end")

        # 3) 오픈나무 | 마크다운: 실시간 상호 변환(어느 쪽에서 써도 반대편이 그 문법으로 바뀐다)
        pick(L, "오픈나무(나무마크)")
        assert L.mode == "opennamu" and "= 삼겹살 =" in L.get_text() and "'''굵게'''" in L.get_text()
        type_into(L, "\n== 새 제목 ==\n'''왼쪽 굵게''' 글\n")
        assert "## 새 제목" in R.get_text() and "**왼쪽 굵게**" in R.get_text(), R.get_text()
        assert ed.src_fmt == "opennamu"
        type_into(R, "\n# 오른쪽 제목\n", at="end")
        assert "= 오른쪽 제목 =" in L.get_text() and ed.src_fmt == "markdown"
        # 서식 버튼은 지금 편집 중인 패널의 문법으로 들어간다
        L.text.focus_force()
        root.update()
        ed.set_active(L)
        L.text.delete("1.0", "end")
        L.text.insert("1.0", "글")
        L.text.tag_add("sel", "1.0", "1.end")
        ed.wrap("b")
        assert L.get_text() == "'''글'''", L.get_text()
        root.update()
        ed.flush()
        root.update()
        assert "**글**" in R.get_text()   # 반대편에는 마크다운 표기로

        # 4) 도쿠위키 | 미디어위키, 같은 문법 둘, 둘 다 렌더링도 문제없다
        pick(L, "도쿠위키")
        pick(R, "미디어위키")
        assert L.mode == "dokuwiki" and R.mode == "mediawiki"
        assert "**글**" in L.get_text() and "'''글'''" in R.get_text()
        pick(R, "도쿠위키")
        assert R.get_text() == L.get_text()
        type_into(L, "\n덧붙임\n")
        assert "덧붙임" in R.get_text()
        pick(L, "렌더링")
        pick(R, "렌더링")
        assert ed.panel is None and ed.text is None
        ed.wrap("b")                       # 편집할 패널이 없으면 안내만 하고 죽지 않는다
        assert "편집할 수 있는" in ed.status.cget("text")
        assert "덧붙임" in L.view.get("1.0", "end") and "덧붙임" in R.view.get("1.0", "end")
        pick(L, "마크다운")
        assert ed.panel is L and "덧붙임" in L.get_text()

        # 저장: 기준 글(마지막에 쓴 패널의 문법)을 원래 줄바꿈으로
        pick(R, "렌더링")
        assert ed.save() and not ed.dirty
        with open(note, "rb") as f:
            raw = f.read()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"") and "덧붙임".encode() in raw

        # 설정이 저장된다(패널 모드)
        st = editor.load_settings()
        assert st["left"] == "markdown" and st["right"] == "rendered", st

        # 링크 따라가기, 사이드바, 서식(마크다운 패널)
        ed.follow("page", "돼지고기")
        root.update()
        assert ed.path == other and "돼지고기 문서입니다." in L.get_text()
        ed.follow("page", "없는문서")
        assert ed.path == other
        assert ed.show_sidebar.get() and str(ed.sidebar) in [str(p) for p in ed.pane.panes()]
        rows = ed.tree.get_children()
        assert "**글**" in {ed.tree.item(i, "text") for r in rows for i in ed.tree.get_children(r)}
        assert ed.tree.heading("#0")["text"] == "문법" and ed.tree.heading("name")["text"] == "해설"
        L.text.delete("1.0", "end")
        L.text.insert("1.0", "하나\n둘\n셋")
        L.text.tag_add("sel", "1.0", "1.end")
        bold = next(i for i, a in ed.entry_actions.items() if a == ("wrap", "b"))
        ed.apply_action(ed.entry_actions[bold])
        assert L.text.get("1.0", "1.end") == "**하나**"
        L.text.tag_remove("sel", "1.0", "end")
        L.text.tag_add("sel", "2.0", "3.end")
        ed.line_prefix("ul")
        assert L.get_text() == "**하나**\n- 둘\n- 셋", L.get_text()
        ed.line_prefix("ul")
        assert L.get_text() == "**하나**\n둘\n셋"
        L.text.mark_set("insert", "1.0")
        L.text.tag_remove("sel", "1.0", "end")
        ed.heading(2)
        assert L.text.get("1.0", "1.end") == "## **하나**"
        ed.insert_block("table")
        assert "| --- | --- |" in L.get_text()
        ed.flip_sidebar()
        assert not ed.show_sidebar.get() and str(ed.sidebar) not in [str(p) for p in ed.pane.panes()]
        assert editor.load_settings().get("sidebar") is False
        ed.flip_sidebar()
        assert [str(p) for p in ed.pane.panes()].index(str(ed.sidebar)) == 0   # 맨 왼쪽

        # 되돌리기는 편집 중인 패널의 글을 되돌리고 반대편도 따라온다
        L.text.delete("1.0", "end")
        L.text.insert("1.0", "처음")
        ed.flush()
        L.text.insert("end", " 둘째")
        ed.flush()
        L.text.edit_undo()
        root.update()
        ed.flush()
        root.update()
        assert "둘째" not in L.get_text()

        # 사이드바·툴바: 빈 줄에서 눌러도 표시와 자리 글이 들어간다(제목·목록·인용)
        pick(L, "마크다운")
        ed.set_active(L)
        L.text.delete("1.0", "end")
        ed.heading(2)
        assert L.get_text() == "## 제목", repr(L.get_text())
        assert L.text.get("sel.first", "sel.last") == "제목"
        L.text.delete("1.0", "end")
        ed.line_prefix("ul")
        assert L.get_text() == "- 항목"
        L.text.delete("1.0", "end")
        ed.line_prefix("ol")
        assert L.get_text() == "1. 항목"
        L.text.delete("1.0", "end")
        ed.line_prefix("quote")
        assert L.get_text() == "> 인용문"
        for kind in ("table", "hr", "codeblock"):
            L.text.delete("1.0", "end")
            ed.apply_action(("block", kind))
            assert L.get_text().strip(), kind
        for fmt, label in (("dokuwiki", "도쿠위키"), ("mediawiki", "미디어위키"), ("opennamu", "오픈나무(나무마크)")):
            pick(L, label)
            for kind, act in (("제목", ("heading", 1)), ("글머리", ("line", "ul"))):
                L.text.delete("1.0", "end")
                ed.apply_action(act)
                assert L.get_text().strip() and ("제목" in L.get_text() or "항목" in L.get_text()), (fmt, kind, L.get_text())
        pick(L, "마크다운")

        # 보기 모드: 좌우 분할 / 렌더링만 / 원문만
        L.text.delete("1.0", "end")
        L.text.insert("1.0", "# 모드 시험\n\n본문 **굵게**\n")
        root.update()
        ed.flush()
        vis = lambda: [p.mode for p in ed.visible_panels()]   # noqa: E731
        assert ed.view_mode.get() == "split" and vis() == ["markdown", "rendered"]
        ed.view_mode.set("preview")
        ed.apply_view_mode()
        assert vis() == ["rendered"] and "모드 시험" in R.view.get("1.0", "end")
        assert ed.panel is None            # 렌더링만 볼 때는 편집 대상이 없다
        ed.view_mode.set("source")
        ed.apply_view_mode()
        assert vis() == ["markdown"] and ed.panel is L
        L.text.insert("end", "원문만 보는 중\n")
        root.update()
        ed.flush()
        ed.view_mode.set("split")
        ed.apply_view_mode()
        root.update()
        assert vis() == ["markdown", "rendered"] and "원문만 보는 중" in R.view.get("1.0", "end")
        assert editor.load_settings()["view"] == "split"
        # 렌더링 패널이 없을 때 '렌더링만'을 누르면 한쪽을 렌더링으로 바꿔 준다
        pick(R, "도쿠위키")
        ed.view_mode.set("preview")
        ed.apply_view_mode()
        assert vis() == ["rendered"] and any(not p.editable() for p in ed.panels)
        ed.view_mode.set("split")
        ed.apply_view_mode()

        # 큰 글은 스레드에서 변환해도 결과가 같고 입력이 막히지 않는다
        big = "".join(f"== 제목 {i} ==\n본문 '''굵게''' [[문서{i}]] 가나다라마바사\n\n" for i in range(1200))
        assert len(big) > editor.BIG_DOC
        pick(L, "오픈나무(나무마크)")
        pick(R, "마크다운")
        L.text.delete("1.0", "end")
        L.text.insert("1.0", big)
        root.update()
        ed.flush()
        deadline = time.time() + 20
        while time.time() < deadline and "## 제목 1199" not in R.get_text():
            root.update()
            time.sleep(0.02)
        assert "## 제목 1199" in R.get_text() and "**굵게**" in R.get_text()

        # 파일 > 대량 변환…: 도쿠위키 zip → 미디어위키 zip (대화상자에서 그대로 실행)
        import zipfile
        src_zip = os.path.join(tmp, "doku.zip")
        with zipfile.ZipFile(src_zip, "w") as z:
            z.writestr("data/pages/start.txt", "====== 첫 화면 ======\n\n**굵게** [[foo]]\n")
            z.writestr("data/pages/foo.txt", "문서 foo\n")
        dlg = ed.bulk_dialog()
        dlg.src.set("도쿠위키")
        dlg.dst.set("미디어위키")
        dlg.refresh()
        dlg.path.set(src_zip)
        dlg.out.set(os.path.join(tmp, "bulk-out.zip"))
        dlg.out_touched = True
        dlg.start()
        deadline = time.time() + 30
        while time.time() < deadline and dlg.running:
            root.update()
            time.sleep(0.02)
        root.update()
        rep = getattr(dlg, "report", None)
        assert rep is not None and rep.pages == 2 and os.path.exists(os.path.join(tmp, "bulk-out.zip")), dlg.msg.cget("text")
        assert "완료" in dlg.msg.cget("text")
        assert "mediawiki-import.xml" in zipfile.ZipFile(os.path.join(tmp, "bulk-out.zip")).namelist()
        dlg.dst.set("도쿠위키")        # 같은 형식은 막는다
        dlg.start()
        assert "같습니다" in dlg.msg.cget("text")
        dlg.path.set(os.path.join(tmp, "없는파일.zip"))
        dlg.dst.set("마크다운")
        dlg.start()
        assert "찾지 못했습니다" in dlg.msg.cget("text")
        dlg.close()
        root.update()

        # 잘못된 글도 죽지 않는다
        pick(L, "도쿠위키")
        L.text.delete("1.0", "end")
        L.text.insert("1.0", "{{{{ [[ ((( ''' \x01")
        ed.flush()
        root.update()
        assert ed.status.cget("text")

        # 외부 이미지: 기본으로 켜져 있고, 주소의 JPEG 를 내려받아 렌더링에 그림으로 보인다(끄면 안 보임)
        import http.server
        import socketserver
        import struct
        import threading
        import zlib
        imgdir = tempfile.mkdtemp()
        w_, h_ = 800, 400
        rows = b"".join(b"\x00" + b"".join(bytes([x * 255 // w_, y * 255 // h_, 90]) for x in range(w_))
                        for y in range(h_))

        def chunk(t, b):
            return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
        png_bytes = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w_, h_, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        with open(os.path.join(imgdir, "x.png"), "wb") as f:
            f.write(png_bytes)
        editor.gdip_convert(os.path.join(imgdir, "x.png"), os.path.join(imgdir, "photo.jpg"), "jpeg")

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=imgdir, **k)

            def log_message(self, *a):
                pass
        srv = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/photo.jpg"
        try:
            assert ed.ext_images.get() is True                      # 기본값: 켜짐
            pick(L, "마크다운")
            pick(R, "◉ 렌더링 (읽기 전용)")
            L.text.delete("1.0", "end")
            L.text.insert("1.0", f"# 사진\n\n![대체 글]({url})\n")
            root.update()
            ed.flush()
            deadline = time.time() + 20
            while time.time() < deadline and not R.images:
                root.update()
                time.sleep(0.02)
            assert len(R.images) == 1 and R.images[0].width() <= editor.IMG_MAX_W, R.images
            assert ed.imgs[url][0] == "ok" and "[그림: 대체 글]" not in R.view.get("1.0", "end")
            ed.ext_images.set(False)                                  # 끄면 글자 칩으로 돌아가고 안내가 뜬다
            ed.toggle_ext_images()
            root.update()
            assert not R.images and "[그림: 대체 글]" in R.view.get("1.0", "end")
            assert "외부 이미지 1개" in ed.status.cget("text")
            assert editor.load_settings()["external_images"] is False
            ed.ext_images.set(True)
            ed.toggle_ext_images()
            root.update()
            assert len(R.images) == 1                                # 캐시가 있어 바로 보인다
        finally:
            srv.shutdown()
        # 안 열리는 주소는 죽지 않고 이유를 알려 준다
        bad = "http://127.0.0.1:9/없는그림.jpg"
        L.text.delete("1.0", "end")
        L.text.insert("1.0", f"![안됨]({bad})\n")
        root.update()
        ed.flush()
        deadline = time.time() + 20
        while time.time() < deadline and ed.imgs.get(bad, ("pending",))[0] == "pending":
            root.update()
            time.sleep(0.02)
        root.update()
        assert ed.imgs[bad][0] == "fail" and "불러오지 못했습니다" in ed.status.cget("text"), ed.status.cget("text")

        # 파일을 열어도 내가 고른 패널 선택은 그대로이고, 저장은 파일 자신의 문법으로
        pick(L, "도쿠위키")
        pick(R, "◉ 렌더링 (읽기 전용)")
        md_file = os.path.join(tmp, "문법유지.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# 제목\n\n**굵게** 글\n")
        ed.open_path(md_file, ask=False)
        root.update()
        assert [p.mode for p in ed.panels] == ["dokuwiki", "rendered"]
        assert "====== 제목 ======" in L.get_text() and "**굵게**" in L.get_text()   # 도쿠위키로 바꿔 보여 줌
        assert not ed.dirty
        type_into(L, "\n덧붙임\n")
        assert ed.dirty and ed.save()
        content = open(md_file, encoding="utf-8").read()
        assert "# 제목" in content and "덧붙임" in content and "======" not in content, content   # 파일은 마크다운 그대로
        assert [p.mode for p in ed.panels] == ["dokuwiki", "rendered"]

        # 껐다 켜도 기억할 것을 정해 두고 저장(창을 닫을 때와 같은 저장)
        pick(L, "도쿠위키")
        pick(R, "◉ 렌더링 (읽기 전용)")
        ed.view_mode.set("split")
        ed.apply_view_mode()
        ed.zoom(2)
        want_size = ed.size
        root.deiconify()                      # 숨긴 창은 크기가 적용되지 않으므로 잠깐 보인다
        root.geometry("980x620+40+30")
        root.update()
        ed.save_ui_state(window=True)
        root.withdraw()
        saved = editor.load_settings()
        assert saved["left"] == "dokuwiki" and saved["right"] == "rendered" and saved["size"] == want_size, saved
        assert saved["geometry"].startswith("980x620")
    finally:
        ed.dirty = False
        root.destroy()

    # 프로그램을 다시 켠다: 패널 선택·글자 크기·창 크기가 그대로 돌아온다
    root2, ed2 = editor.run_gui([], loop=False)
    try:
        root2.update()
        assert [p.mode for p in ed2.panels] == ["dokuwiki", "rendered"], [p.mode for p in ed2.panels]
        assert ed2.panels[0].combo.get() == "도쿠위키" and ed2.panels[1].combo.get().startswith("◉")
        assert ed2.size == want_size
        assert root2.geometry().startswith("980x620"), root2.geometry()
        # 저장된 값이 이상해도 죽지 않고 기본값으로
        ed2.dirty = False
        editor.save_settings({"left": "엉뚱", "right": None, "geometry": "9999x9999+99999+99999", "size": "abc"})
    finally:
        root2.destroy()
    editor.save_settings({"left": "엉뚱", "right": None, "geometry": "9999x9999+99999+99999", "size": 9999})
    root3, ed3 = editor.run_gui([], loop=False)
    try:
        root3.withdraw()
        root3.update()
        assert [p.mode for p in ed3.panels] == ["markdown", "rendered"] and ed3.size == 28
    finally:
        ed3.dirty = False
        root3.destroy()
    print("test_editor: 통과")


if __name__ == "__main__":
    main()
