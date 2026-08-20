#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBIMU-9DOF (E2BOX) 설정 도구  -  Ubuntu / Raspberry Pi

윈도우 뷰어 없이 센서 출력 항목(오일러/쿼터니언/자이로/가속도/지자기 등)을
켜고 끄기 위한 도구.

모드
  1) 상태 확인      python3 ebimu_cmd.py -p /dev/ttyUSB0 --show
  2) 자동 탐색      python3 ebimu_cmd.py -p /dev/ttyUSB0 --probe
  3) 명령 직접 전송  python3 ebimu_cmd.py -p /dev/ttyUSB0 -c "<sog1>" -c "<soa1>"
  4) 대화형 터미널   python3 ebimu_cmd.py -p /dev/ttyUSB0 --shell
  5) 상황별 프리셋   python3 ebimu_cmd.py --list-presets
                    python3 ebimu_cmd.py -p /dev/ttyUSB0 --preset vibration

확인된 명령어 (출처: E2BOX "EBIMU-9DOFV5 상황별 설정" 문서)
  <lf>          초기 설정 복원
  <cg>          자이로센서 캘리브레이션
  <caf>         가속도센서 캘리브레이션
  <cmf>         지자기센서 캘리브레이션
  <sem0/1>      지자기센서 OFF / ON
  <sod2>        global 거리데이터 출력
  <avca_e0/1>   가속도 AVC 비활성/활성
  <avcg_e0/1>   자이로 AVC 비활성/활성
  <raa_l0.05>   RAA Level
  <raa_t0>      RAA Timeout (0 = 비활성)
  <rha_t>       RHA Timeout
  <lpfg> <lpfa> LPF 설정
  <posf_sl0.2>  걸음 추적 보폭 파라미터

  → 출력 항목(자이로/가속도 등)을 켜는 <so_> 계열 명령은 위 문서에 없다.
    --probe 가 후보를 보내보고 필드 수 변화로 맞는 것을 찾아낸다.

안전장치
  - --probe 는 "출력 항목 on" 계열 명령만 시도한다.
    통신속도(baudrate) 변경, 공장초기화, 캘리브레이션 계열은 절대 보내지 않는다.
  - 설정을 플래시에 저장하는 명령은 보내지 않으므로,
    잘못돼도 센서 전원을 껐다 켜면 원래대로 돌아온다.
    (마음에 들면 매뉴얼의 저장 명령을 --cmd 로 직접 보내면 된다)
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    serial = None   # --list-presets 는 pyserial 없이도 동작


# ── 시도해볼 '출력 항목 켜기' 후보 ────────────────────────────────
# E2BOX 명령 형식은 <so + 항목문자 + 값> 이다.
# (공식 문서에 <sod2> = global 거리 출력 설정 예시가 나와 있다)
CANDIDATES = [
    ("<soe1>", "Euler  (roll/pitch/yaw)"),
    ("<sog1>", "Gyro   (각속도)"),
    ("<soa1>", "Accel  (가속도)"),
    ("<som1>", "Magnet (지자기)"),
    ("<soq1>", "Quaternion"),
    ("<sot1>", "Temperature"),
    ("<sod1>", "Distance"),
    ("<sov1>", "Velocity"),
    ("<sots1>", "Timestamp"),
]

# 확인 없이 보내면 안 되는 명령 (E2BOX 상황별 설정 문서 기준)
#   <lf>  초기 설정 복원      <cg>   자이로 캘리브레이션
#   <cmf> 지자기 캘리브레이션  <caf>  가속도 캘리브레이션
#   <sb>  통신속도 변경
DANGEROUS = ("<lf", "<sb", "<fd", "<rst")
CALIBRATION = ("<cg", "<cmf", "<caf", "<cm", "<ca")
FORBIDDEN = DANGEROUS + CALIBRATION


