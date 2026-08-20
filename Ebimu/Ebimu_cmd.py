#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBIMU-9DOF (E2BOX) 설정 도구  -  Ubuntu / Raspberry Pi

윈도우 뷰어 없이 센서 출력 항목(오일러/쿼터니언/자이로/가속도/지자기 등)을
켜고 끄기 위한 도구.

모드
  0) 설정 확인      python3 ebimu_cmd.py -p /dev/ttyUSB0 --detect
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

  → 출력 항목을 켜고 끄는 <so_> 계열은 SPECIFICATION 문서 6-1 에 있다.
    전체 명령표는 COMMANDS.md 에 정리해 두었다.
      <sof1/2>  자세 포맷 Euler / Quaternion  (끌 수 없음)
      <sog0/1>  자이로            <som0/1>  지자기
      <soa0~5>  가속도 1~3 / 속도 4~5 (동시 불가)
      <sod0/1/2> 거리 끄기 / Local / Global
      <sot0/1>  온도              <sots0/1> 타임스탬프
      <cfg>     현재 설정 전부 출력  ← --detect 가 쓰는 명령

안전장치
  - --probe 는 "출력 항목 on" 계열 명령만 시도한다.
    통신속도(baudrate) 변경, 공장초기화, 캘리브레이션 계열은 절대 보내지 않는다.
  - [!] 설정은 내부 비휘발성 메모리에 자동 저장된다 (매뉴얼 6-1).
    전원을 껐다 켜도 되돌아오지 않는다. 되돌리려면 반대 명령을 직접 보내거나
    <lf> 로 공장초기화해야 한다. 바꾸기 전에 --detect 로 지금 설정을 적어 두자.
