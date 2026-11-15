# -*- coding: utf-8 -*-
"""版本号工具: 保证 pyproject.toml / __init__.py / uv.lock / git tag 四处版本一致。

项目里版本号出现在四个地方, 任何一处漏改都会造成版本漂移:

  1. ``pyproject.toml`` 的 ``[project].version``  -- 打包与 PyPI 发布依据
  2. ``src/pyzk20r/__init__.py`` 的 ``__version__`` -- 对外暴露的版本号
  3. ``uv.lock`` 里 pyzk20r 根包的 ``version``      -- 依赖锁文件记录的项目版本
  4. git tag ``vX.Y.Z``                            -- 发版标记

子命令::

    check [--tag vX.Y.Z]   校验一致性 (CI 用; 发现漂移退出码 1)
    bump X.Y.Z            把 1/2/3 三处版本号同步改成 X.Y.Z
    sync                  把三处版本号同步为最新 tag 的版本
    tag [X.Y.Z]           校验通过后创建 annotated tag
    release X.Y.Z         bump + 提交版本文件 + 创建 annotated tag

零第三方依赖, 兼容 Python 3.7+。
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def _default_root():
    """仓库根目录: 可用环境变量 PYZK20R_ROOT 覆盖 (测试用)。"""
    env = os.environ.get("PYZK20R_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


ROOT = _default_root()
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "pyzk20r" / "__init__.py"
UV_LOCK = ROOT / "uv.lock"

PACKAGE_NAME = "pyzk20r"

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class VersionError(Exception):
    """版本相关操作失败。"""


# --------------------------------------------------------------------------
# 文件读写 (保持原换行符与 UTF-8 编码)
# --------------------------------------------------------------------------


def read_text(path):
    """读取文本文件, 返回 (内容, 换行符)。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VersionError("读取 {} 失败: {}".format(path, exc))
    text = raw.decode("utf-8")
    return text, ("\r\n" if "\r\n" in text else "\n")


def write_text(path, text, newline):
    """写回文本文件, 统一使用指定换行符。"""
    if newline == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def section_span(text, name):
    """返回 [name] 段在文本中的 (起始, 结束) 偏移, 不含段头本身。"""
    m = re.search(r"(?m)^\[" + re.escape(name) + r"\][ \t\r]*$", text)
    if not m:
        raise VersionError("pyproject.toml 中找不到 [{}] 段".format(name))
    start = m.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    return start, end


# --------------------------------------------------------------------------
# 各处版本号的读取 / 写入
# --------------------------------------------------------------------------


def read_pyproject_version(text=None):
    """读取 pyproject.toml 的 [project].version。"""
    if text is None:
        text, _ = read_text(PYPROJECT)
    start, end = section_span(text, "project")
    m = re.search(r'(?m)^version[ \t]*=[ \t]*"([^"]+)"', text[start:end])
    if not m:
        raise VersionError("pyproject.toml 的 [project] 段缺少 version 字段")
    return m.group(1)


def write_pyproject_version(text, version):
    """把 pyproject.toml 的 [project].version 改成指定值, 返回新文本。"""
    start, end = section_span(text, "project")
    m = re.search(r'(?m)^version[ \t]*=[ \t]*"[^"]+"', text[start:end])
    if not m:
        raise VersionError("pyproject.toml 的 [project] 段缺少 version 字段")
    head, body, tail = text[:start], text[start:end], text[end:]
    return head + body[: m.start()] + 'version = "{}"'.format(version) + body[m.end() :] + tail


def read_init_version(text=None):
    """读取 src/pyzk20r/__init__.py 的 __version__。"""
    if text is None:
        text, _ = read_text(INIT_PY)
    m = re.search(r'(?m)^__version__[ \t]*=[ \t]*"([^"]+)"', text)
    if not m:
        raise VersionError("{} 中找不到 __version__".format(INIT_PY))
    return m.group(1)


def write_init_version(text, version):
    """把 __init__.py 的 __version__ 改成指定值, 返回新文本。"""
    m = re.search(r'(?m)^__version__[ \t]*=[ \t]*"[^"]+"', text)
    if not m:
        raise VersionError("{} 中找不到 __version__".format(INIT_PY))
    return text[: m.start()] + '__version__ = "{}"'.format(version) + text[m.end() :]


def lock_package_span(text, name):
    """返回 uv.lock 中名为 name 的 [[package]] 块的 (起始, 结束) 偏移。"""
    for m in re.finditer(r"(?m)^\[\[package\]\][ \t\r]*$", text):
        start = m.end()
        nxt = re.search(r"(?m)^\[\[package\]\][ \t\r]*$", text[start:])
        end = start + nxt.start() if nxt else len(text)
        block = text[start:end]
        nm = re.search(r'(?m)^name[ \t]*=[ \t]*"([^"]+)"', block)
        if nm and nm.group(1) == name:
            return start, end
    raise VersionError("uv.lock 中找不到包 {}".format(name))