# ── E2BOX "EBIMU-9DOFV5 상황별 설정" 문서의 프리셋 ──────────────
# cal=True 인 항목은 센서를 물리적으로 움직여야 하는 캘리브레이션이다.
PRESETS = {
    "rollpitch": ("Roll/Pitch축만 사용", [
        ("<sem0>", "지자기센서 OFF", False),
    ]),
    "yaw-relative": ("외부자기장 영향 없는 Yaw축 사용 (yaw는 상대각, drift 발생)", [
        ("<cg>",   "자이로센서 캘리브레이션", True),
        ("<sem0>", "지자기센서 OFF", False),
    ]),
    "yaw-drift": ("Yaw축 드리프트/오차 발생", [
        ("<cmf>",  "지자기센서 캘리브레이션", True),
        ("<rha_t>", "RHA Timeout 변경 (선택)", False),
    ]),
    "rollpitch-drift": ("Roll/Pitch축 오차가 크거나 미세 드리프트", [
        ("<cg>",  "자이로센서 캘리브레이션", True),
        ("<caf>", "가속도센서 캘리브레이션", True),
    ]),
    "distance": ("거리데이터 오차 줄이기", [
        ("<cg>",        "자이로센서 캘리브레이션", True),
        ("<caf>",       "가속도센서 캘리브레이션", True),
        ("<cmf>",       "지자기센서 캘리브레이션", True),
        ("<raa_l0.05>", "RAA Level 0.05로", False),
        ("<avca_e0>",   "가속도 AVC 비활성화", False),
        ("<avcg_e0>",   "자이로 AVC 비활성화", False),
        ("<sod2>",      "global 거리데이터 출력", False),
    ]),
    "vibration": ("진동이 심한 환경", [
        ("<cg>",      "자이로센서 캘리브레이션", True),
        ("<avca_e1>", "가속도 AVC 활성화", False),
        ("<avcg_e1>", "자이로 AVC 활성화", False),
        ("<raa_t0>",  "RAA 비활성화", False),
    ]),
    "dynamic": ("장시간 가감속 / 원운동", [
        ("<raa_t>", "RAA Timeout 변경", False),
    ]),
    "moving": ("센서 위치가 크게 변하며 사용", [
        ("<rha_t>", "RHA Timeout 변경", False),
    ]),
    "walk": ("걸음 추적 (센서를 발등에 부착)", [
        ("<cg>",         "자이로센서 캘리브레이션", True),
        ("<caf>",        "가속도센서 캘리브레이션", True),
        ("<cmf>",        "지자기센서 캘리브레이션", True),
        ("<avca_e0>",    "가속도 AVC 비활성화", False),
        ("<avcg_e0>",    "자이로 AVC 비활성화", False),
        ("<posf_sl0.2>", "posf_sl 0.2 (0.05~0.3, 빠를수록 크게)", False),
        ("<sod2>",       "global 거리데이터 출력", False),
    ]),
}


def drain(ser, sec=0.4):
    """지정 시간 동안 들어오는 것 전부 읽어서 문자열로."""
    buf = b""
    t0 = time.time()
    while time.time() - t0 < sec:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.01)
    return buf.decode("utf-8", errors="ignore")


def sample_fields(ser, sec=0.7):
    """지금 출력되는 패킷의 필드 개수와 예시 한 줄을 돌려준다."""
    txt = drain(ser, sec)
    lines = [l.strip() for l in txt.splitlines() if l.strip().startswith("*")]
    if not lines:
        return 0, "", len(txt)
    # 가장 흔한 필드 수를 채택 (깨진 첫 줄 방지)
    counts = {}
    for l in lines:
        c = len(l[1:].split(","))
        counts[c] = counts.get(c, 0) + 1
    best = max(counts, key=counts.get)
    example = next(l for l in lines if len(l[1:].split(",")) == best)
    return best, example, len(lines)


def numeric_fields(line):
    """예시 줄에서 실제 숫자로 해석되는 필드 수 (체크섬 hex 제외)."""
    n = 0
    for f in line[1:].split(","):
        try:
            float(f.strip())
            n += 1
        except ValueError:
            pass
    return n


