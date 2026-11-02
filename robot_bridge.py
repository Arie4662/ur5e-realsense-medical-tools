"""Continuous low-speed joint control with browser and robot-side watchdogs.

No automatic power-on, brake release, safety reset, gripper action, or motion on connect.
"""
import math
import ipaddress
import secrets
import socket
import struct
import threading
import time
from tool_state import GripperState, flange_xyz

HOST = '192.168.10.2'
SPEED = math.radians(1)
LINEAR_SPEED = 0.005
LEASE = 0.25
ROBOT_TIMEOUT = 0.12


def dashboard():
    with socket.create_connection((HOST, 29999), timeout=.4) as sock:
        sock.settimeout(.4)
        stream = sock.makefile('rb')
        if 'Dashboard' not in stream.readline().decode():
            raise RuntimeError('Dashboard 回應不正確')
        state = {}
        for key in ('is in remote control', 'robotmode', 'safetystatus', 'programState'):
            sock.sendall((key+'\n').encode())
            state[key] = stream.readline().decode().strip()
        return state


def parse_robot_state(payload):
    result = {'joints':None, 'tcp_pose':None, 'tcp_offset':None}
    offset = 0
    while offset+5 <= len(payload):
        length, kind = struct.unpack_from('!IB', payload, offset)
        if length < 5 or offset+length > len(payload):
            raise ValueError('無效狀態封包')
        if kind == 1:
            if length != 251:
                raise ValueError('不支援的關節資料格式')
            result['joints'] = [math.degrees(struct.unpack_from('!d',payload,offset+5+i*41)[0]) for i in range(6)]
        elif kind == 4:
            if length < 101:
                raise ValueError('不完整的 TCP 資料')
            values = list(struct.unpack_from('!12d',payload,offset+5))
            result['tcp_pose'], result['tcp_offset'] = values[:6], values[6:]
        offset += length
    if offset != len(payload): raise ValueError('狀態封包尾端不完整')
    for values in result.values():
        if values is not None and not all(math.isfinite(v) for v in values):
            raise ValueError('機器人位置不是有限數值')
    return result


def kinematics():
    deadline = time.monotonic()+.8
    def readn(sock, size):
        data = b''
        while len(data) < size:
            if time.monotonic() > deadline: raise TimeoutError('位置讀取逾時')
            part = sock.recv(size-len(data))
            if not part: raise ConnectionError('位置連線中斷')
            data += part
        return data
    with socket.create_connection((HOST,30011),timeout=.4) as sock:
        sock.settimeout(.4)
        for _ in range(30):
            size, kind = struct.unpack('!IB',readn(sock,5))
            if not 5 <= size <= 1000000: raise ValueError('無效狀態封包')
            payload = readn(sock,size-5)
            if kind == 16:
                result = parse_robot_state(payload)
                if result['joints'] is not None: return result
    raise RuntimeError('沒有收到機器人位置')


def joints():
    return kinematics()['joints']


def ready(state):
    return (state.get('is in remote control') == 'true'
            and state.get('robotmode') == 'Robotmode: RUNNING'
            and state.get('safetystatus') == 'Safetystatus: NORMAL')


def validate_direction(axis, direction, mode="joint"):
    if mode not in ("joint", "cartesian"):
        raise ValueError("未知的移動模式")
    if type(axis) is not int or axis not in range(6) or type(direction) is not int or direction not in (-1, 1):
        raise ValueError('只接受六個方向軸與正負方向')


