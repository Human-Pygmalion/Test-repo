# Ebimu

E2BOX EBIMU-9DOF 센서(Ubuntu / Raspberry Pi)의 설정과 실시간 모니터링 스크립트입니다.

## 구성

- `Ebimu_cmd.py`: 센서 출력 항목 설정 및 명령 전송 도구
- `Ebimu_live.py`: 9축 데이터 실시간 모니터 (터미널)
- `Ebimu_web.py`: 자세를 3D 상자로 보는 웹 뷰어 (브라우저)
- [`COMMANDS.md`](COMMANDS.md): 센서 명령어 정리 (E2BOX 매뉴얼 rev3 기준)

> **주의**: 센서 설정은 모두 내부 비휘발성 메모리에 **자동 저장**됩니다.
> 전원을 껐다 켜도 되돌아오지 않습니다. 되돌리려면 반대 명령을 직접 보내거나
> `<lf>` 로 공장초기화해야 합니다. 바꾸기 전에 `--detect` 로 지금 설정을 적어 두세요.
> 자세한 것은 [COMMANDS.md](COMMANDS.md) 를 보세요.

아래 명령어는 모두 이 `Ebimu/` 폴더 안에서 실행하는 기준입니다.

## 환경 준비

```bash
pip install -r ../requirements.txt        # pyserial>=3.5
```

포트 접근 권한이 없다면:

```bash
ls /dev/ttyUSB* /dev/ttyACM* /dev/serial*   # 포트 이름 확인
sudo usermod -aG dialout $USER              # 권한 부여 후 재로그인
```

기본 보레이트는 `115200` 입니다.

---

## Ebimu_cmd.py — 설정 도구

윈도우 뷰어 없이 센서 출력 항목(오일러/쿼터니언/자이로/가속도/지자기 등)을 켜고 끄는 도구입니다.

### 옵션

| 옵션 | 설명 |
| --- | --- |
| `-p, --port` | 시리얼 포트 (예: `/dev/ttyUSB0`) |
| `-b, --baud` | 보레이트 (기본 `115200`) |
| `-c, --cmd` | 명령 직접 전송 (여러 번 지정 가능) |
| `--show` | 현재 출력 상태만 확인 |
| `--probe` | 출력 항목 켜기 자동 탐색 |
| `--detect` | **켜져 있는 출력 항목 확인** — `<cfg>` 로 물어봄, 설정을 바꾸지 않음 |
| `--save-layout` | `--detect` 결과를 `ebimu_layout.txt` 에 저장 |
| `--toggle` | `--detect` 를 항목을 껐다 켜는 방식으로 (`<cfg>` 가 안 될 때만) |
| `--shell` | 대화형 명령 입력 |
| `--preset NAME` | 상황별 설정 프리셋 적용 |
| `--list-presets` | 프리셋 목록 보기 |
| `--include-cal` | 프리셋의 캘리브레이션 명령까지 전송 |

### 실행 명령어

```bash
# 1) 현재 출력 상태 확인
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --show

# 2) 출력 항목 자동 탐색 (통신속도/초기화/캘리브레이션 명령은 보내지 않음)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --probe

# 3) 명령 직접 전송 (자이로 + 가속도 출력 켜기)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 -c "<sog1>" -c "<soa1>"

# 4) 켜져 있는 출력 항목 확인 (Ebimu_live.py 의 --layout 값을 알아냄)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --detect

# 4-1) 결과를 파일로 저장 — 이후 Ebimu_live.py 는 --layout 없이 실행하면 됨
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --detect --save-layout

# 5) 대화형 터미널
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --shell

# 6) 프리셋 목록 보기 (포트 없이도 실행 가능)
python3 Ebimu_cmd.py --list-presets

# 7) 프리셋 적용 (진동이 심한 환경)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --preset vibration

# 8) 프리셋을 캘리브레이션까지 포함해 적용 (센서를 직접 움직여야 함)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --preset distance --include-cal

# 9) 보레이트를 지정해서 실행
python3 Ebimu_cmd.py -p /dev/serial0 -b 115200 --show
```

### 프리셋 목록