def clean_resp(txt, limit=60):
    """응답에서 데이터 패킷('*'로 시작)을 걸러내고 센서의 회신만 남긴다."""
    keep = [l.strip() for l in txt.splitlines()
            if l.strip() and not l.strip().startswith("*")]
    r = " ".join(keep)
    return (r[:limit] + "…") if len(r) > limit else r


def send(ser, cmd, wait=0.4):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    ser.flush()
    return clean_resp(drain(ser, wait))


def show(ser):
    n, ex, cnt = sample_fields(ser, 1.0)
    print(f"\n  수신 패킷 수 : {cnt} (1초)")
    if n == 0:
        print("  [!] '*' 로 시작하는 패킷이 안 옵니다. 보레이트/배선/전원을 확인하세요.")
        return 0
    num = numeric_fields(ex)
    print(f"  필드 수      : {n}  (숫자 {num}개" + (" + 체크섬 1개)" if num < n else ")"))
    print(f"  예시         : {ex}")
    print(f"\n  → 숫자 {num}개 = ", end="")
    print({3: "오일러각만",
           4: "쿼터니언만",
           6: "오일러 + 자이로 (또는 자이로+가속도)",
           9: "오일러 + 자이로 + 가속도  ← 목표",
           10: "쿼터니언 + 자이로 + 가속도"}.get(num, "사용자 설정"))
    return num


def probe(ser):
    print("\n  [probe] 출력 항목을 하나씩 켜보며 반응을 확인합니다.")
    print("          (통신속도/초기화/캘리브레이션 명령은 보내지 않습니다)\n")

    base, ex, _ = sample_fields(ser, 0.7)
    base_num = numeric_fields(ex) if ex else 0
    print(f"  시작 상태: 숫자 {base_num}개  {ex}\n")

    working = []
    cur = base_num
    for cmd, desc in CANDIDATES:
        resp = send(ser, cmd, 0.5)
        n, ex2, _ = sample_fields(ser, 0.7)
        num = numeric_fields(ex2) if ex2 else 0
        tag = ""
        if num > cur:
            tag = f"  ✔ 필드 {cur} → {num}"
            working.append((cmd, desc, num))
            cur = num
        elif num == 0:
            tag = "  ✖ 출력 멈춤 (명령 미지원 가능)"
        else:
            tag = f"  · 변화 없음 (필드 {num})"
        print(f"  {cmd:<9s} {desc:<24s}{tag}   응답:{resp!r}")

    print("\n  ── 결과 ──")
    if working:
        print("  효과가 있었던 명령:")
        for cmd, desc, num in working:
            print(f"    {cmd}  {desc}  → 필드 {num}개")
    else:
        print("  필드 수가 늘어난 명령이 없습니다.")
        print("  펌웨어 버전에 따라 명령 문자가 다를 수 있습니다.")
        print("  E2BOX 매뉴얼(EBIMU-9DOFV5_상황별_설정.pdf)의 명령표를 확인하거나")
        print("  --shell 모드로 직접 명령을 입력해 보세요.")

    print("\n  최종 상태:")
    show(ser)
    print("\n  ※ 이 설정은 아직 저장되지 않았습니다. 전원을 껐다 켜면 원래대로 돌아갑니다.")
    print("     마음에 들면 매뉴얼의 저장 명령을 -c 로 보내 확정하세요.")