def continuous_script(axis, direction, address, port, nonce, mode="joint"):
    validate_direction(axis, direction, mode)
    address = str(ipaddress.IPv4Address(address))
    if type(port) is not int or not 1 <= port <= 65535 or type(nonce) is not int or not 0 < nonce < 2**31:
        raise ValueError('無效的反向連線參數')
    velocity = [0.0]*6
    velocity[axis] = direction*(LINEAR_SPEED if mode == 'cartesian' and axis < 3 else SPEED)
    motion = ('speedj(%s, a=0.2, t=get_steptime())' % velocity if mode == 'joint' else
              'speedl(%s, a=0.05, t=get_steptime(), aRot=0.2)' % velocity)
    stop = 'stopj(0.5)' if mode == 'joint' else 'stopl(0.1, aRot=0.5)'
    # One request in flight: a delayed reply cannot renew a later lease. The
    # grant is dated at REQUEST time on the robot, never at receipt time.
    return f'''def web_continuous_hold():
  global web_alive = True
  global web_granted = False
  global web_clock = 0.0
  global web_grant_at = -1.0
  thread web_timer():
    while web_alive:
      sync()
      global web_clock = web_clock + get_steptime()
    end
  end
  thread web_reader():
    local sequence = 0
    while web_alive:
      local requested_at = web_clock
      socket_send_int(sequence, "web_hold")
      local wait_time = {ROBOT_TIMEOUT}
      if not web_granted:
        wait_time = 0.6
      end
      local reply = socket_read_binary_integer(3, "web_hold", timeout=wait_time)
      if reply[0] != 3:
        global web_alive = False
      elif reply[1] != {nonce} or reply[2] != sequence or reply[3] != 1:
        global web_alive = False
      elif web_clock - requested_at >= {ROBOT_TIMEOUT}:
        if web_granted:
          global web_alive = False
        end
      else:
        global web_grant_at = requested_at
        global web_granted = True
      end
      sequence = sequence + 1
      sleep(0.02)
    end
  end
  if socket_open("{address}", {port}, "web_hold"):
    local timer = run web_timer()
    local reader = run web_reader()
    while web_alive and not web_granted and web_clock < 1.0:
      sync()
    end
    while web_alive and web_granted and web_clock - web_grant_at < {ROBOT_TIMEOUT}:
      {motion}
    end
    global web_alive = False
    {stop}
    kill reader
    kill timer
    socket_close("web_hold")
  end
end
'''.encode('ascii')