출처: E2BOX "EBIMU-9DOFV5 상황별 설정" 문서

| 프리셋 | 용도 |
| --- | --- |
| `rollpitch` | Roll/Pitch축만 사용 |
| `yaw-relative` | 외부자기장 영향 없는 Yaw축 사용 (상대각, drift 발생) |
| `yaw-drift` | Yaw축 드리프트/오차 발생 시 |
| `rollpitch-drift` | Roll/Pitch축 오차가 크거나 미세 드리프트 |
| `distance` | 거리데이터 오차 줄이기 |
| `vibration` | 진동이 심한 환경 |
| `dynamic` | 장시간 가감속 / 원운동 |
| `moving` | 센서 위치가 크게 변하며 사용 |
| `walk` | 걸음 추적 (센서를 발등에 부착) |

### 안전장치

- **설정은 센서에 자동 저장됩니다.** 전원을 껐다 켜도 되돌아오지 않습니다 ([COMMANDS.md](COMMANDS.md) 참고)
- `--detect` 는 `<cfg>` 로 읽기만 하므로 안전합니다. 바꾸기 전에 이걸로 지금 설정을 적어 두세요
- `--probe` 는 "출력 항목 on" 계열 명령만 시도합니다. 통신속도 변경, 공장초기화, 캘리브레이션은 보내지 않습니다
- 프리셋과 `--detect --toggle` 은 실행 전에 `yes` 확인을 받습니다. 프리셋의 캘리브레이션은 `--include-cal` 을 붙여야 전송합니다
- `--shell` 에서 위험 명령은 `yes` 확인을 한 번 더 받습니다
  - 초기화/통신설정: `<lf>` `<sb_>` `<reset>` `<stop>` `<pons_>`
  - 캘리브레이션: `<cg>` `<ca..>` `<cm..>` `<cn..>` `<+cn..>`

---

## Ebimu_live.py — 실시간 모니터

Roll / Pitch / Yaw + Gyro X,Y,Z + Accel X,Y,Z 9개 값을 한 화면에서 갱신하며 보여줍니다.
필드 수가 9가 아니어도 3 / 6 / 9 / 10개 레이아웃에 맞춰 자동으로 라벨을 붙입니다.

### 옵션

| 옵션 | 설명 |
| --- | --- |
| `-p, --port` | 시리얼 포트 (생략 시 프롬프트로 입력) |
| `-b, --baud` | 보레이트 (기본 `115200`) |
| `--hz` | 화면 갱신 주기(Hz), 기본 `20` |
| `--csv` | 갱신 대신 한 줄씩 계속 출력 (로그용, 첫 줄은 항목 이름) |
| `--raw` | 센서 원문 그대로 출력 |
| `--layout` | 켜 둔 출력 항목을 직접 지정, 예: `euler,gyro,accel,temp` (자동 저장됨) |
| `--list-blocks` | `--layout` 에 쓸 수 있는 항목 목록 (포트 없이 실행 가능) |
| `--no-save` | `--layout` 을 저장하지 않고 이번 실행에만 씀 |
| `--forget` | 저장된 `ebimu_layout.txt` 을 지우고 추정으로 되돌림 |

### 실행 명령어

```bash
# 포트/보레이트를 프롬프트로 입력
python3 Ebimu_live.py

# 바로 실행
python3 Ebimu_live.py -p /dev/ttyUSB0

# 포트와 보레이트 지정
python3 Ebimu_live.py -p /dev/serial0 -b 115200

# 화면 갱신 주기 변경 (60Hz)
python3 Ebimu_live.py -p /dev/ttyUSB0 --hz 60

# CSV 형태로 한 줄씩 출력 (파일로 저장)
python3 Ebimu_live.py -p /dev/ttyUSB0 --csv > imu_log.csv

# 센서 원문 그대로 출력
python3 Ebimu_live.py -p /dev/ttyUSB0 --raw

# 켜 둔 출력 항목을 직접 지정 (<sog1> <soa1> <sot1> 을 켠 경우)
python3 Ebimu_live.py -p /dev/ttyUSB0 --layout euler,gyro,accel,temp

# 위에서 한 번 지정했으면 그 다음부터는 그냥 실행하면 됨
python3 Ebimu_live.py -p /dev/ttyUSB0

# 이번만 다르게 보고 저장은 하지 않기
python3 Ebimu_live.py -p /dev/ttyUSB0 --layout quat,gyro,accel --no-save

# 저장된 항목을 지우고 추정으로 되돌리기
python3 Ebimu_live.py --forget

# --layout 에 쓸 수 있는 항목 목록 (포트 없이 실행 가능)
python3 Ebimu_live.py --list-blocks
```

