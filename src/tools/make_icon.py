"""앱 아이콘(assets/icon.png, assets/icon.ico)을 코드로 그린다 — 표준 라이브러리만 사용.

바이올렛 둥근 사각형 위에 흰 문서(접힌 모서리 + 글줄)와 연필. 256px 로 그려 16·32·48 로 줄인다.
    python src/tools/make_icon.py
"""
import math
import os
import struct
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "assets")
ACCENT, ACCENT_DARK, WHITE, LINE, PENCIL = (124, 58, 237), (91, 33, 182), (255, 255, 255), (196, 181, 253), (251, 191, 36)
SIZE, SS = 256, 3   # 최종 크기, 한 변 수퍼샘플링


def rrect(x, y, x0, y0, x1, y1, r):
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def capsule(x, y, ax, ay, bx, by, w):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return (x - (ax + t * dx)) ** 2 + (y - (ay + t * dy)) ** 2 <= (w / 2) ** 2


def poly(x, y, pts):
    inside, j = False, len(pts) - 1
    for i, (xi, yi) in enumerate(pts):
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def color_at(x, y):
    """(x, y) 는 0~256 좌표. 위에 그려진 것이 이긴다. 투명이면 None."""
    if not rrect(x, y, 8, 8, 248, 248, 52):
        return None
    c = ACCENT
    # 문서(흰 종이) + 접힌 모서리
    page = [(62, 44), (150, 44), (194, 88), (194, 212), (62, 212)]
    if poly(x, y, page):
        c = WHITE
        if poly(x, y, [(150, 44), (194, 88), (150, 88)]):
            c = LINE
    # 글줄
    for ly, x1 in ((112, 170), (136, 170), (160, 140)):
        if capsule(x, y, 84, ly, x1, ly, 10):
            c = LINE if c == WHITE else c
    # 연필(오른쪽 아래, 대각선)
    body = [(138, 190), (196, 132), (220, 156), (162, 214)]
    if poly(x, y, body):
        c = PENCIL
    if poly(x, y, [(196, 132), (210, 118), (234, 142), (220, 156)]):
        c = WHITE
    if poly(x, y, [(138, 190), (162, 214), (124, 226)]):
        c = ACCENT_DARK
    return c


def render():
    n = SIZE * SS
    px = bytearray(SIZE * SIZE * 4)
    for oy in range(SIZE):
        for ox in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    c = color_at((ox * SS + sx + 0.5) / SS, (oy * SS + sy + 0.5) / SS)
                    if c:
                        r, g, b, a = r + c[0], g + c[1], b + c[2], a + 1
            i = (oy * SIZE + ox) * 4
            if a:
                px[i:i + 4] = bytes((r // a, g // a, b // a, round(255 * a / (SS * SS))))
    return px


def shrink(px, size, out):
    f = size // out
    res = bytearray(out * out * 4)
    for y in range(out):
        for x in range(out):
            acc = [0, 0, 0, 0]
            for yy in range(f):
                for xx in range(f):
                    i = ((y * f + yy) * size + x * f + xx) * 4
                    a = px[i + 3]
                    acc[0] += px[i] * a
                    acc[1] += px[i + 1] * a
                    acc[2] += px[i + 2] * a
                    acc[3] += a
            j = (y * out + x) * 4
            if acc[3]:
                res[j:j + 4] = bytes((acc[0] // acc[3], acc[1] // acc[3], acc[2] // acc[3], acc[3] // (f * f)))
    return res


def png(px, size):
    raw = b"".join(b"\x00" + bytes(px[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    head = struct.pack("<HHH", 0, 1, len(images))
    offset, entries, data = 6 + 16 * len(images), b"", b""
    for size, blob in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return head + entries + data


def main():
    os.makedirs(OUT, exist_ok=True)
    big = render()
    images = [(256, png(big, 256))] + [(s, png(shrink(big, 256, s), s)) for s in (64, 48, 32, 16)]
    with open(os.path.join(OUT, "icon.png"), "wb") as f:
        f.write(images[0][1])
    with open(os.path.join(OUT, "icon.ico"), "wb") as f:
        f.write(ico(images))
    print("만들었습니다:", os.path.join(OUT, "icon.ico"))


if __name__ == "__main__":
    main()
