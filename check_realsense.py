"""Capture real color/depth frames. No robot commands; stop pipeline on exit."""
import json
import pathlib
import time
import pyrealsense2 as rs

out = pathlib.Path(__file__).resolve().parent / 'realsense_check'
out.mkdir(exist_ok=True)
report = {'success': False}
print('正在列舉 SDK 裝置…', flush=True)
context = rs.context()
devices = context.query_devices()
print('SDK 裝置數量：', len(devices), flush=True)
pipeline = rs.pipeline(context)
started = False
try:
    config = rs.config()
    if len(devices) != 1:
        raise RuntimeError('預期一台相機，SDK 偵測到 %d 台' % len(devices))
    selected = devices[0]
    sdk_serial = selected.get_info(rs.camera_info.serial_number)
    print('SDK 序號：', sdk_serial, flush=True)
    print('SDK 名稱：', selected.get_info(rs.camera_info.name), flush=True)
    config.enable_device(sdk_serial)
    print('正在啟動彩色與深度串流…', flush=True)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
    profile = pipeline.start(config)
    started = True
    device = profile.get_device()
    report['name'] = device.get_info(rs.camera_info.name)
    report['serial'] = device.get_info(rs.camera_info.serial_number)
    counts = {'depth': [], 'color': []}
    begin = time.monotonic()
    last = None
    for i in range(60):
        frames = pipeline.wait_for_frames(5000)
        depth, color = frames.get_depth_frame(), frames.get_color_frame()
        if not depth or not color:
            raise RuntimeError('Missing color or depth frame')
        counts['depth'].append(depth.get_frame_number())
        counts['color'].append(color.get_frame_number())
        last = (depth, color)
    depth, color = last
    raw = memoryview(depth.get_data()).tobytes()
    samples = memoryview(raw).cast('H')
    valid = sum(v > 0 for v in samples)
    report.update({'frames_received': 60, 'elapsed_seconds': time.monotonic()-begin,
                   'resolution': [640,480], 'depth_valid_fraction': valid/len(samples),
                   'depth_scale_m': device.first_depth_sensor().get_depth_scale(),
                   'center_depth_m': depth.get_distance(320,240),
                   'unique_depth_frames': len(set(counts['depth'])),
                   'unique_color_frames': len(set(counts['color']))})
    (out/'color.ppm').write_bytes(b'P6\n640 480\n255\n'+memoryview(color.get_data()).tobytes())
    (out/'depth_z16.raw').write_bytes(raw)
    colored = rs.colorizer().colorize(depth)
    (out/'depth_preview.ppm').write_bytes(b'P6\n640 480\n255\n'+memoryview(colored.get_data()).tobytes())
    report['success'] = valid > 0 and len(set(counts['depth'])) > 1 and len(set(counts['color'])) > 1
except Exception as exc:
    report['error'] = str(exc)
finally:
    if started:
        pipeline.stop()
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
