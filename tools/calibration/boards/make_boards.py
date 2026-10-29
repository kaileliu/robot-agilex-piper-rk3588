#!/usr/bin/env python3
"""生成可打印的棋盘格标定板（PDF，1:1）。

为什么自己做板子：标定只要求"角点数量对、格子够大够清晰"。格子的实际边长对解 K/D 没有影响
（尺子量不量都一样，工具里也不用填），所以没必要去找图、更不用纠结打印缩放——这里按纸张
印几种现成的，打印时选「实际大小 / 100%」即可。

用法：python3 make_boards.py     # 在本目录生成 3 个 PDF
"""
from pathlib import Path

MM = 72 / 25.4          # 1 mm = 2.8346 pt
# 走横向：棋盘是横着的，竖版纸上下会白扔一大半；横过来同样的纸格子还能放大一截
PAPERS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}

# (文件名, 纸, 横向格数, 纵向格数, 格子边长 mm)
# 内角点数 = 格数 - 1：12×8 格 → 11×7 个内角点，就是这个数填进标定页
BOARDS = [
    ("chessboard-A4-11x7.pdf", "A4", 12, 8, 20.0),
    ("chessboard-A3-11x7.pdf", "A3", 12, 8, 30.0),
    ("chessboard-A3-15x10.pdf", "A3", 16, 11, 22.0),
]


def content(cols: int, rows: int, sq_mm: float, page_mm: tuple[float, float]) -> str:
    sq = sq_mm * MM
    w_pt, h_pt = page_mm[0] * MM, page_mm[1] * MM
    bw, bh = cols * sq, rows * sq
    x0 = (w_pt - bw) / 2
    y0 = (h_pt - bh) / 2 + 30
    cap = (f"chessboard {cols - 1}x{rows - 1} inner corners "
           f"({cols}x{rows} squares), square {sq_mm:g} mm, landscape")
    parts = ["0 0 0 rg"]
    for row in range(rows):
        for col in range(cols):
            if (row + col) % 2 == 0:      # 左上角先画黑格，和常见的棋盘一致
                x = x0 + col * sq
                y = y0 + (rows - 1 - row) * sq
                parts.append(f"{x:.2f} {y:.2f} {sq:.2f} {sq:.2f} re f")
    # 100 mm 校验条：打印完拿尺子量一下，不是 100 mm 就说明打印机缩放了
    bar = 100 * MM
    bx, by = (w_pt - bar) / 2, y0 - 45
    parts += [
        "0 0 0 RG 0.8 w",
        f"{bx:.2f} {by:.2f} m {bx + bar:.2f} {by:.2f} l S",
        f"BT /F1 9 Tf 1 0 0 1 {bx:.2f} {by - 12:.2f} Tm (the bar above is 100 mm - if it is not, the printer scaled the page) Tj ET",
        f"BT /F1 9 Tf 1 0 0 1 {bx:.2f} {by + 5:.2f} Tm ({cap} - print at 100% / actual size) Tj ET",
    ]
    return "\n".join(parts)


def write_pdf(path: Path, body: str, page_mm: tuple[float, float]) -> None:
    w_pt, h_pt = page_mm[0] * MM, page_mm[1] * MM
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt:.2f} {h_pt:.2f}] "
         f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>").encode(),
        b"<< /Length " + str(len(body.encode())).encode() + b" >>\nstream\n"
        + body.encode() + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, paper, cols, rows, sq_mm in BOARDS:
        write_pdf(here / name, content(cols, rows, sq_mm, PAPERS[paper]), PAPERS[paper])
        print(f"{name}: {paper}, {cols}x{rows} 格，格边长 {sq_mm:g} mm，"
              f"内角点 {cols - 1}x{rows - 1}")


if __name__ == "__main__":
    main()
