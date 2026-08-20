#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBIMU-9DOF (E2BOX) 9축 실시간 모니터  -  Ubuntu / Raspberry Pi

Roll / Pitch / Yaw + Gyro X,Y,Z + Accel X,Y,Z  9개 값을
한 화면에서 계속 갱신하며 보여준다.

사용법
    python3 ebimu_live.py                          # 포트/보레이트 입력 프롬프트
    python3 ebimu_live.py -p /dev/ttyUSB0          # 바로 실행
    python3 ebimu_live.py -p /dev/serial0 -b 115200
    python3 ebimu_live.py -p /dev/ttyUSB0 --csv    # 한 줄씩 흘려보내기(로그용)
    python3 ebimu_live.py -p /dev/ttyUSB0 --raw    # 센서 원문 그대로

값 이름이 Val 0, Val 1 … 로 나올 때
    패킷에는 숫자만 오고 어떤 항목이 켜져 있는지는 적혀 있지 않다.
    필드 수로 조합을 추정하지만, 같은 개수가 나오는 조합이 여럿이면 틀릴 수 있다.
    이럴 때는 켜 둔 항목을 직접 알려준다.

        python3 ebimu_live.py --list-blocks                       # 항목 이름 보기
        python3 ebimu_live.py -p /dev/ttyUSB0 \
            --layout euler,gyro,accel,temp                        # 직접 지정

    예) <sog1> <soa1> <sot1> 을 켜 두었다면 자세까지 10개이므로
        --layout euler,gyro,accel,temp

    가장 확실한 방법은 센서에 물어보는 것이다. Ebimu_cmd.py --detect 가
    <cfg> 로 현재 설정을 읽어 --layout 문자열을 만들어 준다.
    명령 설명은 COMMANDS.md 참고.

종료: Ctrl-C
"""

import argparse
import os
import shutil
import sys
import unicodedata
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial 이 없습니다.  pip install pyserial  (또는 sudo apt install python3-serial)")


# ────────────────────────────────────────────────────────────────
# 출력 항목(블록) 정의   (E2BOX EBIMU-9DOFV5 매뉴얼 rev3 기준)
#
# 패킷은 sof, sog, soa, som, sod, sot, sots 순서로 이어 붙는다.
# 자세(sof)는 끌 수 없고 Euler(3개)/Quaternion(4개) 중 하나로 항상 나온다.
# 어떤 항목이 켜져 있는지는 패킷만 봐서는 알 수 없다.
#   - Ebimu_cmd.py --detect  가 <cfg> 로 센서에 직접 물어본다 (권장)
#   - 그게 안 되면 필드 수로 추정하고, --layout 으로 직접 지정한다
# 자세한 명령 설명은 COMMANDS.md 참고.
# ────────────────────────────────────────────────────────────────
BLOCKS = {
    "euler": ("오일러각",   [("Roll", "deg"), ("Pitch", "deg"), ("Yaw", "deg")]),
    "quat":  ("쿼터니언",   [("Quat Z", ""), ("Quat Y", ""),
                            ("Quat X", ""), ("Quat W", "")]),
    "gyro":  ("각속도",     [("Gyro X", "deg/s"), ("Gyro Y", "deg/s"),
                            ("Gyro Z", "deg/s")]),
    "accel": ("가속도",     [("Accel X", "g"), ("Accel Y", "g"), ("Accel Z", "g")]),
    "vel":   ("속도",       [("Vel X", "m/s"), ("Vel Y", "m/s"), ("Vel Z", "m/s")]),
    "mag":   ("지자기",     [("Mag X", "uT"), ("Mag Y", "uT"), ("Mag Z", "uT")]),
    "dist":  ("거리",       [("Dist X", "m"), ("Dist Y", "m"), ("Dist Z", "m")]),
    "temp":  ("온도",       [("Temp", "C")]),
    "time":  ("타임스탬프", [("Time", "ms")]),
}

# 자세(sof)는 끌 수 없어 항상 맨 앞에 온다. 나머지는 이 순서대로 이어 붙는다.
# accel 과 vel 은 같은 명령(soa)을 쓰므로 동시에 나올 수 없다.
_OPTIONAL = ["mag", "dist", "temp", "time"]


def _subsets(items):
    """순서를 지키는 부분집합 전부."""
    out = [[]]
    for it in items:
        out = out + [s + [it] for s in out]
    return out


def _candidates():
    """필드 수가 같은 조합이 여러 개일 수 있어, 흔한 것부터 순서대로 돌려준다."""
    rest = _subsets(_OPTIONAL)
    # 보통 앞에서부터 차례로 켜므로 연속된 조합을 먼저 본다.
    rest.sort(key=lambda sub: (0 if _OPTIONAL[:len(sub)] == sub else 1,
                               len(sub),
                               [_OPTIONAL.index(x) for x in sub]))
    out = []
    for head in (["euler"], ["quat"]):
        for gyro in (["gyro"], []):
            for soa in (["accel"], ["vel"], []):
                for tail in rest:
                    out.append(head + gyro + soa + tail)
    return out


def block_labels(names):
    return [lab for n in names for lab in BLOCKS[n][1]]


def matching_blocks(n):
    """필드 수가 n 인 조합 전부 (가능성이 높은 순)."""
    return [c for c in _candidates() if c and len(block_labels(c)) == n]


def guess_blocks(n):
    """필드 수 n 으로 켜져 있는 항목 조합을 추정. 못 찾으면 None."""
    m = matching_blocks(n)
    return m[0] if m else None


def parse_layout(text):
    """'euler,gyro,accel' → ['euler','gyro','accel'] (이름 검증 포함)"""
    names = [t.strip().lower() for t in text.split(",") if t.strip()]
    bad = [n for n in names if n not in BLOCKS]
    if bad:
        sys.exit(f"[!] 모르는 항목: {', '.join(bad)}\n"
                 f"    쓸 수 있는 이름: {', '.join(BLOCKS)}")
    return names


LAYOUT_FILE = "ebimu_layout.txt"


def load_layout_file():
    """Ebimu_cmd.py --detect --save-layout 가 저장해 둔 항목 목록."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LAYOUT_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    return parse_layout(text) if text else None


