"""
目标文件扫描器

扫描指定目录下的 Python 文件，支持全量和增量模式。
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class FileInfo:
    """文件信息"""
    path: str
    name: str
    size: int
    mtime: datetime

    @property
    def rel_path(self) -> str:
        """相对路径（用于报告显示）"""
        return self.path


class TargetScanner:
    """
    目标文件扫描器

    Usage:
        scanner = TargetScanner(
            target_dirs=["~/projects/TwinForge/scripts/"],
            exclude_patterns=["__pycache__", "test_*.py"],
        )
        files = scanner.scan()
        for f in files:
            print(f.name, f.size)
    """

    DEFAULT_EXCLUDES = [
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "*.pyc",
    ]

    def __init__(
        self,
        target_dirs: List[str],
        exclude_patterns: Optional[List[str]] = None,
    ):
        self.target_dirs = [os.path.expanduser(d) for d in target_dirs]
        self.exclude_patterns = exclude_patterns or self.DEFAULT_EXCLUDES

    def scan(self) -> List[FileInfo]:
        """
        扫描所有目标目录下的 .py 文件

        Returns:
            FileInfo 列表
        """
        files: List[FileInfo] = []
        for dir_path in self.target_dirs:
            if not os.path.isdir(dir_path):
                continue
            for root, dirs, filenames in os.walk(dir_path):
                # 过滤排除目录
                dirs[:] = [
                    d for d in dirs
                    if not self._is_excluded(d)
                ]
                for fname in filenames:
                    if not fname.endswith(".py"):
                        continue
                    if self._is_excluded(fname):
                        continue
                    full_path = os.path.join(root, fname)
                    try:
                        stat = os.stat(full_path)
                        files.append(FileInfo(
                            path=full_path,
                            name=fname,
                            size=stat.st_size,
                            mtime=datetime.fromtimestamp(stat.st_mtime),
                        ))
                    except OSError:
                        continue
        return sorted(files, key=lambda f: f.path)

    def filter_changed(self, files: List[FileInfo], since: datetime) -> List[FileInfo]:
        """
        增量模式：只返回自指定时间后修改过的文件

        Args:
            files: 全量文件列表
            since: 截止时间

        Returns:
            修改过的文件列表
        """
        return [f for f in files if f.mtime > since]

    def _is_excluded(self, name: str) -> bool:
        """检查文件名是否匹配排除模式"""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False
