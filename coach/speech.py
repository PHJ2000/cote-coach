import threading
from pathlib import Path

class Speech:
    def __init__(self,root):self.root=root;self.model=None;self.lock=threading.Lock()
    def transcribe(self,path):
        with self.lock:
            from faster_whisper import WhisperModel
            if self.model is None:self.model=WhisperModel('small',device='cpu',compute_type='int8',download_root=str(self.root/'runtime/whisper'),cpu_threads=8,local_files_only=True)
            segments,info=self.model.transcribe(str(path),language='ko',beam_size=5,vad_filter=True)
            return [{'start_ms':round(s.start*1000),'end_ms':round(s.end*1000),'text':s.text.strip()} for s in segments]

def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()
