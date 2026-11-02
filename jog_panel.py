#!/usr/bin/env python3
"""Low-speed bounded-pulse UR5e joint jogging. No motion until explicitly armed."""
import math
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox

HOST = '192.168.10.2'
SPEED = math.radians(1)  # fixed 1 degree/s
PULSE = 0.10
INTERVAL = 0.40
LEASE = 0.15
MAX_HOLD = 3.0


def dashboard():
    with socket.create_connection((HOST, 29999), timeout=0.5) as s:
        s.settimeout(0.5)
        f = s.makefile('rb')
        f.readline()
        out = {}
        for c in ('is in remote control', 'robotmode', 'safetystatus', 'programState'):
            s.sendall((c + '\n').encode())
            out[c] = f.readline().decode().strip()
        return out


def readn(s, n):
    b = b''
    while len(b) < n:
        part = s.recv(n - len(b))
        if not part:
            raise ConnectionError('狀態連線中斷')
        b += part
    return b


def joints():
    with socket.create_connection((HOST, 30011), timeout=0.5) as s:
        s.settimeout(0.5)
        for _ in range(30):
            size, typ = struct.unpack('!IB', readn(s, 5))
            if not 5 <= size <= 1000000:
                raise ValueError('狀態封包錯誤')
            data = readn(s, size - 5)
            if typ != 16:
                continue
            p = 0
            while p + 5 <= len(data):
                length, kind = struct.unpack_from('!IB', data, p)
                if length < 5 or p + length > len(data):
                    raise ValueError('關節封包錯誤')
                if kind == 1:
                    if length != 251:
                        raise ValueError('不支援的關節封包格式')
                    return [math.degrees(struct.unpack_from('!d', data, p+5+i*41)[0]) for i in range(6)]
                p += length
        raise ValueError('未收到關節資料')


def ready(state):
    return (state['is in remote control'] == 'true'
            and state['robotmode'] == 'Robotmode: RUNNING'
            and state['safetystatus'] == 'Safetystatus: NORMAL')


def pulse_script(axis, direction):
    if axis not in range(6) or direction not in (-1, 1):
        raise ValueError('Invalid joint command')
    speeds = [0.0] * 6
    speeds[axis] = direction * SPEED
    # t alone does not stop velocity: explicit robot-side stopj is mandatory.
    return ('def bounded_jog():\n'
            '  speedj(%s, a=0.2, t=%.3f)\n'
            '  stopj(0.5)\n'
            'end\n' % (str(speeds), PULSE))


