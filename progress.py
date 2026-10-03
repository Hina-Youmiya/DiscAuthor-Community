"""Read encoder-reported work, without estimating progress from elapsed time."""
import math
import re


class EncoderProgress:
    def __init__(self, duration):
        self.duration = max(0.001, float(duration))
        self.values = {}
        self.seconds = 0.0

    def feed(self, line):
        key, separator, value = line.strip().partition('=')
        if not separator:
            return None
        self.values[key] = value
        if key != 'progress':
            return None
        seconds = None
        # FFmpeg's legacy out_time_ms is also expressed in microseconds.
        for name in ('out_time_us', 'out_time_ms'):
            try:
                seconds = float(self.values[name]) / 1_000_000
                if math.isfinite(seconds):
                    break
            except (KeyError, ValueError):
                pass
            seconds = None
        if seconds is None:
            try:
                hours, minutes, sec = self.values['out_time'].split(':')
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(sec)
            except (KeyError, ValueError):
                return None
        if not math.isfinite(seconds):
            return None
        self.seconds = min(self.duration, max(self.seconds, seconds, 0.0))
        try:
            speed = float(self.values.get('speed', '').rstrip('x'))
            if not math.isfinite(speed) or speed < 0:
                speed = None
        except ValueError:
            speed = None
        # A progress=end record is not success; the process still has to exit 0.
        return min(.999, self.seconds / self.duration), self.seconds, speed


def tool_percentage(line):
    match = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)%\s+(?:complete|done)\b', line)
    return min(.999, max(0, float(match[1]) / 100)) if match else None


def programme_range(videos, index, start=10.0, end=85.0):
    durations = [max(.001, float(video['info']['duration'])) for video in videos]
    total = sum(durations)
    previous = sum(durations[:index])
    return (start + (end-start) * previous / total,
            start + (end-start) * (previous + durations[index]) / total)