종료는 `Ctrl-C` 입니다.

### 값 이름이 `Val 0`, `Val 1` … 로 나올 때

패킷에는 숫자만 오고 어떤 항목이 켜져 있는지는 적혀 있지 않습니다.
그래서 필드 수로 조합을 추정하는데, **같은 개수가 나오는 조합이 여럿**이라 틀릴 수 있습니다.
(예: 10개 = `euler+gyro+accel+temp` 일 수도, `quat+gyro+accel` 일 수도 있음)

화면 위쪽에 지금 무엇으로 해석하고 있는지 나옵니다.

```
필드 10개 · 추정 euler+gyro+accel+temp
※ 같은 10개 조합: euler+gyro+accel+time, euler+gyro+mag+temp … --layout 으로 확정하세요
```

**가장 확실한 방법은 센서에 직접 물어보는 것입니다.** `Ebimu_cmd.py --detect` 는
`<cfg>` 명령으로 센서의 현재 설정을 그대로 읽어 `--layout` 문자열을 만들어 줍니다.
설정을 읽기만 하므로 아무것도 바뀌지 않습니다.

```bash
# 센서에 물어봐서 알아내기 (모니터를 끄고 실행 — 포트를 하나만 열 수 있음)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --detect

  현재 숫자 필드 10개   *12.50,-3.20,178.90,...

  [detect] <cfg> 로 센서에 현재 설정을 물어봅니다. (설정을 바꾸지 않습니다)

    <sof1>     Euler Angles
    <sog1>     각속도
    <soa1>     중력성분 포함 가속도
    <sot1>     온도

  켜져 있는 항목: euler,gyro,accel,temp  (합계 10개)

    python3 Ebimu_live.py -p /dev/ttyUSB0 --layout euler,gyro,accel,temp
```

`<cfg>` 에 답하지 않는 펌웨어라면 `--toggle` 로 항목을 하나씩 껐다 켜며 찾을 수
있습니다. **다만 출력 설정은 센서에 자동 저장되므로**, 중간에 끊기면 항목이 꺼진 채로
남고 전원을 껐다 켜도 돌아오지 않습니다. 그래서 실행 전에 확인을 한 번 받습니다.

한 번 저장해 두면 모니터는 `--layout` 없이 실행해도 됩니다.

```bash
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --detect --save-layout   # ebimu_layout.txt 생성
python3 Ebimu_live.py -p /dev/ttyUSB0                         # 파일을 읽어서 표시
```

직접 지정해도 됩니다. 지정하면 추정하지 않습니다.

```bash
python3 Ebimu_live.py --list-blocks                                # 항목 이름 확인
python3 Ebimu_live.py -p /dev/ttyUSB0 --layout euler,gyro,accel,temp
```

### 한 번 지정하면 다음부터는 그냥 실행하면 됩니다

`--layout` 으로 준 항목은 `ebimu_layout.txt` 에 저장됩니다.
다음 실행부터는 `--layout` 을 빼도 같은 항목으로 나옵니다.

```bash
python3 Ebimu_live.py -p /dev/ttyUSB0 --layout euler,gyro,accel,temp
  [i] ebimu_layout.txt 에 저장했습니다. 다음부터는 --layout 없이 실행해도 같은 항목으로 나옵니다.

python3 Ebimu_live.py -p /dev/ttyUSB0        # 위와 같게 나옴
  필드 10개 · ebimu_layout.txt euler+gyro+accel+temp
```

화면 위쪽에 항목이 어디서 온 값인지 나옵니다 — `지정`(이번에 `--layout` 으로 준 값),
`ebimu_layout.txt`(저장해 둔 값), `추정`(필드 수로 짐작한 값).

