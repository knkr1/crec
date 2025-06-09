class ProgressHandler:
    def __init__(self):
        self.download_progress = 0
        self.processing_progress = 0

    def download_progress_hook(self, d):
        if d['status'] == 'downloading':
            self.download_progress = d.get('_percent_str', '0%')
        elif d['status'] == 'finished':
            self.download_progress = '100%'

    def processing_progress_hook(self, d):
        if d['status'] == 'started':
            self.processing_progress = 0
        elif d['status'] == 'processing':
            self.processing_progress = d.get('_percent_str', '0%')
        elif d['status'] == 'finished':
            self.processing_progress = '100%' 