def list_blocks():
    print("\n  --layout 에 쓸 수 있는 항목 (패킷에 나오는 순서)\n")
    for key, (desc, labs) in BLOCKS.items():
        print(f"    {key:<7s} {pad(desc, 12)}{len(labs)}개   "
              f"{', '.join(l[0] for l in labs)}")
    print("\n  ※ 자세(euler/quat)는 끌 수 없어 항상 맨 앞에 옵니다.")
    print("     가속도와 속도는 같은 명령(soa)을 써서 동시에 나올 수 없습니다.")
    print("\n  예)  --layout euler,gyro,accel        9개")
    print("       --layout euler,gyro,accel,temp  10개   (<sog1> <soa1> <sot1>)")
    print("       --layout quat,gyro,accel        10개")
    print("       --layout euler,gyro,accel,mag   12개\n")


def resolve(n, layout):
    """필드 수 n 에 대한 (라벨 목록, 설명, 보조 안내)."""
    if layout:
        names, source = layout
        labs = block_labels(names)
        note = f"{source} " + "+".join(names)
        hint = ""
        if len(labs) != n:
            hint = f"[!] 지정 {len(labs)}개 ≠ 수신 {n}개 — 센서 설정을 확인하세요"
        labs += [(f"Val {i}", "") for i in range(len(labs), n)]
        return labs[:n], note, hint

    m = matching_blocks(n)
    if m:
        hint = ""
        if len(m) > 1:
            other = ", ".join("+".join(c) for c in m[1:3])
            hint = f"같은 {n}개 조합: {other} … --layout 으로 확정하세요"
        return block_labels(m[0]), "추정 " + "+".join(m[0]), hint
    return ([(f"Val {i}", "") for i in range(n)], "항목을 알 수 없음",
            "--layout 으로 지정하세요 (--list-blocks 로 항목 확인)")


# ────────────────────────────────────────────────────────────────
# 공유 상태
# ────────────────────────────────────────────────────────────────
class Shared:
    def __init__(self):
        self.values = []      # 최신 float 리스트
        self.count = 0        # 누적 패킷 수
        self.bad = 0          # 파싱 실패 수
        self.running = True
        self.error = None
        self.hz = 0.0


def reader(ser, sh, raw_mode):
    """백그라운드에서 계속 읽어 최신 값만 덮어쓴다."""
    while sh.running:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
        except Exception as e:
            sh.error = str(e)
            sh.running = False
            return

        if not line or not line.startswith("*"):
            continue

        if raw_mode:
            sh.count += 1
            print(line, flush=True)
            continue

        fields = line[1:].split(",")
        vals = []
        for f in fields:
            f = f.strip()
            if not f:
                continue
            try:
                vals.append(float(f))
            except ValueError:
                # 마지막 필드가 16진수 체크섬인 경우 등 -> 그냥 버린다
                pass

        if vals:
            sh.values = vals
            sh.count += 1
        else:
            sh.bad += 1


# ────────────────────────────────────────────────────────────────
# 화면 출력
# ────────────────────────────────────────────────────────────────
BAR_W = 24


def bar(v, lo, hi):
    """값을 막대그래프 한 줄로."""
    if hi <= lo:
        return " " * BAR_W
    p = (v - lo) / (hi - lo)
    p = 0.0 if p < 0 else (1.0 if p > 1 else p)
    mid = BAR_W // 2
    pos = int(p * (BAR_W - 1))
    cells = ["·"] * BAR_W
    cells[mid] = "|"
    cells[pos] = "█"
    return "".join(cells)