- 저장하지 않고 이번만 다르게 보려면 `--no-save`
- 저장을 지우고 추정으로 되돌리려면 `--forget`
- 센서 설정을 바꿨다면 저장된 값이 안 맞습니다. `--forget` 하거나 새 `--layout` 을 주세요
  (개수가 다르면 화면에 경고가 뜹니다)

찾은 항목의 합계가 실제 필드 수와 다르면 경고가 뜹니다. 이때는 `--raw` 로 원문을 보고
`--layout` 을 손으로 맞춰야 합니다.

지정한 개수와 실제 수신 개수가 다르면 화면에 경고가 뜨고, 남는 값은 `Val N` 으로 표시됩니다.

### 출력 항목 이름

`--layout` 에 콤마로 이어서 씁니다. **패킷에 나오는 순서대로** 적어야 합니다.

| 이름 | 항목 | 개수 | 값 |
| --- | --- | --- | --- |
| `euler` | 오일러각 | 3 | Roll, Pitch, Yaw |
| `quat` | 쿼터니언 | 4 | Quat Z, Quat Y, Quat X, Quat W |
| `gyro` | 각속도 | 3 | Gyro X/Y/Z (deg/s) |
| `accel` | 가속도 | 3 | Accel X/Y/Z (g) |
| `vel` | 속도 | 3 | Vel X/Y/Z (m/s) |
| `mag` | 지자기 | 3 | Mag X/Y/Z (uT) |
| `dist` | 거리 | 3 | Dist X/Y/Z (m) |
| `temp` | 온도 | 1 | Temp (℃) |
| `time` | 타임스탬프 | 1 | Time (ms) |

- 자세(`euler`/`quat`)는 **끌 수 없어** 항상 맨 앞에 옵니다
- `accel` 과 `vel` 은 같은 명령(`soa`)을 써서 **동시에 나올 수 없습니다**

### 중력방향

자세(오일러각 또는 쿼터니언)가 나오면 **중력방향 단위벡터**를 같이 계산해서 보여줍니다.
"몸체 좌표에서 본 아래쪽" 이고, 센서가 수평이면 `(0, 0, -1)` 입니다.

```
  중력방향 (오일러각에서 계산)
  Grav X        -0.342        ·······█····|···········
  Grav Y        -0.470        ······█·····|···········
  Grav Z        -0.814        ··█·········|···········
  기울기           35.5 deg    ····█·······|···········
  가속도차        0.000
```

- **기울기**: 수평에서 몇 도 기울었는지. 수평 0, 옆으로 눕히면 90, 뒤집으면 180
- **가속도차**: 정지 상태의 가속도계는 중력방향을 직접 잽니다. 자세에서 계산한 값과
  비교해 **0.05 이하면 정상**입니다. 크게 벌어지면 부착 방향이나 오일러 순서를
  의심하세요

계산식은 HUPHY 저장소의 `src/huphy/sensors/base.py` 에서 가져왔습니다
(`gravity_from_quat`, `gravity_from_euler`).

**오일러로 받을 때 주의**: 오일러에서 중력을 뽑으려면 회전 순서를 알아야 하는데,
EBIMU 매뉴얼에 명시가 없어 **ZYX(yaw→pitch→roll) 를 전제**합니다. 순서가 다르면
중력방향이 조용히 틀어지고 크기는 여전히 1이라 눈치채기 어렵습니다. 위 '가속도차'
가 그것을 잡는 장치입니다. 확실하게 하려면 쿼터니언 출력(`<sof2>`)을 쓰세요 —
순서 규약이 필요 없습니다.

**가속도차는 `<soa1>`(중력 포함) 일 때만 뜻이 있습니다.** `<soa2>`/`<soa3>` 은 중력을
뺀 값이라 정지 상태에서 0 이 나오고, 차이가 1 로 벌어집니다.

---

## Ebimu_web.py — 웹 3D 뷰어

터미널 막대그래프로는 어느 쪽으로 기울었는지 감이 안 와서, 자세를 브라우저에서
3D 상자로 봅니다. 상자가 센서와 같이 돌고, 빨간 화살표가 중력방향을 가리킵니다.