def read_lock_version(text=None):
    """读取 uv.lock 里项目根包的版本号。"""
    if text is None:
        text, _ = read_text(UV_LOCK)
    start, end = lock_package_span(text, PACKAGE_NAME)
    m = re.search(r'(?m)^version[ \t]*=[ \t]*"([^"]+)"', text[start:end])
    if not m:
        raise VersionError("uv.lock 中 {} 包缺少 version 字段".format(PACKAGE_NAME))
    return m.group(1)


def write_lock_version(text, version):
    """把 uv.lock 里项目根包的版本号改成指定值, 返回新文本。"""
    start, end = lock_package_span(text, PACKAGE_NAME)
    m = re.search(r'(?m)^version[ \t]*=[ \t]*"[^"]+"', text[start:end])
    if not m:
        raise VersionError("uv.lock 中 {} 包缺少 version 字段".format(PACKAGE_NAME))
    head, body, tail = text[:start], text[start:end], text[end:]
    return head + body[: m.start()] + 'version = "{}"'.format(version) + body[m.end() :] + tail


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def git(*args):
    """执行 git 命令, 返回 stdout 文本; 失败抛 VersionError。"""
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise VersionError("git {} 失败: {}".format(" ".join(args), (proc.stderr or "").strip()))
    return proc.stdout


def git_try(*args):
    """执行 git 命令, 失败返回 (False, stderr)。"""
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()
    return True, proc.stdout


def list_tags():
    """列出所有 vX.Y.Z 形式的 tag, 按版本从旧到新排序。"""
    out = git("tag", "--list")
    tags = [line.strip() for line in out.splitlines() if TAG_RE.match(line.strip())]
    tags.sort(key=version_key)
    return tags


def version_at_ref(ref):
    """读取某个 ref (tag/commit) 处 pyproject.toml 的版本号; 取不到返回 None。"""
    ok, out = git_try("show", "{}:pyproject.toml".format(ref))
    if not ok:
        return None
    try:
        return read_pyproject_version(out)
    except VersionError:
        return None


def version_key(version):
    """版本号排序键: 数字段优先, 后缀按预发布处理 (排在正式版之前)。"""
    m = VERSION_RE.match(version)
    if not m:
        return (0, 0, 0, 1, version)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, "")


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------


def current_version():
    """当前 pyproject.toml 里的版本号。"""
    return read_pyproject_version()


def do_check(only_tag=None):
    """校验四处版本一致; 返回问题列表 (空列表表示通过)。"""
    problems = []

    version = current_version()
    if not VERSION_RE.match(version):
        problems.append("pyproject.toml 的版本号 {!r} 不是 X.Y.Z 格式".format(version))

    init_version = read_init_version()
    if init_version != version:
        problems.append(
            "src/pyzk20r/__init__.py 的 __version__ = {} 与 pyproject.toml 的 {} 不一致".format(
                init_version, version
            )
        )

    if UV_LOCK.exists():
        lock_version = read_lock_version()
        if lock_version != version:
            problems.append(
                "uv.lock 里 {} 包的版本 {} 与 pyproject.toml 的 {} 不一致 (需重新 lock)".format(
                    PACKAGE_NAME, lock_version, version
                )
            )
    else:
        print("[提示] 未找到 uv.lock, 跳过锁文件校验")

    tags = list_tags()
    for tag in tags:
        tagged_version = version_at_ref(tag)
        if tagged_version is None:
            problems.append("tag {} 指向的提交里没有可解析的 pyproject.toml".format(tag))
        elif tagged_version != tag[1:]:
            problems.append(
                "tag {} 指向的提交里 pyproject.toml 版本为 {} —— 版本漂移".format(
                    tag, tagged_version
                )
            )

    if tags:
        latest = tags[-1]
        if version_key(version) < version_key(latest[1:]):
            problems.append(
                "pyproject.toml 版本 {} 低于已打 tag 的最高版本 {} —— 版本漂移".format(
                    version, latest
                )
            )

    if only_tag:
        if only_tag not in tags:
            problems.append("指定的 tag {} 不存在".format(only_tag))

    if problems:
        print("[失败] 发现 {} 处版本漂移:".format(len(problems)))
        for item in problems:
            print("  - {}".format(item))
        return problems

    print(
        "[通过] 版本一致: pyproject.toml / __init__.py / uv.lock = {}; 已校验 {} 个 tag".format(
            version, len(tags)
        )
    )
    return problems


