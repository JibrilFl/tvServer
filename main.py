import os
import subprocess
import shutil
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8888
VIDEO_DIR = "videos"
STREAM_DIR = "stream"
MONOLITH_FILE = "full_tv_loop.mp4"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>TV Live Broadcast</title>
    <style>
        html, body { margin: 0; padding: 0; width: 100%; height: 100%; background-color: black; overflow: hidden; }
        video { width: 100%; height: 100%; }
    </style>
</head>
<body>
    <video id="videoPlayer" src="/stream/live.m3u8" autoplay controls muted playsinline></video>
    <script>
        var player = document.getElementById('videoPlayer');
        document.body.onclick = function() {
            if (player.muted) player.muted = false;
        };
        player.onerror = function() {
            setTimeout(function() {
                player.load();
                player.play();
            }, 3000);
        };
    </script>
</body>
</html>
"""


def prepare_monolith():
    """Собирает ВСЕ видео в один файл через неубиваемый фильтр сложных потоков"""
    valid_extensions = ('.mp4', '.webm', '.ogg', '.mkv', '.avi')
    if not os.path.exists(VIDEO_DIR):
        os.makedirs(VIDEO_DIR)

    videos = [f for f in sorted(os.listdir(VIDEO_DIR)) if os.path.splitext(f)[1].lower() in valid_extensions]

    if not videos:
        print(f"![ОШИБКА] Положите видеофайлы в папку '{VIDEO_DIR}'!")
        sys.exit(1)

    if os.path.exists(MONOLITH_FILE):
        print(f"[Сервер] Найдена готовая сборка {MONOLITH_FILE}. Пропускаем монтаж.")
        return

    print(f"[Сервер] Найдено видеофайлов: {len(videos)}. Начинаем жесткую фоновую сборку...")

    # Строим команду фильтрации: сшиваем видео- и аудио-потоки поштучно
    inputs = []
    filter_complex = ""
    for i, v in enumerate(videos):
        inputs.extend(['-i', os.path.abspath(os.path.join(VIDEO_DIR, v))])
        # Приводим каждый кусок к единой сетке фильтрами, чтобы убрать ошибки NAL, разрешения и кодеков
        filter_complex += f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"

    # Склеиваем подготовленные дорожки в один ряд
    for i in range(len(videos)):
        filter_complex += f"[v{i}][{i}:a]"
    filter_complex += f"concat=n={len(videos)}:v=1:a=1[v_out][a_out]"

    render_cmd = [
                     'ffmpeg', '-y'
                 ] + inputs + [
                     '-filter_complex', filter_complex,
                     '-map', '[v_out]', '-map', '[a_out]',
                     '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',  # Быстрый рендер для сборки
                     '-r', '25', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
                     MONOLITH_FILE
                 ]

    start_time = time.time()
    # Запускаем сборку. Она займет время, но сделает монолит, идеальный для HLS
    process = subprocess.Popen(render_cmd, stdout=subprocess.DEVNULL, stderr=sys.stderr)
    process.wait()

    if process.returncode == 0:
        print(f"[Сервер] Сборка успешно завершена за {int(time.time() - start_time)} сек!")
    else:
        print("![ОШИБКА] Не удалось смонтировать монолитное видео.")
        sys.exit(1)


def start_ffmpeg_stream():
    """Запускает вещание готового монолита. Нагрузка на ЦП теперь равна 0%"""
    if os.path.exists(STREAM_DIR):
        shutil.rmtree(STREAM_DIR)
    os.makedirs(STREAM_DIR)

    cmd = [
        'ffmpeg',
        '-re',
        '-stream_loop', '-1',
        '-i', MONOLITH_FILE,
        '-c', 'copy',  # ПРОСТО КОПИРУЕМ ПОТОК без нагрузки на сервер!
        '-f', 'hls',
        '-hls_time', '4',
        '-hls_list_size', '5',
        '-hls_flags', 'delete_segments+independent_segments',
        os.path.join(STREAM_DIR, 'live.m3u8')
    ]

    print("[Сервер] Запуск бесконечного вещания готового файла...")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=sys.stderr)


class LiveTVHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        clean_path = self.path.split('?')[0]

        if clean_path == '/' or clean_path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))

        elif clean_path.startswith('/stream/'):
            self.directory = os.getcwd()
            try:
                super().do_GET()
            except (ConnectionResetError, BrokenPipeError):
                pass
        else:
            self.send_error(404, "Not found")

    def end_headers(self):
        if "live.m3u8" in self.path:
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()


if __name__ == '__main__':
    ffmpeg_process = None
    try:
        # 1. Сначала готовим неубиваемый монолит из всех видео
        prepare_monolith()

        # 2. Только потом запускаем легкий стриминг
        ffmpeg_process = start_ffmpeg_stream()

        server_address = ('', PORT)
        httpd = ThreadingHTTPServer(server_address, LiveTVHandler)
        print(f"[УСПЕХ] Сервер трансляции запущен на порту {PORT}!")
        httpd.serve_forever()

    except KeyboardInterrupt:
        print("\nОстановка сервера...")
    finally:
        if ffmpeg_process:
            ffmpeg_process.terminate()