# -*- coding: utf-8 -*-
"""麟的音乐播放器 · 回收站（软删除缓存）。

从「全部音乐 / 内置原声 / 我的导入」里移出的歌先丢进这里，不会立刻丢：

- **恢复**：原样回到曲库，文件从来没动过（原来被哪些分类夹收着也一起放回去）；
- **彻底删除**：真的删掉——「我的导入」的歌连本地文件一起删；内置原声属于
  曲库包（exe 旁 music\原声\，或老版本内嵌在 exe 里），不替用户删文件，只记进
  屏蔽表，永远不再出现在曲库（重启也不会回来）；想找回来得清掉 trash.json
  里的屏蔽记录（换个文件夹/换台电脑则天然恢复）。

数据落在 exe 旁边的 playlist/trash.json，跟便携包一起走，换 exe 也不丢。
纯 Python、不依赖 Qt，可以单独跑 python trash.py 自测。
"""

import os
import time

from playlists import playlist_dir, track_key, split_key

TRASH_SCOPE = "sys_trash"          # 界面上的虚拟分类（不进 playlists.json）
TRASH_NAME = "回收站"
TRASH_VERSION = 1


def trash_path():
    return os.path.join(playlist_dir(), "trash.json")


class TrashStore(object):
    """回收站：items = 待恢复的歌，blocked = 彻底删掉后不许再出现的键。"""

    def __init__(self, path=None):
        self.path = path or trash_path()
        self.items = []
        self.blocked = []
        self._hid = None            # 隐藏键缓存
        self._hidt = None           # 隐藏曲名缓存（兼容旧来源标签）
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
        blocked = []
        if isinstance(data, dict):
            items = data.get("items") or []
            blocked = data.get("blocked") or []
        elif isinstance(data, list):        # 宽容：手写成裸数组也能读
            items = data
        clean = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            key = str(it.get("key") or "").strip()
            if not key:
                continue
            src, title = split_key(key)
            src = str(it.get("source") or src)
            title = str(it.get("title") or title)
            pls = it.get("from_playlists")
            pls = [str(p) for p in pls if isinstance(p, str)] if isinstance(pls, list) else []
            clean.append({
                "key": key,
                "source": src,
                "title": title,
                "path": str(it.get("path") or ""),
                "removed_at": int(it.get("removed_at") or 0),
                "from_playlists": pls,
            })
        self.items = clean
        self.blocked = [str(k) for k in (blocked if isinstance(blocked, list) else [])
                        if isinstance(k, str)]
        self._hid = None
        self._hidt = None
        return self.items

    def save(self):
        import json
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {"version": TRASH_VERSION, "items": self.items,
                       "blocked": self.blocked}
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print("[trash] 保存失败:", e)
            return False

    # ---- 查询 -----------------------------------------------------------
    def count(self):
        return len(self.items)

    def keys(self):
        return [d["key"] for d in self.items]

    def by_key(self, key):
        for d in self.items:
            if d["key"] == key:
                return d
        return None

    def tracks(self):
        """给界面显示用：[(path, title, source)]（文件没了也能列出来）。"""
        return [(d.get("path") or "", d.get("title") or "", d.get("source") or "")
                for d in self.items]

    def hidden_key_set(self):
        if self._hid is None:
            self._hid = set(d["key"] for d in self.items) | set(self.blocked)
        return self._hid

    def hidden_titles(self):
        if self._hidt is None:
            self._hidt = set(split_key(k)[1] for k in self.hidden_key_set())
        return self._hidt

    def is_hidden(self, source, title):
        """这首现在该不该从曲库里藏起来（在回收站里 / 已被彻底删掉）。"""
        if track_key(source, title) in self.hidden_key_set():
            return True
        return title in self.hidden_titles()

    # ---- 增删 -----------------------------------------------------------
    def add(self, entries):
        """把若干首丢进回收站；entries 每项 {source,title,path,from_playlists}。

        已经在回收站里的同一首不会重复，只更新路径/时间/原分类夹。
        返回本次真正收下的歌数（去重后）。
        """
        if not entries:
            return 0
        by_key = {d["key"]: d for d in self.items}
        now = int(time.time())
        seen = set()
        for e in entries:
            if not isinstance(e, dict):
                continue
            src = str(e.get("source") or "")
            title = str(e.get("title") or "")
            if not title:
                continue
            k = track_key(src, title)
            if k in seen:
                continue
            seen.add(k)
            d = by_key.get(k)
            if d is None:
                d = {"key": k, "source": src, "title": title,
                     "path": str(e.get("path") or ""), "removed_at": now,
                     "from_playlists": []}
                self.items.append(d)
                by_key[k] = d
            else:
                d["removed_at"] = now
                if e.get("path"):
                    d["path"] = str(e["path"])
            for pid in e.get("from_playlists") or []:
                if pid and str(pid) not in d["from_playlists"]:
                    d["from_playlists"].append(str(pid))
        self._hid = None
        self._hidt = None
        self.save()
        return len(seen)

    def restore(self, keys):
        """恢复若干首（从回收站拿出来），返回被恢复的条目列表。"""
        drop = set(str(k) for k in (keys or []))
        out = [d for d in self.items if d["key"] in drop]
        if out:
            self.items = [d for d in self.items if d["key"] not in drop]
            self._hid = None
            self._hidt = None
            self.save()
        return out

    def forget(self, keys, blocked_keys=()):
        """把若干首从回收站彻底清掉；blocked_keys 里的键以后永远不再出现。"""
        drop = set(str(k) for k in (keys or []))
        out = [d for d in self.items if d["key"] in drop]
        self.items = [d for d in self.items if d["key"] not in drop]
        added = False
        for k in blocked_keys or []:
            k = str(k)
            if k and k not in self.blocked:
                self.blocked.append(k)
                added = True
        self._hid = None
        self._hidt = None
        if out or added:
            self.save()
        return out

    def clear(self):
        """清空回收站（彻底删除全部，含屏蔽表）。"""
        self.items = []
        self.blocked = []
        self._hid = None
        self._hidt = None
        self.save()