"""

import argparse
import os
import re
import sys
import time

try:
    import serial
except ImportError:
    serial = None   # --list-presets 는 pyserial 없이도 동작


# ── '출력 항목 켜기' 명령 (매뉴얼 6-1, COMMANDS.md 참고) ─────────
# 자세(sof)는 끌 수 없어 후보에 없다. 속도는 <soa4>/<soa5> 로,
# 가속도와 같은 자리를 쓰므로 동시에 켤 수 없다.
CANDIDATES = [
    ("<sog1>", "Gyro   (각속도)"),
    ("<soa1>", "Accel  (중력성분 포함 가속도)"),
    ("<som1>", "Magnet (지자기)"),
    ("<sod1>", "Distance (Local)"),
    ("<sot1>", "Temperature"),
    ("<sots1>", "Timestamp"),
]


# ── --detect 용 (COMMANDS.md 6-1 / 6-4-1 참고) ───────────────────
# <cfg> 가 현재 설정을 명령어별로 출력한다. 이것으로 확인하는 게 정확하고,
# 설정을 건드리지 않으므로 안전하다.
#
# 자세(sof)는 끌 수 없다. Euler(3개) / Quaternion(4개) 중 하나로 항상 나온다.
# 가속도와 속도는 soa 하나를 나눠 쓰므로 동시에 나올 수 없다.
CFG_TO_BLOCK = [
    ("sof",  {"1": "euler", "2": "quat"}),
    ("sog",  {"1": "gyro"}),
    ("soa",  {"1": "accel", "2": "accel", "3": "accel",
              "4": "vel", "5": "vel"}),
    ("som",  {"1": "mag"}),
    ("sod",  {"1": "dist", "2": "dist"}),
    ("sot",  {"1": "temp"}),
    ("sots", {"1": "time"}),
]

# soa/sod 는 값에 따라 뜻이 달라 되돌릴 때 주의가 필요하다.
CFG_DETAIL = {
    ("soa", "1"): "중력성분 포함 가속도",
    ("soa", "2"): "중력성분 제거 Local 가속도",
    ("soa", "3"): "중력성분 제거 Global 가속도",
    ("soa", "4"): "Local 속도",
    ("soa", "5"): "Global 속도",
    ("sod", "1"): "Local 거리",
    ("sod", "2"): "Global 거리",
    ("sof", "1"): "Euler Angles",
    ("sof", "2"): "Quaternion",
}

# <cfg> 응답에서 <명령값> 을 뽑는다. 예: <sog1> <raa_t10000>
CFG_TOKEN = re.compile(r"<([a-z_+]+?)([-0-9.]*)>")
# 괄호 없이 나오는 펌웨어를 위한 대비책
CFG_BARE = re.compile(r"\b(sof|sog|soa|som|sod|sots|sot)\s*[:=]?\s*([0-9])\b")

# --detect --toggle 용: 항목을 하나씩 꺼 보는 방법에서 쓸 on/off 명령.
# <cfg> 를 못 읽을 때만 쓴다. 설정이 자동 저장되므로 위험하다.
TOGGLE_CMDS = [
    ("gyro",  "<sog{}>",  "각속도"),
    ("accel", "<soa{}>",  "가속도/속도"),
    ("mag",   "<som{}>",  "지자기"),
    ("dist",  "<sod{}>",  "거리"),
    ("temp",  "<sot{}>",  "온도"),
    ("time",  "<sots{}>", "타임스탬프"),
]

# 패킷에 나오는 순서. --layout 은 이 순서대로 적어야 한다.
BLOCK_ORDER = ["euler", "quat", "gyro", "accel", "vel",
               "mag", "dist", "temp", "time"]

LAYOUT_FILE = "ebimu_layout.txt"

# 확인 없이 보내면 안 되는 명령 (E2BOX 상황별 설정 문서 기준)
#   <lf>  초기 설정 복원      <cg>   자이로 캘리브레이션
#   <cmf> 지자기 캘리브레이션  <caf>  가속도 캘리브레이션
#   <sb>  통신속도 변경
#   <lf>    공장초기화 (캘리브레이션 결과까지 사라짐)
#   <sb_>   통신속도 변경 (보레이트가 달라져 통신이 끊긴다)
#   <reset> 센서 reset      <stop>  출력 중지
#   <pons0> 전원 인가시 작동 안함으로 설정
DANGEROUS = ("<lf", "<sb", "<reset", "<stop", "<pons")
#   <cg> <caf> <cas> 자이로/가속도,  <cmf> <cnxy> <cnz> <+cnxy> <+cnz> 지자기
#   <cmo..> 자세 offset,  <cmco> offset 제거
CALIBRATION = ("<cg", "<ca", "<cm", "<cn", "<+cn")
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
    print("          (통신속도/초기화/캘리브레이션 명령은 보내지 않습니다)")
    print("  [!] 켜진 설정은 센서에 자동 저장됩니다."
          " 지금 설정을 알고 싶으면 --detect 를 먼저 쓰세요.\n")

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
    print("\n  ※ 바뀐 설정은 센서에 자동 저장되었습니다."
          " 전원을 껐다 켜도 그대로입니다.")
    print("     되돌리려면 <sog0> 처럼 직접 끄거나 <lf> 로 공장초기화하세요.")


def read_config(ser):
    """<cfg> 로 현재 설정을 받아온다.

    매뉴얼 6-4-1: cfg 는 '>' 를 입력할 때까지 센서를 정지 상태로 둔다.
    무슨 일이 있어도 '>' 를 보내야 하므로 finally 에서 처리한다.
    설정을 바꾸지 않으므로 이 명령 자체는 안전하다.
    """
    ser.reset_input_buffer()
    ser.write(b"<cfg>")
    ser.flush()
    try:
        return drain(ser, 2.0)
    finally:
        ser.write(b">")
        ser.flush()
        drain(ser, 0.5)


def parse_config(txt):
    """<cfg> 응답에서 {명령이름: 값} 을 뽑는다."""
    found = dict(CFG_TOKEN.findall(txt))
    if not any(k in found for k, _ in CFG_TO_BLOCK):
        found.update(dict(CFG_BARE.findall(txt)))
    return found


def layout_from_config(cfg):
    """설정 표에서 켜져 있는 출력 항목 목록을 만든다."""
    layout, detail = [], []
    for key, mapping in CFG_TO_BLOCK:
        val = cfg.get(key)
        if val is None:
            if key == "sof":
                # 자세는 끌 수 없다. 값이 안 보이면 기본값 Euler 로 본다.
                layout.append("euler")
                detail.append(("sof", "?", "Euler Angles (기본값으로 가정)"))
            continue
        block = mapping.get(val)
        if block:
            layout.append(block)
            detail.append((key, val, CFG_DETAIL.get((key, val),
                                                    BLOCKS_DESC.get(block, ""))))
    return layout, detail


BLOCKS_DESC = {
    "euler": "오일러각", "quat": "쿼터니언", "gyro": "각속도",
    "accel": "가속도", "vel": "속도", "mag": "지자기",
    "dist": "거리", "temp": "온도", "time": "타임스탬프",
}

BLOCK_SIZE = {"euler": 3, "quat": 4, "gyro": 3, "accel": 3, "vel": 3,
              "mag": 3, "dist": 3, "temp": 1, "time": 1}


def save_layout(arg):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LAYOUT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(arg + "\n")
    print(f"\n  {LAYOUT_FILE} 에 저장했습니다.")
    print("  이제 Ebimu_live.py 는 --layout 없이 실행해도 이 값을 씁니다.")


def report(layout, base, ser, save):
    """찾은 항목을 실제 필드 수와 대조해 보고한다."""
    total = sum(BLOCK_SIZE[b] for b in layout)
    arg = ",".join(layout)

    print(f"\n  켜져 있는 항목: {arg}  (합계 {total}개)")
    if base and total != base:
        print(f"\n  [!] 합계 {total}개 ≠ 실제 필드 {base}개.")
        print("      --raw 로 원문을 보고 --layout 을 손으로 맞춰야 합니다.")
    print("\n  실시간 모니터에 그대로 넣으세요:\n")
    print(f"    python3 Ebimu_live.py -p {ser.port} --layout {arg}")
    if save:
        save_layout(arg)


def detect(ser, save, toggle):
    """켜져 있는 출력 항목을 알아낸다.

    기본은 <cfg> 로 센서에 직접 물어보는 것이다. 설정을 바꾸지 않는다.
    --toggle 은 항목을 하나씩 꺼 보는 옛 방법으로, 설정이 자동 저장되므로
    되돌리지 못할 수 있다.
    """
    base_n, base_ex, _ = sample_fields(ser, 1.0)
    base = numeric_fields(base_ex) if base_ex else 0
    if base:
        print(f"\n  현재 숫자 필드 {base}개   {base_ex}")
    else:
        print("\n  [!] 패킷이 오지 않습니다. 보레이트/배선을 확인하세요.")

    if toggle:
        return detect_by_toggle(ser, save, base)

    print("\n  [detect] <cfg> 로 센서에 현재 설정을 물어봅니다."
          " (설정을 바꾸지 않습니다)\n")
    txt = read_config(ser)
    cfg = parse_config(txt)

    out_keys = [k for k, _ in CFG_TO_BLOCK if k in cfg]
    if not out_keys:
        print("  [!] <cfg> 응답에서 출력 설정을 찾지 못했습니다.")
        print("      받은 내용 앞부분:")
        for line in txt.splitlines()[:12]:
            if line.strip():
                print(f"        {line.strip()[:70]}")
        print("\n      펌웨어가 다른 형식으로 답할 수 있습니다."
              " 항목을 껐다 켜며 찾으려면:")
        print(f"        python3 Ebimu_cmd.py -p {ser.port} --detect --toggle")
        print("      (설정이 자동 저장되므로 되돌리지 못할 수 있습니다)")
        return

    layout, detail = layout_from_config(cfg)
    for key, val, desc in detail:
        print(f"    <{key}{val}>{'':<4s} {desc}")

    off = [k for k, _ in CFG_TO_BLOCK if cfg.get(k) == "0"]
    if off:
        print(f"\n  꺼져 있음: {', '.join(off)}")

    report(layout, base, ser, save)
    print("\n  ※ <cfg> 는 설정을 읽기만 합니다. 아무것도 바뀌지 않았습니다.")


def detect_by_toggle(ser, save, base):
    """항목을 하나씩 꺼 보고 필드 수 변화로 알아낸다. <cfg> 가 안 될 때만.

    주의: 출력 설정은 내부 비휘발성 메모리에 자동 저장된다(매뉴얼 6-1).
    중간에 끊기면 항목이 꺼진 채로 남고, 전원을 껐다 켜도 돌아오지 않는다.
    """
    if not base:
        print("  [!] 패킷이 없으면 필드 수 변화를 볼 수 없습니다.")
        return

    print("\n  [detect --toggle] 항목을 하나씩 껐다 켭니다.")
    print("  [!] 출력 설정은 센서에 자동 저장됩니다."
          " 중간에 끊기면 꺼진 채로 남습니다.")
    print("      (되돌리려면 <sog1> 처럼 직접 켜거나 <lf> 로 공장초기화)")
    if input("\n  진행할까요? (yes): ").strip().lower() != "yes":
        print("  취소했습니다.")
        return

    found, off, turned_off = {}, [], []
    try:
        for name, fmt, desc in TOGGLE_CMDS:
            send(ser, fmt.format(0), 0.5)
            turned_off.append((name, fmt))
            _, ex, _ = sample_fields(ser, 0.7)
            now = numeric_fields(ex) if ex else 0
            delta = base - now

            if delta > 0:
                found[name] = delta
                send(ser, fmt.format(1), 0.5)
                turned_off.pop()
                _, ex2, _ = sample_fields(ser, 0.7)
                back = numeric_fields(ex2) if ex2 else 0
                mark = "" if back == base else f"  [!] 복구 후 {back}개 (기대 {base}개)"
                print(f"  {name:<6s} {desc:<12s} 켜져 있음  필드 {delta}개{mark}")
            else:
                # 원래 꺼져 있었거나 펌웨어가 명령을 모르는 것이다. 켜지 않는다.
                turned_off.pop()
                off.append(name)
                print(f"  {name:<6s} {desc:<12s} 꺼져 있음 (또는 명령 미지원)")
    finally:
        for name, fmt in turned_off:
            send(ser, fmt.format(1), 0.5)

    # 자세는 끌 수 없다. 나머지를 뺀 만큼이 자세 필드 수다.
    rest = sum(found.values())
    head = base - rest
    layout = []
    if head == 4:
        layout.append("quat")
    elif head == 3:
        layout.append("euler")
    else:
        print(f"\n  [!] 자세 필드가 {head}개로 나왔습니다 (Euler 3 / Quaternion 4).")
    layout += [b for b in BLOCK_ORDER if b in found]

    if not found and head not in (3, 4):
        print("\n  켜져 있는 항목을 찾지 못했습니다. --probe 로 확인하세요.")
        return

    report(layout, base, ser, save)

    if "accel" in found:
        print("\n  ※ 가속도는 <soa1>(중력성분 포함) 로 되돌렸습니다."
              " 원래 <soa2>~<soa5> 였다면 그 값으로 다시 지정하세요.")
    if "dist" in found:
        print("  ※ 거리는 <sod1>(Local) 로 되돌렸습니다."
              " Global 이었다면 <sod2> 로 다시 지정하세요.")
    if off:
        print(f"  ※ 꺼져 있다고 나온 항목: {', '.join(off)}")
        print("     명령을 지원하지 않아 그렇게 보일 수도 있습니다.")


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
    print("\n  ※ 바뀐 설정은 센서에 자동 저장되었습니다."
          " 전원을 껐다 켜도 그대로입니다.")
    print("     되돌리려면 반대 명령을 직접 보내거나 <lf> 로 공장초기화하세요.")
    show(ser)


def main():
    ap = argparse.ArgumentParser(description="EBIMU 설정 도구")
    ap.add_argument("-p", "--port")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-c", "--cmd", action="append", default=[], help="보낼 명령 (여러 번 가능)")
    ap.add_argument("--show", action="store_true", help="현재 출력 상태만 확인")
    ap.add_argument("--probe", action="store_true", help="출력 항목 켜기 자동 탐색")
    ap.add_argument("--detect", action="store_true",
                    help="켜져 있는 출력 항목 확인 (--layout 문자열을 만들어 줌)")
    ap.add_argument("--save-layout", action="store_true",
                    help=f"--detect 결과를 {LAYOUT_FILE} 에 저장")
    ap.add_argument("--toggle", action="store_true",
                    help="--detect 를 <cfg> 대신 항목을 껐다 켜는 방식으로"
                         " (설정이 자동 저장되므로 위험)")
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
        elif args.detect:
            detect(ser, args.save_layout, args.toggle)
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