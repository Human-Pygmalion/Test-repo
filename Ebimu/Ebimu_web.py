#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBIMU-9DOF 웹 3D 뷰어  -  Ubuntu / Raspberry Pi

센서 자세를 브라우저에서 3D 상자로 본다. 터미널 막대그래프로는 어느 쪽으로
기울었는지 감이 안 오기 때문이다.

사용법
    python3 ebimu_web.py -p /dev/ttyUSB0
    python3 ebimu_web.py -p /dev/ttyUSB0 --web-port 8080
    python3 ebimu_web.py -p /dev/ttyUSB0 --layout quat,gyro,accel

    실행하면 http://localhost:8000 을 브라우저로 연다.
    같은 네트워크의 다른 기기에서 보려면 --host 0.0.0.0 을 준다
    (라즈베리파이에 센서를 꽂고 노트북에서 보는 경우).

    종료: Ctrl-C

왜 로컬 서버인가
    브라우저는 시리얼 포트를 직접 못 읽는다. 이 스크립트가 포트를 읽어
    SSE(Server-Sent Events)로 브라우저에 흘려보낸다.

항목 판정과 중력 계산은 Ebimu_live.py 것을 그대로 쓴다. --layout, 저장된
ebimu_layout.txt, --list-blocks 모두 같게 동작한다.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import Ebimu_live as live       # noqa: E402  항목/중력 계산을 그대로 씀

try:
    import serial
except ImportError:
    sys.exit("pyserial 이 없습니다.  pip install pyserial")


# ────────────────────────────────────────────────────────────────
# 브라우저에 보낼 한 프레임
# ────────────────────────────────────────────────────────────────
def frame(sh, layout):
    vals = list(sh.values)
    labs, note, hint, names = live.resolve(len(vals), layout)

    quat = None
    span = live.block_slice(names, "quat")
    if span and span[1] <= len(vals):
        quat = list(live.to_quaternion(vals[span[0]:span[1]]))

    euler = None
    span = live.block_slice(names, "euler")
    if span and span[1] <= len(vals):
        euler = list(vals[span[0]:span[1]])

    grav, kind = live.gravity_of(vals, names)
    gap = live.accel_gap(vals, names, grav) if grav else None

    return {
        "values": [round(v, 4) for v in vals],
        "labels": [l[0] for l in labs],
        "units": [l[1] for l in labs],
        "quat": quat,
        "euler": euler,
        "gravity": [round(v, 4) for v in grav] if grav else None,
        "gravityFrom": kind,
        "tilt": round(live.tilt_deg(grav), 2) if grav else None,
        "accelGap": round(gap, 4) if gap is not None else None,
        "note": note,
        "hint": hint,
        "hz": round(sh.hz, 1),
        "count": sh.count,
        "bad": sh.bad,
    }


