"""Explicitly motion-free reverse-connection probe. Run only during maintenance."""
import json
import re
import secrets
import socket
import struct
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_bridge import HOST, continuous_script, dashboard, ready


def run_probe():
    state = dashboard()
    if not ready(state) or not state['programState'].startswith('STOPPED'):
        raise RuntimeError('Probe requires a ready robot with no program running')
    messages = []
    poses = []
    done = threading.Event()

    def observe():
        with socket.create_connection((HOST,30011),timeout=.5) as sock:
            sock.settimeout(.5)
            def readn(n):
                data=b''
                while len(data)<n:
                    part=sock.recv(n-len(data))
                    if not part: raise ConnectionError('state stream ended')
                    data+=part
                return data
            while not done.is_set():
                try:
                    size,kind=struct.unpack('!IB',readn(5)); payload=readn(size-5)
                    if kind == 20 and len(payload)>=10:
                        subtype=payload[9]
                        text=''.join(chr(x) if 32<=x<127 else ' ' for x in payload[10:])
                        messages.append({'subtype':subtype,'text':text.strip()})
                    elif kind == 16 and not poses:
                        offset=0
                        while offset+5<=len(payload):
                            length,subkind=struct.unpack_from('!IB',payload,offset)
                            if length<5 or offset+length>len(payload): break
                            if subkind==4 and length>=101:
                                poses.append(struct.unpack_from('!12d',payload,offset+5))
                            offset+=length
                except socket.timeout: continue
                except Exception as exc:
                    messages.append({'observer_error':str(exc)});break

    observer=threading.Thread(target=observe,daemon=True);observer.start()
    duration=5.0 if '--long' in sys.argv else 1.3
    result={'before':state,'motion_commands_sent':False,'python':sys.executable,'requested_duration_s':duration}
    try:
        with socket.create_connection((HOST,30002),timeout=.5) as program:
            address=program.getsockname()[0]
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
                listener.bind((address,0));listener.listen(1);listener.settimeout(3)
                nonce=secrets.randbelow(2**31-1)+1
                mode='cartesian' if '--cartesian' in sys.argv else 'joint'
                result['mode']=mode
                script=continuous_script(0,1,address,listener.getsockname()[1],nonce,mode).decode()
                if '--simple' in sys.argv:
                    script=f'''def web_reverse_probe():
  if socket_open("{address}", {listener.getsockname()[1]}, "web_hold"):
    local count = 0
    while count < 10:
      socket_send_int(count, "web_hold")
      local reply = socket_read_binary_integer(3, "web_hold", timeout=2)
      if reply[0] != 3:
        break
      end
      count = count + 1
      sleep(0.02)
    end
    socket_close("web_hold")
  end
end
'''
                script=re.sub(r'^\s+speed[jl]\(.*$', '      sync()',script,flags=re.M)
                script=re.sub(r'^\s+stop[jl]\(.*$', '    sync()',script,flags=re.M)
                if '--simple' not in sys.argv:
                    script=script.replace('    global web_alive = False\n    sync()',
                                          '    textmsg("DIAG_CLOCK=", web_clock)\n'
                                          '    textmsg("DIAG_GRANT=", web_grant_at)\n'
                                          '    textmsg("DIAG_ALIVE=", web_alive)\n'
                                          '    textmsg("DIAG_GRANTED=", web_granted)\n'
                                          '    global web_alive = False\n    sync()')
                for forbidden in ('speedj(', 'speedl(', 'stopj(', 'stopl(', 'movej(', 'movel(', 'servoj(', 'set_digital_out('):
                    if forbidden in script: raise AssertionError('Motion-free check failed: '+forbidden)
                Path(__file__).with_suffix('.urscript').write_text(script)
                began=time.monotonic();program.sendall(script.encode('ascii'))
                connection,peer=listener.accept()
                result['reverse_peer']=peer[0]
                result['connect_seconds']=round(time.monotonic()-began,4)
                count=0; gaps=[]; last=None
                with connection as sock:
                    result['accepted_at']=round(time.monotonic()-began,4)
                    sock.settimeout(.5)
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    except OSError as exc:
                        result['tcp_nodelay_warning']=str(exc)
                    while time.monotonic()-began<duration+1.2:
                        data=b''
                        while len(data)<4:
                            part=sock.recv(4-len(data))
                            if not part: break
                            data+=part
                        if len(data)!=4: break
                        sequence,=struct.unpack('!i',data)
                        result['last_sequence']=sequence
                        if '--disconnect' in sys.argv and sequence >= 10:
                            result['deliberate_disconnect']=True
                            break
                        now=time.monotonic()
                        if last is not None:gaps.append(now-last)
                        last=now
                        enable=int(now-began<duration)
                        sock.sendall(struct.pack('!iii',nonce,sequence,enable));count+=1
                        if not enable:break
                result.update(challenges=count, max_gap_s=round(max(gaps,default=0),4), exchange_duration_s=round(time.monotonic()-began,3))
    except Exception as exc:
        result['error']=str(exc)
    finally:
        done.wait(.3);done.set();observer.join(timeout=1)
    result['messages']=messages
    result['cartesian_packets']=poses
    result['after']=dashboard()
    destination=Path(__file__).parent/('probe-%s-%s.json' %
                                     (time.strftime('%Y%m%d-%H%M%S'),result.get('mode','unknown')))
    destination.write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':run_probe()
