import unittest
import struct
from unittest.mock import patch
from robot_bridge import RobotBridge, continuous_script, parse_robot_state


class Controls(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.camera = True
        self.r = RobotBridge(lambda: self.camera, lambda: self.now)
        self.r.updated = self.now
        self.r.state = {'is in remote control':'true', 'robotmode':'Robotmode: RUNNING',
                        'safetystatus':'Safetystatus: NORMAL', 'programState':'STOPPED test'}

    def arm_start(self):
        self.r.arm('tab1')
        self.r.hold('tab1',1,'press1',0,1,True)

    def test_camera_and_remote_required(self):
        self.camera = False
        with self.assertRaises(ValueError): self.r.arm('tab1')
        self.camera = True
        self.r.state['is in remote control'] = 'false'
        with self.assertRaises(ValueError): self.r.arm('tab1')

    def test_lease_and_release_reject_delayed_heartbeat(self):
        self.arm_start()
        self.now += .3
        self.r.updated = self.now
        self.assertFalse(self.r.eligible())
        with self.assertRaises(ValueError): self.r.hold('tab1',2,'press1',0,1)
        self.assertIsNone(self.r.owner)
        self.arm_start()
        self.r.release('tab1')
        with self.assertRaises(ValueError): self.r.hold('tab1',3,'press1',0,1)

    def test_one_owner_and_order(self):
        self.arm_start()
        with self.assertRaises(ValueError): self.r.arm('tab2')
        with self.assertRaises(ValueError): self.r.hold('tab2',2,'press1',0,1)
        with self.assertRaises(ValueError): self.r.hold('tab1',1,'press1',0,1)
        with self.assertRaises(ValueError): self.r.hold('tab1',2,'press2',1,1,True)

    def test_camera_loss_still_stops_continuous_hold(self):
        self.arm_start()
        self.camera = False
        self.assertFalse(self.r.eligible())
        self.camera = True
        self.assertTrue(self.r.eligible())

    def test_fresh_heartbeats_allow_long_hold_in_both_modes_then_release_stops(self):
        for mode in ('joint','cartesian'):
            self.setUp()
            self.r.tcp_pose=[0,0,1,0,0,0]
            self.r.tcp_offset=[0]*6
            self.r.arm('tab1',mode,True,[0]*6)
            self.r.hold('tab1',1,'long-press',0,1,True,mode)
            # Simulate 60 seconds of held input, well beyond the old 3 s limit.
            for seq in range(2,1002):
                self.now += .06
                self.r.updated = self.now
                self.r.hold('tab1',seq,'long-press',0,1,False,mode)
                self.assertTrue(self.r.eligible())
            self.assertIsNone(self.r.snapshot()['max_hold_s'])
            self.r.release('tab1')
            self.assertFalse(self.r.eligible())
            with self.assertRaises(ValueError): self.r.hold('tab1',1002,'long-press',0,1,False,mode)

    def test_stale_heartbeat_still_stops_after_a_long_hold(self):
        self.arm_start()
        for seq in range(2,102):
            self.now += .06
            self.r.updated = self.now
            self.r.hold('tab1',seq,'press1',0,1)
        self.now += .26
        self.r.updated = self.now
        self.assertFalse(self.r.eligible())
        with self.assertRaises(ValueError): self.r.hold('tab1',102,'press1',0,1)
        self.assertIsNone(self.r.owner)

    def test_release_during_socket_connect_sends_nothing(self):
        self.arm_start()
        intent = self.r.intent
        r = self.r
        calls = []
        class FakeProgram:
            def __enter__(self):
                r.release('tab1')
                return self
            def __exit__(self,*args): pass
            def settimeout(self,value): pass
            def getsockname(self): return ('192.168.10.1',50000)
            def sendall(self,value): calls.append(value)
        with patch('robot_bridge.socket.create_connection',return_value=FakeProgram()), patch('robot_bridge.socket.socket') as listener:
            listener.return_value.__enter__.return_value.getsockname.return_value = ('192.168.10.1', 50001)
            self.r.run_motion(intent)
        self.assertEqual(calls, [])

    def test_robot_challenges_stop_after_browser_expiry_camera_loss_release(self):
        for condition in ['expired','camera','release','wrong_hold']:
            self.setUp()
            self.arm_start()
            intent = self.r.intent
            packets = []
            class Sink:
                def sendall(self, data): packets.append(struct.unpack('!iii', data))
            sock = Sink()
            self.assertTrue(self.r.reply_to_challenge(sock,intent,999,0,-1))
            if condition == 'expired': self.now += .26
            elif condition == 'camera': self.camera = False
            elif condition == 'release': self.r.release('tab1')
            else: self.r.intent = ('new_hold',0,1)
            self.assertFalse(self.r.reply_to_challenge(sock,intent,999,1,0))
            self.assertEqual(packets,[(999,0,1),(999,1,0)])

    def test_replay_does_not_send_a_grant(self):
        self.arm_start()
        with patch('robot_bridge.socket.socket') as sock:
            for seq, previous in [(0,0),(4,1),(-1,-1)]:
                with self.assertRaises(ValueError):
                    self.r.reply_to_challenge(sock,self.r.intent,999,seq,previous)
            sock.sendall.assert_not_called()

    def test_release_closes_live_reverse_connection(self):
        self.arm_start()
        with patch('robot_bridge.socket.socket') as sock:
            self.r.reverse_socket = sock
            self.r.release('tab1')
            sock.shutdown.assert_called_once()

    def test_program_running_rejects_new_hold(self):
        self.r.arm('tab1')
        self.r.state['programState'] = 'PLAYING other'
        with self.assertRaises(ValueError): self.r.hold('tab1',1,'p',0,1,True)
        self.assertIsNone(self.r.intent)

    def test_continuous_program_has_robot_side_watchdogs(self):
        for axis in range(6):
            for sign in (-1,1):
                script = continuous_script(axis,sign,'192.168.10.1',50001,123456).decode()
                self.assertIn('t=get_steptime()',script)
                self.assertIn('web_clock - web_grant_at < 0.12',script)
                self.assertNotIn('began',script)
                self.assertIn('while web_alive and web_granted and web_clock - web_grant_at < 0.12:',script)
                self.assertIn('global web_grant_at = requested_at',script)
                self.assertIn('reply[1] != 123456',script)
                self.assertIn('stopj(0.5)',script)
                self.assertLess(script.index('stopj'),script.index('kill reader'))
        for bad in ((6,1),(0,0),(True,1)):
            with self.assertRaises(ValueError): continuous_script(*bad,'192.168.10.1',50001,123)
        with self.assertRaises(ValueError): continuous_script(0,1,'invalid',50001,123)

    def test_cartesian_requires_matching_tcp_ack_and_rejects_changed_tcp(self):
        self.r.tcp_pose=[.1,.2,.3,0,0,0]
        self.r.tcp_offset=[0.,0.,0.,0.,0.,0.]
        with self.assertRaises(ValueError): self.r.arm('tab1','cartesian')
        with self.assertRaises(ValueError): self.r.arm('tab1','cartesian',True,[0,0,.2,0,0,0])
        self.r.arm('tab1','cartesian',True,[0]*6)
        with self.assertRaises(ValueError): self.r.hold('tab1',1,'p',0,1,True,'joint')
        self.r.hold('tab1',1,'p',0,1,True,'cartesian')
        self.assertTrue(self.r.eligible())
        self.r.tcp_offset[2]=.2
        self.assertFalse(self.r.eligible())
        with self.assertRaises(ValueError): self.r.hold('tab1',2,'p',0,1,False,'cartesian')
        self.assertIsNone(self.r.owner)

    def test_cartesian_script_speeds_and_stop_use_linear_control(self):
        import ast
        import math
        import re
        for axis in range(6):
            for sign in (-1,1):
                script=continuous_script(axis,sign,'192.168.10.1',50001,123,'cartesian').decode()
                v=ast.literal_eval(re.search(r'speedl\((\[.*?\])',script).group(1))
                self.assertEqual(sum(x!=0 for x in v),1)
                self.assertAlmostEqual(v[axis],sign*(.005 if axis<3 else math.radians(1)))
                self.assertIn('stopl(0.1, aRot=0.5)',script)
                self.assertNotIn('speedj(',script)
                self.assertIn('web_clock - web_grant_at < 0.12',script)
        with self.assertRaises(ValueError): continuous_script(0,1,'192.168.10.1',50001,123,'tool')

    def test_joint_and_tcp_are_parsed_from_the_same_state_packet(self):
        import math
        joint=struct.pack('!IB',251,1)+b''.join(struct.pack('!d',i/10)+b'\0'*33 for i in range(6))
        pose=[.1,.2,.3,1.,2.,3.]
        tcp=[0,0,.2,0,0,.5]
        cartesian=struct.pack('!IB12d',101,4,*(pose+tcp))
        result=parse_robot_state(joint+cartesian)
        self.assertAlmostEqual(result['joints'][5],math.degrees(.5))
        self.assertEqual(result['tcp_pose'],pose)
        self.assertEqual(result['tcp_offset'],tcp)
        with self.assertRaises(ValueError): parse_robot_state(joint+cartesian[:-1])
        bad=struct.pack('!IB12d',101,4,*([float('nan')]+[0]*11))
        with self.assertRaises(ValueError): parse_robot_state(bad)

    def test_stop_reason_survives_a_delayed_generic_stop(self):
        self.arm_start()
        initial=self.r.control_revision
        self.r.revoke('連線錯誤：test')
        self.r.revoke('已停止')
        self.assertEqual(self.r.snapshot()['last_stop_reason'],'連線錯誤：test')
        self.assertGreater(self.r.control_revision,initial)


if __name__ == '__main__': unittest.main()