```bash
python3 Ebimu_web.py -p /dev/ttyUSB0
```

실행하면 주소가 뜹니다. 브라우저로 http://localhost:8000 을 여세요.

| 옵션 | 설명 |
| --- | --- |
| `-p, --port` | 시리얼 포트 |
| `-b, --baud` | 보레이트 (기본 115200) |
| `--layout` | 출력 항목 지정 (`Ebimu_live.py` 와 같음, 저장도 공유) |
| `--list-blocks` | 항목 목록 |
| `--web-port` | 웹 포트 (기본 8000) |
| `--host` | 기본 `127.0.0.1`. 다른 기기에서 보려면 `0.0.0.0` |

```bash
# 웹 포트 바꾸기 (8000 이 이미 쓰이는 경우)
python3 Ebimu_web.py -p /dev/ttyUSB0 --web-port 8080

# 라즈베리파이에 센서를 꽂고 노트북 브라우저에서 보기
python3 Ebimu_web.py -p /dev/ttyUSB0 --host 0.0.0.0
#   -> 노트북에서 http://<라즈베리파이 IP>:8000
```

화면에 나오는 것:

- **센서 보드** — 매뉴얼 9장의 실제 치수 비율 그대로입니다 (16.3 × 18.6 × 3.05mm).
  윗면 부품 배치와 castellated 패드도 도면(38쪽)을 따랐습니다
- **축 화살표** — 보드에 붙어 같이 돕니다. 색은 매뉴얼 4-3~4-5 와 같습니다
  - <span>X+ 빨강</span> (roll 축) · Y+ 초록 (pitch 축) · Z+ 파랑 (yaw 축)
- **회색 화살표 `g`** — 중력. 월드 기준이라 **항상 아래**를 가리킵니다.
  보드가 이것에 대해 얼마나 기울었는지가 곧 자세입니다
- **자세 / 중력방향 / 수신 값** — 터미널 모니터와 같은 값

### 시점 바꾸기

- **끌기** — 시점 회전 (위아래는 ±89°에서 멈춥니다. 넘어가면 방향 감각이 사라집니다)
- **휠** — 확대/축소 (0.4배 ~ 3배)
- **버튼** — `위` / `앞` / `옆` / `기본` 으로 한 번에 이동

시점은 보는 각도만 바꿉니다. 센서 자세와는 별개라 **시점을 돌려도 값은 변하지 않습니다.**

브라우저는 시리얼 포트를 직접 못 읽습니다. 이 스크립트가 포트를 읽어
SSE(Server-Sent Events)로 브라우저에 흘려보냅니다. **라이브러리를 쓰지 않아**
인터넷 없이도 동작하고 (CSS 3D 변환만 씀), 추가 설치도 필요 없습니다.

`Ebimu_live.py` 와 **같은 포트를 동시에 열 수 없습니다.** 하나만 실행하세요.

`--host 0.0.0.0` 은 같은 네트워크의 누구나 볼 수 있게 됩니다. 인증이 없으므로
신뢰할 수 있는 망에서만 쓰세요.

### 센서 설정 요구사항

자세는 항상 나오고(Euler 3개), 자이로 + 가속도를 켜면 9개가 됩니다.
값이 부족하면 출력 항목을 켜세요. 명령 설명은 [COMMANDS.md](COMMANDS.md) 참고.

```bash
python3 Ebimu_cmd.py -p /dev/ttyUSB0 -c "<sog1>" -c "<soa1>"
```

---

## 일반적인 사용 순서

```bash
# 1. 의존성 설치
pip install -r ../requirements.txt

# 2. 현재 센서 출력 확인
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --show

# 3. 9축 출력이 안 나오면 항목 켜기
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --probe

# 4. 켜져 있는 항목을 확인해서 저장 (값 이름이 Val 로 나오지 않게)
python3 Ebimu_cmd.py -p /dev/ttyUSB0 --detect --save-layout

# 5. 실시간 모니터링
python3 Ebimu_live.py -p /dev/ttyUSB0
```