# ---- 全局单例：播放器扫描曲库时也要问一句「这首是不是已经不要了」 ---------
_STORE = None


def get_store():
    global _STORE
    if _STORE is None:
        _STORE = TrashStore()
    return _STORE


def reset_store():
    """测试用：丢掉缓存，下次 get_store() 按当前 data_dir() 重新读盘。"""
    global _STORE
    _STORE = None


def is_hidden(source, title):
    """给 music_folder.scan_tracks 用的过滤钩子（回收站里的歌不进曲库）。"""
    try:
        return get_store().is_hidden(source, title)
    except Exception:
        return False


def _self_test():
    import json
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="lin_trash_")
    tmp = os.path.join(tmp_dir, "trash.json")
    st = TrashStore(tmp)
    ext = os.path.join(tmp_dir, "music")
    os.makedirs(ext)
    # 造一个真实的「导入文件」，验证彻底删除文件是界面层的事，数据层不动文件
    fake = os.path.join(ext, "测试曲.mp3")
    with open(fake, "wb") as fh:
        fh.write(b"\x00" * 128)
    n = st.add([
        {"source": "本地", "title": "测试曲", "path": fake, "from_playlists": ["pl_a"]},
        {"source": "内置", "title": "内置曲", "path": "C:/fake/x.mp3"},
        {"source": "本地", "title": "测试曲", "path": fake},        # 重复
    ])
    assert n == 2, n
    assert st.count() == 2
    assert st.is_hidden("本地", "测试曲") and st.is_hidden("内置", "内置曲")
    assert not st.is_hidden("本地", "别的歌")
    assert st.is_hidden("本地", "内置曲"), "来源标签错了也要按曲名认出"
    st2 = TrashStore(tmp)                                          # 持久化
    assert st2.count() == 2 and st2.by_key("本地|测试曲")["from_playlists"] == ["pl_a"]
    back = st2.restore(["本地|测试曲"])
    assert back and back[0]["from_playlists"] == ["pl_a"]
    assert not st2.is_hidden("本地", "测试曲")
    st2.forget(["内置|内置曲"], blocked_keys=["内置|内置曲"])
    assert st2.count() == 0 and st2.is_hidden("内置", "内置曲")
    assert os.path.isfile(fake), "数据层不许动文件"
    # 坏文件 / 裸数组容错
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert TrashStore(tmp).count() == 0
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump([{"key": "本地|裸数组", "title": "裸数组"}], fh)
    assert TrashStore(tmp).count() == 1
    print("trash self-test OK:", tmp_dir)


if __name__ == "__main__":
    _self_test()