# 막대그래프 눈금 범위. None 이면 막대를 그리지 않는다.
RANGES = {
    "deg":   (-180, 180),
    "deg/s": (-300, 300),
    "g":     (-2, 2),
    "uT":    (-100, 100),
    "m":     (-5, 5),
    "m/s":   (-5, 5),
    "C":     (0, 80),
    "ms":    None,
    "":      (-1, 1),
}


def dwidth(text):
    """터미널에서 실제로 차지하는 칸 수. 한글/한자는 두 칸이다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text, n):
    return text + " " * max(0, n - dwidth(text))


def clip(line, width):
    """터미널 폭을 넘으면 자른다. 줄바꿈이 생기면 화면 갱신이 어긋난다."""
    out, w = "", 0
    for ch in line:
        cw = dwidth(ch)
        if w + cw >= width:
            break
        out, w = out + ch, w + cw
    return pad(out, width - 1)


def render(sh, port, baud, layout):
    vals = list(sh.values)
    labs, note, hint = resolve(len(vals), layout)
    width = max(shutil.get_terminal_size((80, 24)).columns, 40)

    out = []
    out.append(f"  EBIMU  {port} @ {baud}")
    out.append(f"  packets {sh.count:<10d} drops {sh.bad:<6d} {sh.hz:6.1f} Hz")
    out.append(f"  필드 {len(vals)}개 · {note}")
    if hint:
        out.append(f"  ※ {hint}")
    out.append("  " + "─" * 56)
    for (name, unit), v in zip(labs, vals):
        rng = RANGES.get(unit, (-1, 1))
        graph = bar(v, *rng) if rng else " " * BAR_W
        out.append(f"  {name:<9s} {v:>10.3f} {unit:<6s} {graph}")
    out.append("  " + "─" * 56)
    out.append("  Ctrl-C 로 종료")
    return [clip(l, width) for l in out]


def main():
    ap = argparse.ArgumentParser(description="EBIMU-9DOF 실시간 모니터")
    ap.add_argument("-p", "--port", help="예: /dev/ttyUSB0, /dev/serial0, COM3")
    ap.add_argument("-b", "--baud", type=int, help="기본 115200")
    ap.add_argument("--hz", type=float, default=20.0, help="화면 갱신 주기(Hz), 기본 20")
    ap.add_argument("--csv", action="store_true", help="갱신 대신 한 줄씩 계속 출력")
    ap.add_argument("--raw", action="store_true", help="센서 원문 그대로 출력")
    ap.add_argument("--layout", help="출력 항목을 직접 지정, 예: euler,gyro,accel")
    ap.add_argument("--list-blocks", action="store_true",
                    help="--layout 에 쓸 수 있는 항목 목록 (포트 없이 실행 가능)")
    args = ap.parse_args()

    if args.list_blocks:
        list_blocks()
        return

    if args.layout:
        layout = (parse_layout(args.layout), "지정")
    else:
        from_file = load_layout_file()
        layout = (from_file, f"{LAYOUT_FILE}") if from_file else None

    if args.port:
        port = args.port
        baud = args.baud or 115200
    else:
        port = input("EBIMU Port (예: /dev/ttyUSB0): ").strip()
        baud = args.baud or int(input("Baudrate [115200]: ").strip() or 115200)

    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.2)
    except Exception as e:
        sys.exit(f"[!] 포트를 열 수 없습니다: {e}\n"
                 f"    - 포트 이름 확인:  ls /dev/ttyUSB* /dev/ttyACM* /dev/serial*\n"
                 f"    - 권한:           sudo usermod -aG dialout $USER  (재로그인)\n")

    sh = Shared()
    th = threading.Thread(target=reader, args=(ser, sh, args.raw), daemon=True)
    th.start()

    period = 1.0 / max(args.hz, 1.0)
    last_t, last_c = time.time(), 0
    lines_printed = 0
    csv_header = False

    try:
        while sh.running:
            time.sleep(period)

            now = time.time()
            dt = now - last_t
            if dt >= 0.5:
                sh.hz = (sh.count - last_c) / dt
                last_t, last_c = now, sh.count

            if args.raw:
                continue

            if not sh.values:
                print("\r데이터 대기 중...", end="", flush=True)
                continue

            if args.csv:
                if not csv_header:
                    labs, _n, _h = resolve(len(sh.values), layout)
                    print(",".join(n.replace(" ", "") for n, _u in labs), flush=True)
                    csv_header = True
                print(",".join(f"{v:.3f}" for v in sh.values), flush=True)
                continue

            block = render(sh, port, baud, layout)
            if lines_printed:
                sys.stdout.write(f"\033[{lines_printed}A")   # 커서 위로
            sys.stdout.write("\n".join(block) + "\n")
            sys.stdout.flush()
            lines_printed = len(block)

    except KeyboardInterrupt:
        pass
    finally:
        sh.running = False
        time.sleep(0.05)
        ser.close()
        print("\n종료했습니다.")
        if sh.error:
            print(f"[!] 시리얼 오류: {sh.error}")


if __name__ == "__main__":
    main()