class Panel:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.armed = False
        self.hold = None
        self.lease = 0.0
        self.started = 0.0
        self.closed = threading.Event()
        root.title('UR5e 六軸低速點動 — 192.168.10.2')
        root.geometry('640x630')
        tk.Label(root, text='UR5e 六軸點動', font=('', 22)).pack(pady=12)
        tk.Label(root, text='固定 1°/s · 短脈衝點動 · 單次按住最多 3 秒\n放開 / 離開按鈕 / 視窗失焦即取消後續動作', fg='#444').pack()
        self.status = tk.StringVar(value='正在讀取機器人狀態…')
        tk.Label(root, textvariable=self.status, wraplength=600).pack(pady=12)
        self.values = []
        self.buttons = []
        frame = tk.Frame(root)
        frame.pack()
        for i, name in enumerate(('底座', '肩部', '肘部', '手腕 1', '手腕 2', '手腕 3')):
            tk.Label(frame, text='J%d %s' % (i+1, name), width=12).grid(row=i, column=0, pady=7)
            v = tk.StringVar(value='— °')
            self.values.append(v)
            tk.Label(frame, textvariable=v, width=15, font=('', 16)).grid(row=i, column=1)
            for col, d, label in ((2, -1, '− 按住'), (3, 1, '＋ 按住')):
                b = tk.Button(frame, text=label, width=10, state='disabled')
                b.grid(row=i, column=col, padx=6)
                b.bind('<ButtonPress-1>', lambda e, a=i, sign=d: self.press(a, sign))
                b.bind('<Leave>', lambda e: self.release())
                self.buttons.append(b)
        self.arm_button = tk.Button(root, text='啟用點動', command=self.arm)
        self.arm_button.pack(pady=12)
        tk.Button(root, text='停止／解除啟用（空白鍵）', command=self.disarm, bg='#eaa', width=35).pack()
        tk.Label(root, text='此按鈕不是安全急停。請保持實體急停可觸及。\n尚未實機驗證；首次使用請確認工具、負載、關節限制及作業空間。\n不會自動上電、釋放煞車、解除保護停止或啟動既有程式。', fg='#933', wraplength=600).pack(pady=14)
        root.bind_all('<ButtonRelease-1>', lambda e: self.release())
        root.bind('<FocusOut>', lambda e: self.disarm())
        root.bind('<space>', lambda e: self.disarm())
        root.bind('<Escape>', lambda e: self.disarm())
        root.protocol('WM_DELETE_WINDOW', self.close)
        threading.Thread(target=self.worker, daemon=True).start()
        self.tick()

    def arm(self):
        if not messagebox.askokcancel('啟用低速點動', '確認周圍淨空、負載與工具設定正確，並可立即按實體急停。\n每個按鈕只移動一個關節。是否啟用？'):
            return
        self.events.put(('arm', None))

    def press(self, axis, direction):
        with self.lock:
            if self.armed:
                self.hold = (axis, direction)
                self.started = time.monotonic()
                self.lease = self.started + LEASE

    def release(self):
        with self.lock:
            self.hold = None
            self.lease = 0

    def disarm(self):
        with self.lock:
            self.armed = False
            self.hold = None
            self.lease = 0
        for b in self.buttons:
            b.config(state='disabled')
        self.arm_button.config(state='normal')

    def tick(self):
        with self.lock:
            if self.hold and time.monotonic() - self.started < MAX_HOLD:
                self.lease = time.monotonic() + LEASE
            elif self.hold:
                self.hold = None
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'arm':
                    threading.Thread(target=self.check_arm, daemon=True).start()
                elif kind == 'armed':
                    with self.lock:
                        self.armed = True
                    for b in self.buttons:
                        b.config(state='normal')
                    self.arm_button.config(state='disabled')
                elif kind == 'state':
                    angles, state = data
                    for v, q in zip(self.values, angles):
                        v.set('%.3f°' % q)
                    self.status.set('遠端：%s ｜ %s ｜ %s' % (state['is in remote control'], state['robotmode'], state['safetystatus']))
                elif kind == 'error':
                    self.disarm()
                    self.status.set('已解除啟用：' + str(data))
        except queue.Empty:
            pass
        if not self.closed.is_set():
            self.root.after(40, self.tick)

    def check_arm(self):
        try:
            s = dashboard()
            joints()
            if not ready(s) or not s['programState'].startswith('STOPPED'):
                raise RuntimeError('需要遠端、RUNNING、NORMAL 且程式停止')
            self.events.put(('armed', None))
        except Exception as e:
            self.events.put(('error', e))

    def worker(self):
        last_pulse = 0
        while not self.closed.is_set():
            try:
                state = dashboard()
                angles = joints()
                self.events.put(('state', (angles, state)))
                if not ready(state):
                    raise RuntimeError('遠端或安全狀態不允許點動')
                # One worker, no queued motion, freshness rechecked after network I/O.
                with self.lock:
                    now = time.monotonic()
                    hold = self.hold if (self.armed and now < self.lease and now-self.started < MAX_HOLD) else None
                if hold and now-last_pulse >= INTERVAL:
                    if not state['programState'].startswith('STOPPED'):
                        raise RuntimeError('其他程式執行中，已禁止點動')
                    with socket.create_connection((HOST, 30002), timeout=0.3) as s:
                        s.settimeout(0.15)
                        with self.lock:
                            if self.armed and self.hold == hold and time.monotonic() < self.lease:
                                s.sendall(pulse_script(*hold).encode('ascii'))
                                last_pulse = time.monotonic()
            except Exception as e:
                with self.lock:
                    self.armed = False
                    self.hold = None
                self.events.put(('error', e))
            self.closed.wait(0.08)

    def close(self):
        self.disarm()
        self.closed.set()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Panel(root)
    root.mainloop()