def shell(ser):
    print("\n  [shell] 명령을 입력하세요. 예: <sog1>   (빈 줄 = 현재 출력 보기, q = 종료)")
    print("          위험 명령(<sb..>, <fd>, <rst> 등)은 확인을 한 번 더 받습니다.\n")
    while True:
        try:
            cmd = input("  cmd> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd.lower() in ("q", "quit", "exit"):
            break
        if not cmd:
            show(ser)
            continue
        if not (cmd.startswith("<") and cmd.endswith(">")):
            print("  형식은 <...> 입니다.")
            continue
        if cmd.lower().startswith(FORBIDDEN):
            kind = "초기화/통신설정" if cmd.lower().startswith(DANGEROUS) else "캘리브레이션"
            ok = input(f"  [!] {cmd} 는 {kind} 명령입니다. 정말 보낼까요? (yes): ")
            if ok.strip().lower() != "yes":
                continue
        resp = send(ser, cmd, 0.5)
        print(f"  응답: {resp!r}")
        show(ser)


def list_presets():
    print("\n  사용 가능한 프리셋 (출처: E2BOX 'EBIMU-9DOFV5 상황별 설정')\n")
    for key, (desc, cmds) in PRESETS.items():
        print(f"  {key:<16s} {desc}")
        for c, d, cal in cmds:
            mark = " [캘리브레이션]" if cal else ""
            print(f"                   {c:<14s} {d}{mark}")
        print()
    print("  ※ [캘리브레이션] 명령은 센서를 규정된 방법으로 움직여야 합니다.")
    print("     기본적으로 건너뛰며, --include-cal 을 붙여야 전송합니다.\n")


def apply_preset(ser, name, include_cal):
    if name not in PRESETS:
        print(f"  [!] '{name}' 프리셋이 없습니다.  --list-presets 로 확인하세요.")
        return
    desc, cmds = PRESETS[name]
    todo = [c for c in cmds if include_cal or not c[2]]
    skipped = [c for c in cmds if not include_cal and c[2]]

    print(f"\n  프리셋: {name}  —  {desc}\n")
    print("  보낼 명령:")
    for c, d, cal in todo:
        print(f"    {c:<14s} {d}" + ("  [캘리브레이션]" if cal else ""))
    if skipped:
        print("\n  건너뛸 캘리브레이션 (센서를 직접 움직여야 함, --include-cal 로 포함):")
        for c, d, _ in skipped:
            print(f"    {c:<14s} {d}")

    if input("\n  진행할까요? (yes): ").strip().lower() != "yes":
        print("  취소했습니다.")
        return

    for c, d, _ in todo:
        print(f"    send {c:<14s} → 응답:{send(ser, c, 0.6)!r}")
    print("\n  ※ 설정 저장 명령은 보내지 않았습니다. 전원을 껐다 켜면 원래대로 돌아갑니다.")
    print("     되돌리려면 초기화 명령 <lf> 를 --shell 에서 보내세요.")
    show(ser)


def main():
    ap = argparse.ArgumentParser(description="EBIMU 설정 도구")
    ap.add_argument("-p", "--port")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-c", "--cmd", action="append", default=[], help="보낼 명령 (여러 번 가능)")
    ap.add_argument("--show", action="store_true", help="현재 출력 상태만 확인")
    ap.add_argument("--probe", action="store_true", help="출력 항목 켜기 자동 탐색")
    ap.add_argument("--shell", action="store_true", help="대화형 명령 입력")
    ap.add_argument("--preset", help="상황별 설정 프리셋 적용")
    ap.add_argument("--list-presets", action="store_true", help="프리셋 목록 보기")
    ap.add_argument("--include-cal", action="store_true",
                    help="프리셋의 캘리브레이션 명령까지 전송 (센서를 직접 움직여야 함)")
    args = ap.parse_args()

    if args.list_presets:
        list_presets()
        return
    if serial is None:
        sys.exit("pyserial 이 없습니다.  pip install pyserial")
    if not args.port:
        ap.error("-p/--port 가 필요합니다")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except Exception as e:
        sys.exit(f"[!] 포트를 열 수 없습니다: {e}")

    time.sleep(0.3)
    try:
        for c in args.cmd:
            print(f"  send {c}  →  응답:{send(ser, c, 0.5)!r}")
        if args.cmd:
            show(ser)
        if args.preset:
            apply_preset(ser, args.preset, args.include_cal)
        elif args.probe:
            probe(ser)
        elif args.shell:
            shell(ser)
        elif args.show or not args.cmd:
            show(ser)
    finally:
        ser.close()


if __name__ == "__main__":
    main()