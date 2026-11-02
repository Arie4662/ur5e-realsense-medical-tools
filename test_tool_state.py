import math
import unittest
from unittest.mock import patch
from tool_state import flange_xyz, GripperState


class ToolTests(unittest.TestCase):
    def test_flange_removes_translated_and_rotated_tcp_offset(self):
        self.assertEqual(flange_xyz([1,2,3,0,0,0],[0]*6),[1,2,3])
        # A flange rotated 90 degrees about Z carries an X-offset along base Y.
        result=flange_xyz([1,2.2,3,0,0,math.pi/2],[.2,0,0,0,0,0])
        for a,b in zip(result,[1,2,3]):self.assertAlmostEqual(a,b)
        # The TCP rotation itself must also be removed before applying offset.
        result=flange_xyz([1.2,2,3,0,0,math.pi/2],[.2,0,0,0,0,math.pi/2])
        for a,b in zip(result,[1,2,3]):self.assertAlmostEqual(a,b)
        self.assertIsNone(flange_xyz(None,None))

    def test_fragmented_gripper_position_is_not_truncated_and_only_gets_are_sent(self):
        responses=iter([b'POS 2',b'05\n',b'STA 3\n',b'OBJ 0\n',b'FLT 00\n'])
        with patch('tool_state.socket.create_connection') as connect:
            sock=connect.return_value.__enter__.return_value
            sock.recv.side_effect=lambda n:next(responses)
            result=GripperState('unused').read()
            self.assertEqual(result['position_raw'],205)
            self.assertIsNone(result['width_mm'])
            self.assertEqual([x.args[0] for x in sock.sendall.call_args_list],
                             [b'GET POS\n',b'GET STA\n',b'GET OBJ\n',b'GET FLT\n'])


if __name__=='__main__':unittest.main()
