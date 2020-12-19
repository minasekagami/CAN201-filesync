import difflib
import socket
import threading
from asys import logger, cfg, db
import struct
import json
import asysio
import math
import os
import time
import asystp
import gzip

"""
sync_files() -> new_files, deleted_files, mod_files:
通过遍历sync文件夹来找到new_files, deleted_files, mod_files, 返回的是str

save_files_list(new_files: set, deleted_files: set, modified_files: set):
通过sync_files()的返回值来持续化到db.json中

check_files():
整个filesystem的入口
"""


def sync_files() -> tuple:
    """return new files and deleted files
    """
    # 需要同步的文件, SyncFile对象的 list
    sync_objs = db["sync_files"]
    # receive files
    # receive 的 set 是之有文件名的 set
    rev_files = set(db["recv_files"])
    # current exist files
    cur_files = set()
    # past exist files
    # 这个set是原来的所有, 也是原来所有的文件
    ori_files = set()
    # modified files 修改的文件
    mod_files = set()
    # ignored files 忽略的文件
    ign_files = set(db["ignore"])
    # 本地文件的 name
    sync_files = set()

    # 把对象的 name 用 str 的形式放进去
    for i in sync_objs:
        sync_files.add(i["name"])

    # 原来的所有文件 = 接收到的所有文件 + 本地的所有文件
    ori_files.update(rev_files)
    ori_files.update(sync_files)

    # 得到现有的所有文件, 添加到 cur_files 中
    for root, dirs, files in os.walk(cfg["sync_dir"]):
        for cur_file in files:
            # add path at the start of filename
            cur_file = os.path.join(root, cur_file)
            cur_files.add(cur_file)

    # 得到修改的文件的文件名
    for sync_obj in sync_objs:
        for cur_file in cur_files:
            cur_obj = SyncFile(cur_file)
            if sync_obj["name"] == cur_obj.name and sync_obj["time"] == cur_obj.time and sync_obj["size"] != cur_obj.size:
                mod_files.add(sync_obj["name"])

    new_files = cur_files - ori_files - ign_files
    update_db_file(new_files, mod_files)
    return new_files, mod_files


def update_db_file(new_files: set, mod_files: set):
    """update db dict in memory
    1. 从列表中移除删掉和更新的文件
    2. 添加上更新的文件
    """
    global cfg, db
    if not new_files and not mod_files:
        return
    # original local files dict
    sync_files = db["sync_files"]
    # origin receive files dict
    rev_files = set(db["recv_files"])
    # need to add into sync_files. this is local file.
    new_sync_files = []

    # if there has deleted files or modified files
    # 把修改过的文件的记录先删掉, 再赋予他一个新的记录
    if mod_files:
        # minus deleted files in dict
        sync_files = [x for x in sync_files if x["name"] not in mod_files]
        rev_files = [x for x in rev_files if x not in mod_files]

    # current files to dict
    if new_files or mod_files:
        new_files.update(mod_files)
        for new_file in new_files:
            new_sync_files.append(SyncFile(new_file).__dict__)

    # original files dict + current files to dict - deleted files in dict
    sync_files.extend(new_sync_files)
    db["sync_files"] = list(sync_files)
    db["rev_files"] = list(rev_files)


def file_sys():
    """This function arrange all file system 
    """
    sync_interval = cfg["sync_interval"]
    n = 0
    total = cfg["db_update_persist_ratio"]
    while True:
        time.sleep(sync_interval)
        # logger("update db", "file_sys")
        n += 1
        if n >= total:
            # logger("persist db", "file_sys")
            db.presist_db()
            n = 0

        new_files, mod_files = sync_files()
        update_db_file(new_files, mod_files)

        if new_files:
            logger(new_files, "new_files")
            for new_file in new_files:
                sync_file = SyncFile(new_file)
                # 文件大于 250M
                if sync_file.size >= 250*1024*1024:
                    new_file = asysio.compress(new_file)
                else:
                    logger(new_file, "file_sys")
                    with open(new_file, "rb") as f:
                        data = f.read()
                        if cfg["encryption"] == "True":
                            logger("encryption", "file_sys")
                            data = asysio.encrypt(cfg["key"], data)
                        package = asysio.Package().send(new_file, data)
                        asystp.send(package)
                        logger(f"<SED>{new_file} ", "file_sys")

        if mod_files:
            for mod_file in mod_files:
                with open(mod_file, "rb") as f:
                    content = f.read(300*1024)
                    package = asysio.Package().update(mod_file, 0, content)
                    asystp.send(package)
                    logger(
                        f"<UPT>{mod_files}", "file_sys")


class SyncFile():
    """
    a file entity class to record file and provide operations.
    a format to presist files entity 
    """

    def __init__(self, name):
        self.name = name
        self.time = self.__get_time()
        self.size = self.__get_size()

    def __get_time(self) -> int:
        """get file modify time by file name 
        """
        return os.path.getmtime(self.name)

    def __get_size(self) -> int:
        """get file size by file name
        """
        return os.path.getsize(self.name)

    def __eq__(self, other):
        return self.name == other.name and self.time == other.time and self.size == other.size