class RobotBridge:
    def __init__(self, camera_ready, clock=time.monotonic):
        self.camera_ready = camera_ready
        self.clock = clock
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.state = {}
        self.q = None
        self.tcp_pose = None
        self.tcp_offset = None
        self.owner_mode = None
        self.confirmed_tcp = None
        self.control_revision = 0
        self.last_stop_reason = None
        self.motion_phase = "idle"
        self.updated = 0
        self.message = '正在連接手臂'
        self.owner = None
        self.intent = None
        self.lease_until = 0
        self.last_seq = -1
        self.owner_seen = 0
        self.thread = None
        self.motion_thread = None
        self.launched = None
        self.reverse_socket = None
        self.gripper = GripperState(HOST)

    def start(self):
        self.gripper.start()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def revoke(self, message='移動已解除啟用'):
        with self.lock:
            if self.owner is not None or self.intent is not None:
                self.last_stop_reason = message
                print('ROBOT_STOP: '+message, flush=True)
            self.control_revision += 1
            self.owner = None
            self.owner_mode = None
            self.confirmed_tcp = None
            self.motion_phase = 'idle'
            self.intent = None
            self.lease_until = 0
            self.message = message
            if self.reverse_socket is not None:
                try: self.reverse_socket.shutdown(socket.SHUT_RDWR)
                except OSError: pass

    def safe(self):
        return self.clock()-self.updated < .8 and ready(self.state) and self.camera_ready()

    def arm(self, client, mode="joint", tcp_confirmed=False, tcp_offset=None):
        validate_direction(0,1,mode)
        with self.lock:
            if self.motion_thread and self.motion_thread.is_alive():
                raise ValueError('前次移動正在結束，請稍候再啟用')
            if self.owner:
                raise ValueError('已有控制者，請先解除啟用')
            if not self.safe() or not self.state.get('programState', '').startswith('STOPPED'):
                raise ValueError('需要新鮮影像、遠端模式、正常安全狀態及已停止的程式')
            if mode == 'cartesian':
                if self.tcp_pose is None or self.tcp_offset is None:
                    raise ValueError('尚未取得 TCP 位置與偏移，不能啟用笛卡爾移動')
                if tcp_confirmed is not True or not self.same_tcp(tcp_offset):
                    raise ValueError('請確認目前的 TCP／法蘭作用點與偏移設定')
                self.confirmed_tcp = tuple(self.tcp_offset)
            self.owner = client
            self.owner_mode = mode
            self.control_revision += 1
            self.owner_seen = self.clock()
            self.last_seq = -1
            self.message = '已啟用，按住某軸按鈕移動'

    def hold(self, client, seq, hold_id, axis, direction, new=False, mode="joint"):
        validate_direction(axis, direction, mode)
        if type(seq) is not int or not isinstance(hold_id, str) or not 1 <= len(hold_id) <= 80:
            raise ValueError('無效的按壓序號')
        with self.lock:
            now = self.clock()
            if self.owner != client or self.owner_mode != mode or not self.safe():
                raise ValueError('控制未啟用或影像／手臂狀態已過期')
            if mode == 'cartesian' and not self.same_tcp(self.confirmed_tcp):
                self.revoke('TCP 設定已變更，請重新確認')
                raise ValueError('TCP 設定已變更')
            if seq <= self.last_seq:
                raise ValueError('已忽略過期操作')
            if new:
                if self.intent is not None:
                    raise ValueError('一次只允許一個移動方向')
                if not self.state.get('programState', '').startswith('STOPPED'):
                    raise ValueError('手臂已有程式執行，請先停止')
                self.intent = (hold_id, axis, direction, mode)
                self.control_revision += 1
            elif self.intent != (hold_id, axis, direction, mode) or now >= self.lease_until:
                self.revoke('按壓連線逾時，請重新啟用')
                raise ValueError('按壓租期已失效')
            self.last_seq = seq
            self.owner_seen = now
            self.lease_until = now+LEASE

    def release(self, client):
        with self.lock:
            if self.owner == client:
                # Revoke owner, so delayed arm/hold packets cannot resume motion.
                self.revoke('已停止；下一次移動請重新啟用')

    def snapshot(self):
        with self.lock:
            fresh = self.clock()-self.updated < .8
            return {'connected': fresh and bool(self.state), 'state': self.state,
                    'joints': self.q, 'tcp_pose_m_rad':self.tcp_pose, 'tcp_offset_m_rad':self.tcp_offset,
                    'flange_xyz_m':flange_xyz(self.tcp_pose,self.tcp_offset),
                    'gripper':self.gripper.snapshot(),
                    'coordinate_frame':'base', 'armed': self.owner is not None,
                    'moving_requested': self.intent is not None, 'message': self.message,
                    'mode':'continuous', 'speed_deg_s':1, 'max_hold_s':None,
                    'robot_watchdog_s':ROBOT_TIMEOUT, 'hardware_validated':False,
                    'control_revision':self.control_revision, 'control_mode':self.owner_mode,
                    'linear_speed_mm_s':LINEAR_SPEED*1000, 'angular_speed_deg_s':1,
                    'last_stop_reason':self.last_stop_reason, 'motion_phase':self.motion_phase}

    def same_tcp(self, value):
        return (isinstance(value, (list,tuple)) and len(value) == 6 and self.tcp_offset is not None
                and all(type(a) in (float,int) and math.isfinite(a) and abs(a-b) < 1e-6
                        for a,b in zip(value,self.tcp_offset)))

    def eligible(self):
        return (self.owner is not None and self.intent is not None
                and self.clock() < self.lease_until
                and self.safe() and (self.owner_mode != 'cartesian' or self.same_tcp(self.confirmed_tcp)))

    def reply_to_challenge(self, sock, intent, nonce, sequence, previous):
        if sequence != previous+1:
            raise ValueError('反向連線序號錯誤')
        with self.lock:
            allowed = self.eligible() and self.intent == intent
            sock.sendall(struct.pack('!iii', nonce, sequence, int(allowed)))
        return allowed

    def run_motion(self, intent):
        try:
            # Discover the specific Mac interface used for this robot, bind only
            # that address and accept only the configured robot IP.
            with socket.create_connection((HOST, 30002), timeout=.2) as program:
                program.settimeout(.1)
                address = program.getsockname()[0]
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind((address, 0))
                    listener.listen(1)
                    listener.settimeout(.6)
                    nonce = secrets.randbelow(2**31-1)+1
                    script = continuous_script(intent[1], intent[2], address, listener.getsockname()[1], nonce, intent[3])
                    # Recheck after connect and listener setup. Released requests
                    # cannot upload a program, nor can a new hold reuse this one.
                    with self.lock:
                        if not self.eligible() or self.intent != intent:
                            return
                        self.motion_phase = "connecting"
                        program.sendall(script)
                    connection, peer = listener.accept()
                    with connection as sock:
                        if peer[0] != HOST:
                            raise ValueError('反向連線來源不符')
                        sock.settimeout(.16)
                        try: sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        except OSError: pass  # Optional tuning; timeout checks still apply.
                        with self.lock:
                            self.reverse_socket = sock
                            self.motion_phase = "streaming"
                        previous = -1
                        while True:
                            data = b''
                            deadline = time.monotonic()+.16
                            while len(data) < 4:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('反向連線讀取逾時')
                                part = sock.recv(4-len(data))
                                if not part:
                                    with self.lock:
                                        if self.intent == intent:
                                            self.revoke('手臂已關閉控制連線；請查看示教器訊息')
                                    return
                                data += part
                            sequence, = struct.unpack('!i', data)
                            if not self.reply_to_challenge(sock, intent, nonce, sequence, previous):
                                return
                            previous = sequence
        except Exception as exc:
            with self.lock:
                if self.intent == intent:
                    self.revoke('連續移動已停止：'+str(exc))
        finally:
            with self.lock:
                self.reverse_socket = None
                if self.intent == intent:
                    self.revoke('已停止；下一次移動請重新啟用')

    def loop(self):
        last_read = 0
        while not self.done.is_set():
            try:
                now = self.clock()
                if now-last_read > .18:
                    state, pose = dashboard(), kinematics()
                    with self.lock:
                        self.state, self.q, self.updated = state, pose['joints'], self.clock()
                        self.tcp_pose, self.tcp_offset = pose['tcp_pose'], pose['tcp_offset']
                        if self.owner_mode == 'cartesian' and not self.same_tcp(self.confirmed_tcp):
                            self.revoke('TCP 設定已變更，笛卡爾移動已停止')
                        if not ready(state):
                            self.revoke('手臂尚未就緒或安全狀態異常')
                        elif not self.owner:
                            self.message = '已連線，等待啟用移動'
                    last_read = self.clock()
                with self.lock:
                    now = self.clock()
                    if self.owner and (not self.safe() or now-self.owner_seen > 15):
                        self.revoke('影像／手臂斷線或閒置，已解除啟用')
                    if self.intent and now >= self.lease_until:
                        self.revoke('按壓已逾時，已停止')
                    if self.eligible() and self.launched != self.intent:
                        if not self.state.get('programState', '').startswith('STOPPED'):
                            self.revoke('程式仍在執行，已取消移動')
                        else:
                            self.launched = self.intent
                            self.motion_thread = threading.Thread(target=self.run_motion, args=(self.intent,), daemon=True)
                            self.motion_thread.start()
            except Exception as exc:
                with self.lock:
                    self.updated = 0
                    self.revoke('連線錯誤：'+str(exc))
            self.done.wait(.03)

    def close(self):
        self.gripper.close()
        self.revoke('控制服務已停止')
        self.done.set()
        if self.motion_thread:
            self.motion_thread.join(timeout=1.0)
