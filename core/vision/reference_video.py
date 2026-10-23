"""Optional reference-video decoding at the H3 frame rate."""
from ..errors import require


def decode_reference_video(path, paired_audio=False):
    import av
    import numpy as np
    import torch
    frames = []
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        require(stream is not None, "VIDEO_STREAM", path, "No video stream")
        rate = stream.average_rate
        require(rate is not None and rate > 0, "VIDEO_FPS", path, "Unknown reference FPS")
        first, next_index, previous, previous_time = None, 0, None, 0.0
        for index, frame in enumerate(container.decode(stream)):
            moment = float(frame.time) if frame.time is not None else index / float(rate)
            if first is None:
                first = moment
            moment -= first
            current = frame.to_ndarray(format="rgb24")
            while next_index < 360 and next_index / 24 <= moment + 1e-8:
                frames.append(current if previous is None or abs(next_index / 24 - moment) < 1e-8 else previous)
                next_index += 1
            previous, previous_time = current, moment
            if next_index == 360:
                break
        # Cover the final source frame's interval, including low-FPS references.
        while previous is not None and next_index < 360 and next_index / 24 < previous_time + 1 / float(rate) - 1e-8:
            frames.append(previous)
            next_index += 1
    require(len(frames) >= 5, "VIDEO_LENGTH", path, "Reference needs at least five frames at 24 FPS")
    images = torch.from_numpy(np.stack(frames).astype(np.float32) / 255.0)
    audio = None
    if paired_audio:
        with av.open(path) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            require(stream is not None, "VIDEO_AUDIO", path, "Paired audio enabled but no audio stream")
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
            samples = []
            for frame in container.decode(stream):
                samples.extend(out.to_ndarray() for out in resampler.resample(frame))
                if sum(s.shape[-1] for s in samples) >= len(frames) * 2000:
                    break
            samples.extend(out.to_ndarray() for out in resampler.resample(None))
            require(bool(samples), "VIDEO_AUDIO", path, "Empty paired audio")
            pcm = np.concatenate(samples, axis=-1)[:, :len(frames) * 2000]
            audio = dict(waveform=torch.from_numpy(pcm).unsqueeze(0), sample_rate=48000)
    return images, audio
