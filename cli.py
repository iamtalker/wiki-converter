"""위키 변환기 — 명령줄 (표준 라이브러리만 사용).

    python cli.py text --from dokuwiki --to mediawiki 문서.txt        # 파일(없으면 표준 입력)을 바꿔 표준 출력으로
    python cli.py bulk data.zip --from dokuwiki --to mediawiki        # 통째로 바꿔 zip 으로
    python cli.py guess 문서.txt                                      # 어느 위키 문법인지 짐작

형식: dokuwiki · mediawiki · opennamu(= namumark) · markdown
"""
import argparse
import os
import sys

import bulk


def read_in(path):
    if path and path != "-":
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    return sys.stdin.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="위키 변환기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("text", help="문서 하나 변환")
    t.add_argument("file", nargs="?", default="-")
    t.add_argument("--from", dest="src", default="auto", help="원본 형식(기본: 자동 감지)")
    t.add_argument("--to", dest="dst", required=True)
    t.add_argument("--title", default="")
    b = sub.add_parser("bulk", help="zip·폴더·파일째로 변환")
    b.add_argument("path")
    b.add_argument("--from", dest="src", required=True)
    b.add_argument("--to", dest="dst", required=True)
    b.add_argument("--out", default="", help="만들 zip 경로(기본: output 폴더)")
    b.add_argument("--doku-fnencode", choices=("url", "utf-8"), default="url",
                   help="도쿠위키로 만들 때 파일 이름 방식(도쿠위키 설정 fnencode 와 같게)")
    g = sub.add_parser("guess", help="형식 짐작")
    g.add_argument("file", nargs="?", default="-")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        if a.cmd == "guess":
            fmt, scores = bulk.guess_format(read_in(a.file))
            print(f"{bulk.LABEL[fmt] if fmt else '알 수 없음'}  점수: {scores}")
        elif a.cmd == "text":
            text = read_in(a.file)
            src = a.src
            if src == "auto":
                src, _ = bulk.guess_format(text)
                if not src:
                    raise ValueError("형식을 알아내지 못했습니다. --from 으로 알려 주세요")
                print(f"(감지한 형식: {bulk.LABEL[src]})", file=sys.stderr)
            out, raw = bulk.convert_text(text, src, a.dst, a.title)
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
            if raw:
                print(f"(옮기지 못해 원문으로 남긴 곳 {raw}곳)", file=sys.stderr)
        else:
            stem = os.path.splitext(os.path.basename(os.path.normpath(a.path)))[0]
            out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output",
                                        f"{stem}-{bulk.norm_fmt(a.dst)}.zip")

            def progress(done, total):
                print(f"\r문서 {done:,}" + (f" / {total:,}" if total else ""), end="", file=sys.stderr, flush=True)
            rep = bulk.convert_archive(a.path, a.src, a.dst, out, progress, doku_fnencode=a.doku_fnencode)
            print(file=sys.stderr)
            print(f"완료: 문서 {rep.pages:,}개 · 미디어 {rep.media_total:,}개 · {rep.seconds:.1f}초 → {rep.out}")
            for k, v in rep.skipped.items():
                print(f"  건너뜀: {k} {v:,}개")
            if rep.raw:
                print(f"  옮길 수 없어 원문으로 남긴 곳 {rep.raw:,}곳 (문서 {rep.raw_pages:,}개)")
            if rep.media_missing:
                print(f"  파일을 찾지 못한 그림 {len(rep.media_missing)}개")
            if rep.failed:
                print(f"  오류가 난 문서 {len(rep.failed)}개(원문 그대로 담음): " + "; ".join(rep.failed[:5]))
    except (ValueError, OSError) as e:
        print("오류: " + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