# ────────────────────────────────────────────────────────────────
# 웹 페이지
#
# 라이브러리를 쓰지 않는다. CSS 3D 변환만으로 상자를 돌리므로 인터넷이
# 없어도 되고, 라즈베리파이에서도 가볍다.
# ────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EBIMU 3D</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --line:#262b36;
    --fg:#e6e9ef; --dim:#8b93a7; --accent:#5aa9ff; --warn:#ffb454; --bad:#ff6b6b;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:10px 16px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; font-weight:600; letter-spacing:.02em; }
  .meta { color:var(--dim); font-size:12px; }
  .wrap { display:grid; grid-template-columns:1fr 320px; gap:0; height:calc(100vh - 46px); }
  @media (max-width:800px){ .wrap{ grid-template-columns:1fr; height:auto; } }

  /* 3D 무대 */
  .stage { position:relative; display:grid; place-items:center;
           perspective:900px; overflow:hidden; }
  .scene { transform-style:preserve-3d; transform:rotateX(-18deg) rotateY(-28deg); }
  .body  { position:relative; width:170px; height:170px;
           transform-style:preserve-3d; transition:transform .05s linear; }
  .face  { position:absolute; inset:0; border:1px solid rgba(255,255,255,.25);
           display:grid; place-items:center; font-size:12px; letter-spacing:.08em;
           color:#0f1115; font-weight:700; }
  /* 상자 두께 55px (=110/2) */
  .fx1 { background:rgba(90,169,255,.85);  transform:translateZ(55px); }
  .fx2 { background:rgba(90,169,255,.45);  transform:rotateY(180deg) translateZ(55px); }
  .fy1 { background:rgba(120,220,160,.85); transform:rotateY(90deg) translateZ(55px); }
  .fy2 { background:rgba(120,220,160,.45); transform:rotateY(-90deg) translateZ(55px); }
  .fz1 { background:rgba(255,180,84,.9);   transform:rotateX(90deg) translateZ(55px); }
  .fz2 { background:rgba(255,180,84,.45);  transform:rotateX(-90deg) translateZ(55px); }

  /* 바닥면과 중력 화살표 */
  .floor { position:absolute; width:280px; height:280px; border:1px dashed #333a49;
           transform:rotateX(90deg) translateZ(-130px); }
  .grav  { position:absolute; width:2px; height:110px; background:var(--bad);
           transform-origin:50% 0%; }
  .grav::after { content:""; position:absolute; left:-4px; bottom:-8px;
                 border:5px solid transparent; border-top-color:var(--bad); }

  /* 값 패널 */
  .panel { border-left:1px solid var(--line); background:var(--panel);
           overflow-y:auto; padding:12px 14px; }
  .panel h2 { font-size:12px; color:var(--dim); margin:16px 0 6px;
              font-weight:600; letter-spacing:.06em; text-transform:uppercase; }
  .panel h2:first-child { margin-top:0; }
  table { width:100%; border-collapse:collapse; }
  td { padding:2px 0; white-space:nowrap; }
  td.n { text-align:right; font-variant-numeric:tabular-nums; }
  td.u { color:var(--dim); padding-left:6px; width:44px; }
  .warn { color:var(--warn); }
  .bad  { color:var(--bad); }
  .ok   { color:#7bd88f; }
  .note { color:var(--dim); font-size:12px; margin-top:4px; }
  .off  { color:var(--bad); }
</style></head><body>
<header>
  <h1>EBIMU 3D</h1>
  <span class="meta" id="conn">연결 중…</span>
  <span class="meta" id="stat"></span>
  <span class="meta" id="note"></span>
</header>
<div class="wrap">
  <div class="stage">
    <div class="scene">
      <div class="floor"></div>
      <div class="body" id="box">
        <div class="face fx1">X+</div><div class="face fx2">X−</div>
        <div class="face fy1">Y+</div><div class="face fy2">Y−</div>
        <div class="face fz1">Z+</div><div class="face fz2">Z−</div>
      </div>
      <div class="grav" id="grav"></div>
    </div>
  </div>
  <div class="panel">
    <h2>자세</h2><table id="att"></table>
    <h2>중력방향</h2><table id="gravt"></table>
    <div class="note" id="gnote"></div>
    <h2>수신 값</h2><table id="vals"></table>
  </div>
</div>
<script>
// 쿼터니언 (w,x,y,z) -> 3x3 회전행렬 (몸체 -> 월드)
function quatToR(q) {
  const [w,x,y,z] = q;
  return [
    [1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)],
    [2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)],
    [2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)],
  ];
}
// 오일러(도, ZYX: yaw->pitch->roll) -> 쿼터니언 (w,x,y,z)
function eulerToQuat(r,p,y) {
  const d = Math.PI/180;
  const cr=Math.cos(r*d/2), sr=Math.sin(r*d/2);
  const cp=Math.cos(p*d/2), sp=Math.sin(p*d/2);
  const cy=Math.cos(y*d/2), sy=Math.sin(y*d/2);
  return [cr*cp*cy+sr*sp*sy, sr*cp*cy-cr*sp*sy,
          cr*sp*cy+sr*cp*sy, cr*cp*sy-sr*sp*cy];
}
// 센서 좌표(Z 가 위) -> 화면 좌표(CSS 는 Y 가 아래).
//   화면X = 센서X,  화면Y = -센서Z,  화면Z = 센서Y
const P = [[1,0,0],[0,0,-1],[0,1,0]];
function mul(A,B){ return A.map((r,i)=>B[0].map((_,j)=>
  r.reduce((s,_,k)=>s+A[i][k]*B[k][j],0))); }
function T(A){ return A[0].map((_,j)=>A.map(r=>r[j])); }
function css(R){                       // matrix3d 는 열 우선
  const M = mul(mul(P,R),T(P));
  return `matrix3d(${M[0][0]},${M[1][0]},${M[2][0]},0,`
       + `${M[0][1]},${M[1][1]},${M[2][1]},0,`
       + `${M[0][2]},${M[1][2]},${M[2][2]},0,0,0,0,1)`;
}
function rows(t, list){
  t.innerHTML = list.map(([n,v,u,c]) =>
    `<td>${n}</td><td class="n ${c||''}">${v}</td><td class="u">${u||''}</td>`)
    .map(r=>`<tr>${r}</tr>`).join("");
}
const $ = id => document.getElementById(id);

const es = new EventSource("/stream");
es.onopen  = () => { $("conn").textContent = "연결됨"; $("conn").className="meta ok"; };
es.onerror = () => { $("conn").textContent = "끊김 — 스크립트가 살아있는지 확인";
                     $("conn").className="meta off"; };
