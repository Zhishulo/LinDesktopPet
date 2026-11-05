# -*- coding: utf-8 -*-
"""麟的音乐播放器 · 分类收藏夹（歌单）存储。

对标网易云「收藏歌单」：用户自建分类夹、往里面收纳歌、拖拽排序，
数据落在 **exe 旁边的 playlist/playlists.json**（源码模式就是源码目录），
跟着便携包一起走，换 exe 不丢，也绝不碰 music/ 里的音频文件。

- 系统分类（全部音乐 / 内置原声 / 我的导入）是虚拟的，不落盘；
- 「我喜欢的音乐」是内置的固定分类夹（id=pl_fav）：点心心就收藏，不可删除，
  也不参与自建分类夹的拖动排序，永远排在系统分类下面；
- 自建分类夹用稳定键记录歌曲：「来源|曲名」，例如 "本地|雾之森"、"内置|雪路"。
  内置曲在打包后解压在临时 _MEIPASS 目录，路径每次都变，所以只能按来源+曲名认；
- 文件里预留 cover_path / tags / sort_order 字段，方便以后加封面和标签。

对外 API：
    SYSTEM_SCOPES               系统分类 [(id, 名称), ...]
    track_key(source, title)    -> "本地|雾之森"
    split_key(key)              -> ("本地", "雾之森")
    PlaylistStore               纯 Python、不依赖 Qt（可单独跑测试）
        playlists / by_id / keys / counts
        create / rename / delete / clear
        add_tracks / remove_tracks / set_tracks / set_order / move
        containing(key)         哪些分类夹里有这首
"""

import os
import time

from data import data_dir


# 系统分类：界面置顶、不可删改（符号常量，界面和数据层共用）
SYS_ALL = "sys_all"
SYS_BUILTIN = "sys_builtin"
SYS_EXTERNAL = "sys_external"
SYSTEM_SCOPES = [
    (SYS_ALL, "全部音乐"),
    (SYS_BUILTIN, "内置原声"),
    (SYS_EXTERNAL, "我的导入"),
]
FAV_ID = "pl_fav"              # 「我喜欢的音乐」固定 id（系统内置收藏夹）
FAV_NAME = "我喜欢的音乐"
NAME_MAX = 20          # 分类夹名长度上限（需求 3.1.1）
STORE_VERSION = 1


def playlist_dir():
    """分类夹数据目录：data_dir()/playlist（与 exe 同级，便携）。"""
    return os.path.join(data_dir(), "playlist")


def store_path():
    return os.path.join(playlist_dir(), "playlists.json")


def track_key(source, title):
    """歌曲的稳定键：来源+曲名（内置曲的绝对路径每次启动都会变，不能用路径）。"""
    return "{}|{}".format(source or "", title or "")


def split_key(key):
    """'本地|雾之森' -> ('本地', '雾之森')；没有分隔符时当作「本地」。"""
    key = str(key or "")
    if "|" in key:
        src, title = key.split("|", 1)
        return src, title
    return "本地", key


