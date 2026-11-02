"""Read-only gripper telemetry and rigid-transform math, using stdlib only."""
import math
import socket
import threading
import time


def rotation_matrix(vector):
    theta = math.sqrt(sum(x*x for x in vector))
    if theta < 1e-12: return [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]
    x,y,z = [v/theta for v in vector]
    c,s,t = math.cos(theta),math.sin(theta),1-math.cos(theta)
    return [[t*x*x+c,t*x*y-s*z,t*x*z+s*y],
            [t*x*y+s*z,t*y*y+c,t*y*z-s*x],
            [t*x*z-s*y,t*y*z+s*x,t*z*z+c]]


def flange_xyz(tcp_pose, tcp_offset):
    if tcp_pose is None or tcp_offset is None: return None
    # T_base_tcp = T_base_flange @ T_flange_tcp.
    rbt, rft = rotation_matrix(tcp_pose[3:]), rotation_matrix(tcp_offset[3:])
    rbf = [[sum(rbt[i][k]*rft[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    return [tcp_pose[i]-sum(rbf[i][j]*tcp_offset[j] for j in range(3)) for i in range(3)]


class GripperState:
    def __init__(self, host):
        self.host=host
        self.done=threading.Event()
        self.lock=threading.Lock()
        self.state={}
        self.updated=0
        self.thread=None

    def read(self):
        values={}
        with socket.create_connection((self.host,63352),timeout=.3) as sock:
            sock.settimeout(.3)
            # GET only. No activation, target position, speed or force writes.
            for key in ('POS','STA','OBJ','FLT'):
                sock.sendall(('GET '+key+'\n').encode('ascii'))
                response=b''
                deadline=time.monotonic()+.3
                while not response.endswith(b'\n') and len(response)<128:
                    if time.monotonic()>deadline: raise TimeoutError('夾爪回應逾時')
                    part=sock.recv(128-len(response))
                    if not part: raise ConnectionError('夾爪連線中斷')
                    response+=part
                if not response.endswith(b'\n'): raise ValueError('夾爪回應不完整')
                fields=response.decode('ascii').split()
                if len(fields)!=2 or fields[0]!=key: raise ValueError('夾爪回應格式不符')
                values[key]=int(fields[1])
        if not 0<=values['POS']<=255: raise ValueError('夾爪原始位置超出範圍')
        return {'position_raw':values['POS'],'activation_raw':values['STA'],
                'object_raw':values['OBJ'],'fault_raw':values['FLT'],'width_mm':None,
                'grasp_center_xyz_m':None,'model':'Robotiq（具體型號待確認）'}

    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def loop(self):
        while not self.done.is_set():
            try:
                result=self.read()
                with self.lock:self.state=result;self.updated=time.monotonic()
            except (OSError,ValueError) as exc:
                with self.lock:self.state={'error':str(exc)};self.updated=0
            self.done.wait(.7)

    def snapshot(self):
        with self.lock:
            return {**self.state,'connected':time.monotonic()-self.updated<2}

    def close(self): self.done.set()