def do_bump(target, force=False):
    """把三处版本号同步改成 target。"""
    if not VERSION_RE.match(target):
        raise VersionError("版本号 {!r} 不合法, 应为 X.Y.Z 格式".format(target))

    current = current_version()
    if not force and version_key(target) < version_key(current):
        raise VersionError("不允许降级: 当前 {} -> 目标 {}".format(current, target))

    changed = []

    text, newline = read_text(PYPROJECT)
    if read_pyproject_version(text) != target:
        write_text(PYPROJECT, write_pyproject_version(text, target), newline)
        changed.append("pyproject.toml")

    text, newline = read_text(INIT_PY)
    if read_init_version(text) != target:
        write_text(INIT_PY, write_init_version(text, target), newline)
        changed.append("src/pyzk20r/__init__.py")

    if UV_LOCK.exists():
        text, newline = read_text(UV_LOCK)
        if read_lock_version(text) != target:
            write_text(UV_LOCK, write_lock_version(text, target), newline)
            changed.append("uv.lock")

    if changed:
        print("[完成] 版本号 {} -> {}: {}".format(current, target, ", ".join(changed)))
    else:
        print("[完成] 版本号已是 {}".format(target))
    return changed


def do_sync():
    """把三处版本号同步为最新 tag 的版本。"""
    tags = list_tags()
    if not tags:
        raise VersionError("仓库里还没有 vX.Y.Z 形式的 tag, 无法同步")
    return do_bump(tags[-1][1:], force=True)


def do_tag(target=None, message=None):
    """校验通过后创建 annotated tag。"""
    version = target if target else current_version()
    if not target and not VERSION_RE.match(version):
        raise VersionError("当前版本号 {!r} 不合法, 请显式给出 X.Y.Z".format(version))

    if current_version() != version:
        raise VersionError(
            "pyproject.toml 版本是 {}, 与目标 {} 不一致, 请先跑 bump".format(
                current_version(), version
            )
        )
    if read_init_version() != version:
        raise VersionError(
            "src/pyzk20r/__init__.py 的 __version__ 与 {} 不一致, 请先跑 bump".format(version)
        )
    if UV_LOCK.exists() and read_lock_version() != version:
        raise VersionError(
            "uv.lock 里 {} 包的版本与 {} 不一致, 请先跑 bump".format(PACKAGE_NAME, version)
        )

    dirty = git("status", "--porcelain", "--", *version_files())
    if dirty.strip():
        raise VersionError("版本文件还有未提交的改动, 先提交再打 tag:\n{}".format(dirty.strip()))

    tag = "v{}".format(version)
    if tag in list_tags():
        raise VersionError("tag {} 已存在, 不要重复打 tag".format(tag))

    git("tag", "-a", tag, "-m", message or "release: {}".format(tag))
    print("[完成] 已创建 annotated tag {}".format(tag))
    return tag


def version_files():
    """需要纳入版本管理的文件列表 (uv.lock 被 gitignore 时不计入)。"""
    files = ["pyproject.toml", "src/pyzk20r/__init__.py"]
    ok, _ = git_try("check-ignore", "-q", "uv.lock")
    if not ok:
        files.append("uv.lock")
    return files


def do_release(version, message=None):
    """bump + 提交版本文件 + 创建 annotated tag。"""
    do_bump(version)
    git("add", *version_files())
    git("commit", "-m", "release: v{} (版本号同步 {})".format(version, version))
    tag = do_tag(version, message)
    print("")
    print("接下来推送 (tag 推送即触发 PyPI 发布):")
    print("  git push origin main && git push origin {}".format(tag))
    print("  git push gitee main && git push gitee {}".format(tag))
    return tag


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="版本号工具: 保证 pyproject.toml / __init__.py / uv.lock / tag 四处一致",
    )
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="校验版本一致性 (CI 用)")
    p_check.add_argument("--tag", dest="tag", default=None, help="额外校验指定 tag 存在")

    p_bump = sub.add_parser("bump", help="把三处版本号同步改成 X.Y.Z")
    p_bump.add_argument("version", help="目标版本号, 形如 0.1.3")
    p_bump.add_argument("--force", action="store_true", help="允许降级")

    sub.add_parser("sync", help="把三处版本号同步为最新 tag 的版本")

    p_tag = sub.add_parser("tag", help="校验通过后创建 annotated tag")
    p_tag.add_argument("version", nargs="?", default=None, help="版本号, 默认取当前 pyproject 版本")
    p_tag.add_argument("-m", "--message", default=None, help="tag 说明 (默认 release: vX.Y.Z)")

    p_release = sub.add_parser("release", help="bump + 提交版本文件 + 创建 annotated tag")
    p_release.add_argument("version", help="目标版本号, 形如 0.1.3")
    p_release.add_argument("-m", "--message", default=None, help="tag 说明")

    args = parser.parse_args(argv)
    command = args.command
    if not command:
        parser.print_help()
        return 2

    try:
        if command == "check":
            return 1 if do_check(args.tag) else 0
        if command == "bump":
            do_bump(args.version, force=args.force)
            return 0
        if command == "sync":
            do_sync()
            return 0
        if command == "tag":
            do_tag(args.version, args.message)
            return 0
        if command == "release":
            do_release(args.version, args.message)
            return 0
    except VersionError as exc:
        print("[错误] {}".format(exc))
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