class PlaylistStore(object):
    """分类夹集合。所有改动立即落盘（写失败也不抛，界面照样能用）。"""

    def __init__(self, path=None):
        self.path = path or store_path()
        self.playlists = []
        self.load()

    # ---- 落盘 -----------------------------------------------------------
    def load(self):
        import json
        data = None
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
        except Exception:
            data = None
        items = []
        if isinstance(data, dict):
            items = data.get("playlists") or []
        elif isinstance(data, list):        # 宽容：有人手写成裸数组也能读
            items = data
        clean = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("playlist_id") or "").strip()
            name = str(it.get("playlist_name") or "").strip()
            if not pid or not name:
                continue
            keys = it.get("music_list")
            keys = [str(k) for k in keys if isinstance(k, str)] if isinstance(keys, list) else []
            clean.append({
                "playlist_id": pid,
                "playlist_name": name[:NAME_MAX],
                "create_time": int(it.get("create_time") or 0),
                "cover_path": str(it.get("cover_path") or ""),
                "tags": list(it.get("tags") or []),
                "sort_order": int(it.get("sort_order") or 0),
                "music_list": keys,
            })
        clean.sort(key=lambda d: (d.get("sort_order", 0), d.get("create_time", 0)))
        for i, d in enumerate(clean):
            d["sort_order"] = i
        self.playlists = clean
        return self.playlists

    def save(self):
        import json
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {"version": STORE_VERSION, "playlists": self.playlists}
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print("[playlists] 保存失败:", e)
            return False

    # ---- 查询 -----------------------------------------------------------
    def ensure_favorites(self):
        """确保「我喜欢的音乐」存在（老版本升级上来没有就补一个），返回它。"""
        d = self.by_id(FAV_ID)
        if d is None:
            d = {
                "playlist_id": FAV_ID,
                "playlist_name": FAV_NAME,
                "create_time": int(time.time()),
                "cover_path": "",
                "tags": [],
                "sort_order": -1,
                "music_list": [],
            }
            self.playlists.insert(0, d)
            self._resort()
            self.save()
        return d

    def is_favorites(self, pid):
        return pid == FAV_ID

    def by_id(self, pid):
        for d in self.playlists:
            if d["playlist_id"] == pid:
                return d
        return None

    def name_of(self, pid):
        d = self.by_id(pid)
        return d["playlist_name"] if d else ""

    def keys(self, pid):
        d = self.by_id(pid)
        return list(d["music_list"]) if d else []

    def counts(self):
        return {d["playlist_id"]: len(d["music_list"]) for d in self.playlists}

    def containing(self, key):
        """哪些自建分类夹里已经有这首。"""
        return [d["playlist_id"] for d in self.playlists if key in d["music_list"]]

    # ---- 分类夹增删改 ---------------------------------------------------
    def _new_id(self):
        base = "pl_%d" % int(time.time() * 1000)
        pid, n = base, 1
        while self.by_id(pid):
            n += 1
            pid = "%s_%d" % (base, n)
        return pid

    def create(self, name):
        """新建分类夹（空歌单）。名字去空白、截断到 20 字；空名字返回 None。"""
        name = str(name or "").strip()
        if not name:
            return None
        d = {
            "playlist_id": self._new_id(),
            "playlist_name": name[:NAME_MAX],
            "create_time": int(time.time()),
            "cover_path": "",
            "tags": [],
            "sort_order": len(self.playlists),
            "music_list": [],
        }
        self.playlists.append(d)
        self.save()
        return d

    def rename(self, pid, name):
        name = str(name or "").strip()
        d = self.by_id(pid)
        if not d or not name:
            return False
        d["playlist_name"] = name[:NAME_MAX]
        self.save()
        return True

    def delete(self, pid):
        """删分类夹：只解除收纳关系，绝不碰本地音乐文件（收藏夹不许删）。"""
        if self.is_favorites(pid):
            return False
        d = self.by_id(pid)
        if not d:
            return False
        self.playlists.remove(d)
        self._resort()
        self.save()
        return True

    def clear(self, pid):
        d = self.by_id(pid)
        if not d:
            return False
        d["music_list"] = []
        self.save()
        return True

    def _resort(self):
        for i, d in enumerate(self.playlists):
            d["sort_order"] = i

    def set_order(self, pid_order):
        """按给定的 id 顺序重排分类夹（拖拽排序用）；没提到的保持在后。

        「我喜欢的音乐」位置固定在最前，不参与排序。
        """
        order = [p for p in (pid_order or []) if self.by_id(p) and not self.is_favorites(p)]
        seen = set(order)
        rest = [d["playlist_id"] for d in self.playlists if d["playlist_id"] not in seen]
        by = {d["playlist_id"]: d for d in self.playlists}
        self.playlists = [by[p] for p in order + rest]
        self._resort()
        self.save()
        return True

    def move(self, pid, delta):
        """把分类夹上移/下移一格（右键菜单备用）。"""
        try:
            i = [d["playlist_id"] for d in self.playlists].index(pid)
        except ValueError:
            return False
        j = max(0, min(len(self.playlists) - 1, i + int(delta)))
        if i == j:
            return False
        self.playlists.insert(j, self.playlists.pop(i))
        self._resort()
        self.save()
        return True

    # ---- 歌曲收纳 -------------------------------------------------------
    def add_tracks(self, pid, keys):
        """把若干首加进分类夹（已存在的不重复），返回本次新增数量。"""
        d = self.by_id(pid)
        if not d:
            return 0
        n = 0
        for k in keys or []:
            k = str(k)
            if k and k not in d["music_list"]:
                d["music_list"].append(k)
                n += 1
        if n:
            self.save()
        return n

    def remove_tracks(self, pid, keys):
        """从分类夹移除若干首（不影响其他分类夹、不删文件），返回移除数量。"""
        d = self.by_id(pid)
        if not d:
            return 0
        drop = set(str(k) for k in (keys or []))
        before = len(d["music_list"])
        d["music_list"] = [k for k in d["music_list"] if k not in drop]
        n = before - len(d["music_list"])
        if n:
            self.save()
        return n

    def set_tracks(self, pid, keys):
        """直接把分类夹歌曲顺序替换成 keys（拖拽重排后调用）。"""
        d = self.by_id(pid)
        if not d:
            return False
        seen, out = set(), []
        for k in keys or []:
            k = str(k)
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        d["music_list"] = out
        self.save()
        return True


def _self_test():
    """自测：建/改名/加歌/删歌/排序/删夹，全部走临时文件。"""
    import json
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="lin_pl_"), "playlists.json")
    st = PlaylistStore(tmp)
    a = st.create("轻音乐收藏")
    b = st.create("睡前")
    assert a and b, "create 失败"
    assert st.create("   ") is None, "空名字应当被拒绝"
    assert len(st.create("x" * 40)["playlist_name"]) == NAME_MAX, "名字应截断到 20 字"
    st.playlists = st.playlists[:2]
    st.save()
    st.add_tracks(a["playlist_id"], [track_key("本地", "雾之森"), track_key("内置", "雪路")])
    st.add_tracks(a["playlist_id"], [track_key("本地", "雾之森")])     # 去重
    assert st.keys(a["playlist_id"]) == ["本地|雾之森", "内置|雪路"], st.keys(a["playlist_id"])
    assert st.containing("本地|雾之森") == [a["playlist_id"]]
    st.remove_tracks(a["playlist_id"], ["内置|雪路"])
    assert st.keys(a["playlist_id"]) == ["本地|雾之森"]
    st.rename(a["playlist_id"], "纯音乐")
    st.set_order([b["playlist_id"], a["playlist_id"]])
    st2 = PlaylistStore(tmp)          # 重新读盘，验证持久化
    assert [d["playlist_name"] for d in st2.playlists] == ["睡前", "纯音乐"], \
        [d["playlist_name"] for d in st2.playlists]
    assert st2.keys(a["playlist_id"]) == ["本地|雾之森"]
    assert st2.delete(a["playlist_id"]) is True
    assert PlaylistStore(tmp).by_id(a["playlist_id"]) is None
    print("playlists self-test OK:", tmp)
    print(json.dumps(PlaylistStore(tmp).playlists, ensure_ascii=False))


if __name__ == "__main__":
    _self_test()
