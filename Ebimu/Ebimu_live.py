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

센서 설정 요구사항
    오일러각 + 자이로 + 가속도 출력이 모두 켜져 있어야 9개가 나온다.
    (필드 수가 9가 아니어도 아래 표는 자동으로 맞춰서 표시한다)

종료: Ctrl-C
"""

import argparse
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial 이 없습니다.  pip install pyserial  (또는 sudo apt install python3-serial)")


# ────────────────────────────────────────────────────────────────
# 필드 수에 따른 라벨/단위 (센서 출력 설정에 따라 개수가 달라짐)
# ────────────────────────────────────────────────────────────────
LAYOUTS = {
    3:  [("Roll", "deg"), ("Pitch", "deg"), ("Yaw", "deg")],
    6:  [("Roll", "deg"), ("Pitch", "deg"), ("Yaw", "deg"),
         ("Gyro X", "deg/s"), ("Gyro Y", "deg/s"), ("Gyro Z", "deg/s")],
    9:  [("Roll", "deg"), ("Pitch", "deg"), ("Yaw", "deg"),
         ("Gyro X", "deg/s"), ("Gyro Y", "deg/s"), ("Gyro Z", "deg/s"),
         ("Accel X", "g"), ("Accel Y", "g"), ("Accel Z", "g")],
    10: [("Quat Z", ""), ("Quat Y", ""), ("Quat X", ""), ("Quat W", ""),
         ("Gyro X", "deg/s"), ("Gyro Y", "deg/s"), ("Gyro Z", "deg/s"),
         ("Accel X", "g"), ("Accel Y", "g"), ("Accel Z", "g")],
}


def labels_for(n):
    if n in LAYOUTS:
        return LAYOUTS[n]
    return [(f"Val {i}", "") for i in range(n)]


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


RANGES = {"deg": (-180, 180), "deg/s": (-300, 300), "g": (-2, 2), "": (-1, 1)}


def render(sh, port, baud):
    vals = list(sh.values)
    labs = labels_for(len(vals))

    out = []
    out.append(f"  EBIMU  {port} @ {baud}                     ")
    out.append(f"  packets {sh.count:<10d} drops {sh.bad:<6d} {sh.hz:6.1f} Hz     ")
    out.append("  " + "─" * 52)
    for (name, unit), v in zip(labs, vals):
        lo, hi = RANGES.get(unit, (-1, 1))
        out.append(f"  {name:<8s} {v:>10.3f} {unit:<6s} {bar(v, lo, hi)} ")
    out.append("  " + "─" * 52)
    out.append("  Ctrl-C 로 종료                              ")
    return out


def main():
    ap = argparse.ArgumentParser(description="EBIMU-9DOF 실시간 모니터")
    ap.add_argument("-p", "--port", help="예: /dev/ttyUSB0, /dev/serial0, COM3")
    ap.add_argument("-b", "--baud", type=int, help="기본 115200")
    ap.add_argument("--hz", type=float, default=20.0, help="화면 갱신 주기(Hz), 기본 20")
    ap.add_argument("--csv", action="store_true", help="갱신 대신 한 줄씩 계속 출력")
    ap.add_argument("--raw", action="store_true", help="센서 원문 그대로 출력")
    args = ap.parse_args()

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
                print(",".join(f"{v:.3f}" for v in sh.values), flush=True)
                continue

            block = render(sh, port, baud)
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