es.onmessage = (ev) => {
  const d = JSON.parse(ev.data);
  $("stat").textContent = `${d.hz} Hz · ${d.count} packets · drops ${d.bad}`;
  $("note").textContent = d.note + (d.hint ? "  ※ " + d.hint : "");
  $("note").className = d.hint ? "meta warn" : "meta";

  const q = d.quat ? d.quat
          : (d.euler ? eulerToQuat(d.euler[0], d.euler[1], d.euler[2]) : null);
  if (q) $("box").style.transform = css(quatToR(q));

  if (d.euler) rows($("att"), [["Roll", d.euler[0].toFixed(2), "deg"],
                               ["Pitch",d.euler[1].toFixed(2), "deg"],
                               ["Yaw",  d.euler[2].toFixed(2), "deg"]]);
  else if (d.quat) rows($("att"), d.quat.map((v,i)=>["wxyz"[i], v.toFixed(4), ""]));
  else rows($("att"), [["자세 없음","","" ]]);

  if (d.gravity) {
    const g = d.gravity;
    rows($("gravt"), [
      ["Grav X", g[0].toFixed(3), ""], ["Grav Y", g[1].toFixed(3), ""],
      ["Grav Z", g[2].toFixed(3), ""],
      ["기울기", d.tilt.toFixed(1), "deg"],
      ...(d.accelGap === null ? [] :
        [["가속도차", d.accelGap.toFixed(3), "", d.accelGap > 0.05 ? "bad":"ok"]]),
    ]);
    // 중력 화살표를 실제 방향으로 눕힌다 (화면 좌표로 바꿔서)
    const v = [g[0], -g[2], g[1]];
    const yaw = Math.atan2(v[0], v[2]) * 180/Math.PI;
    const pit = Math.atan2(Math.hypot(v[0],v[2]), v[1]) * 180/Math.PI;
    $("grav").style.transform = `rotateY(${yaw}deg) rotateX(${-pit}deg)`;
    $("gnote").textContent =
      (d.gravityFrom === "euler"
        ? "오일러(ZYX 전제)에서 계산 — 가속도차로 대조하세요"
        : "쿼터니언에서 계산")
      + (d.accelGap !== null && d.accelGap > 0.05
        ? " · 정지 상태인데 가속도차가 크면 부착/순서 확인" : "");
  } else {
    rows($("gravt"), [["자세가 없어 계산 못 함","",""]]);
    $("gnote").textContent = "";
  }

  rows($("vals"), d.values.map((v,i) =>
    [d.labels[i] || `Val ${i}`, v.toFixed(3), d.units[i] || ""]));
};
</script></body></html>
"""


# ────────────────────────────────────────────────────────────────
# HTTP
# ────────────────────────────────────────────────────────────────
class Server(ThreadingHTTPServer):
    """브라우저가 연결을 끊는 것은 정상이다.

    탭을 닫거나 새로고침하면 소켓이 끊기는데, 기본 구현은 그때마다 traceback 을
    찍는다. 터미널이 그것으로 덮이면 정작 봐야 할 시리얼 오류가 묻힌다.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    shared = None
    layout = None

    def log_message(self, *a):
        pass          # 요청 로그로 터미널을 채우지 않는다

    def do_GET(self):
        if self.path.startswith("/stream"):
            return self.stream()
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while self.shared.running:
                data = json.dumps(frame(self.shared, self.layout))
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.0 / 30)
        except (BrokenPipeError, ConnectionResetError):
            pass      # 브라우저 탭을 닫은 것이다. 서버는 계속 돈다


def main():
    ap = argparse.ArgumentParser(description="EBIMU-9DOF 웹 3D 뷰어")
    ap.add_argument("-p", "--port", help="시리얼 포트, 예: /dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=115200, help="기본 115200")
    ap.add_argument("--layout", help="출력 항목 지정, 예: euler,gyro,accel,temp")
    ap.add_argument("--list-blocks", action="store_true",
                    help="--layout 에 쓸 수 있는 항목 목록")
    ap.add_argument("--web-port", type=int, default=8000, help="웹 포트, 기본 8000")
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본 127.0.0.1 (이 기기에서만). 다른 기기에서 보려면 0.0.0.0")
    args = ap.parse_args()

    if args.list_blocks:
        live.list_blocks()
        return

    if args.layout:
        layout = (live.parse_layout(args.layout), "지정")
        live.save_layout_file(layout[0])
    else:
        saved = live.load_layout_file()
        layout = (saved, live.LAYOUT_FILE) if saved else None

    port = args.port or input("EBIMU Port (예: /dev/ttyUSB0): ").strip()
    try:
        ser = serial.Serial(port, args.baud, timeout=0.2)
    except Exception as e:
        sys.exit(f"[!] 포트를 열 수 없습니다: {e}\n"
                 f"    - 포트 확인: ls /dev/ttyUSB* /dev/ttyACM* /dev/serial*\n"
                 f"    - 권한:     sudo usermod -aG dialout $USER  (재로그인)")

    sh = live.Shared()
    threading.Thread(target=live.reader, args=(ser, sh, False), daemon=True).start()

    Handler.shared, Handler.layout = sh, layout
    httpd = Server((args.host, args.web_port), Handler)
    shown = "localhost" if args.host == "127.0.0.1" else args.host
    print(f"\n  브라우저로 여세요:  http://{shown}:{args.web_port}")
    if args.host == "127.0.0.1":
        print("  다른 기기에서 보려면 --host 0.0.0.0 을 주세요")
    print("  종료: Ctrl-C\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sh.running = False
        httpd.server_close()
        ser.close()
        print("종료했습니다.")


if __name__ == "__main__":
    main()
