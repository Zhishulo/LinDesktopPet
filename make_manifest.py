# -*- coding: utf-8 -*-
r"""生成内置曲目清单 builtin_tracks.json（打包用）。

第 17 轮起曲库不再打进 exe：exe 里只带这份清单（决定「哪些文件算内置」），
真正的音频由用户把「原声曲库.zip」解压到 exe 旁的 music\原声\。

用法：
    C:\LinVenv\Scripts\python.exe make_manifest.py [曲库目录]
默认曲库目录 = 本文件同级的 music 文件夹（源码模式下就是 78 首原声）；
输出固定写在本文件同级目录的 builtin_tracks.json。
"""
import json
import os
import sys

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"}


def collect(src):
    names = [
        f for f in os.listdir(src)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTS
    ]
    return sorted(names, key=lambda s: s.casefold())


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "music")
    if not os.path.isdir(src):
        print("music dir not found: %s" % src)
        return 1
    tracks = collect(src)
    out = os.path.join(here, "builtin_tracks.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"version": 1, "tracks": tracks}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("tracks=%d -> %s" % (len(tracks), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
