# -*- coding: utf-8 -*-
"""导出表核对: 列出 libzkfp.dll 的全部导出函数, 与本库绑定清单比对。

不做任何 SDK 调用, 只读 PE 导出表 (纯 stdlib 解析), 用于确认:
  1. 本库绑定的每个函数都真实存在于 DLL
  2. DLL 里是否还有本库未绑定的可用接口 (如 Base64/Blob/GenRegTemplate)
  3. 导出的名字修饰 (_ZKFPM_xxx@N) 反推 stdcall 栈清理字节数, 与本库
     argtypes 数量是否一致
"""

import os
import struct
import sys

DLL_PATHS = [
    r"C:\Windows\System32\libzkfp.dll",
    r"C:\Windows\SysWOW64\libzkfp.dll",
]

# 本库 _binding.py 中声明原型的函数 (与 protos 字典保持一致)
BOUND = {
    "ZKFPM_Init": 0,
    "ZKFPM_Terminate": 0,
    "ZKFPM_GetDeviceCount": 0,
    "ZKFPM_OpenDevice": 1,
    "ZKFPM_CloseDevice": 1,
    "ZKFPM_SetParameters": 4,
    "ZKFPM_GetParameters": 4,
    "ZKFPM_GetCaptureParamsEx": 4,
    "ZKFPM_AcquireFingerprint": 5,
    "ZKFPM_AcquireFingerprintImage": 3,
    "ZKFPM_DBInit": 0,
    "ZKFPM_DBFree": 1,
    "ZKFPM_DBSetParameter": 3,
    "ZKFPM_DBGetParameter": 3,
    "ZKFPM_DBMerge": 6,
    "ZKFPM_DBAdd": 4,
    "ZKFPM_DBDel": 2,
    "ZKFPM_DBClear": 1,
    "ZKFPM_DBCount": 2,
    "ZKFPM_DBIdentify": 5,
    "ZKFPM_DBMatch": 5,
    "ZKFPM_VerifyByID": 4,
    "ZKFPM_ExtractFromImage": 5,
    "ZKFPM_GetTemplateQuality": 3,
}


def read_exports(path):
    """解析 PE 导出表, 返回导出名列表。"""
    with open(path, "rb") as f:
        data = f.read()
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe_off : pe_off + 4] == b"PE\0\0", "not a PE file"
    coff = pe_off + 4
    n_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:  # PE32
        dd = opt + 96
    else:  # PE32+
        dd = opt + 112
    exp_rva, exp_size = struct.unpack_from("<II", data, dd)
    sections = opt + opt_size
    sec = []
    for i in range(n_sections):
        base = sections + i * 40
        va = struct.unpack_from("<I", data, base + 12)[0]
        vsize = struct.unpack_from("<I", data, base + 8)[0]
        praw = struct.unpack_from("<I", data, base + 20)[0]
        sec.append((va, vsize, praw))

    def rva_to_off(rva):
        for va, vsize, praw in sec:
            if va <= rva < va + max(vsize, 1):
                return praw + (rva - va)
        return None

    exp_off = rva_to_off(exp_rva)
    if exp_off is None:
        return []
    n_names = struct.unpack_from("<I", data, exp_off + 24)[0]
    names_rva = struct.unpack_from("<I", data, exp_off + 32)[0]
    names_off = rva_to_off(names_rva)
    out = []
    for i in range(n_names):
        rva = struct.unpack_from("<I", data, names_off + i * 4)[0]
        off = rva_to_off(rva)
        if off is None:
            continue
        end = data.index(b"\0", off)
        out.append(data[off:end].decode("ascii", "replace"))
    return out


def main():
    for path in DLL_PATHS:
        if not os.path.exists(path):
            print("[skip] not found:", path)
            continue
        try:
            names = read_exports(path)
        except Exception as e:  # pragma: no cover - 诊断脚本
            print("[error] {}: {}".format(path, e))
            continue
        print("=" * 70)
        print("{}  ({} exports)".format(path, len(names)))
        print("=" * 70)
        clean = {}
        for n in names:
            if not n.startswith("ZKFPM_"):
                continue
            base = n.split("@")[0]
            clean.setdefault(base, []).append(n)
        print("-- 本库已绑定的函数 --")
        missing = []
        for fn in sorted(BOUND):
            if fn in clean:
                deco = clean[fn][0]
                stack = deco.split("@")[1].rstrip("A") if "@" in deco else ""
                note = ""
                if stack:
                    try:
                        if int(stack) != BOUND[fn] * 4:
                            note = "  <== 栈字节 {} != argtypes*4 {}".format(stack, BOUND[fn] * 4)
                    except ValueError:
                        note = "  <== 修饰名异常"
                print("  OK   {:<28} {:<30}{}".format(fn, deco, note))
            else:
                missing.append(fn)
                print("  MISS {:<28} <not exported>".format(fn))
        print("-- DLL 导出但本库未绑定的 ZKFPM_ 接口 --")
        for fn in sorted(clean):
            if fn not in BOUND:
                print("  --   {:<28} {}".format(fn, clean[fn][0]))
        if missing:
            print("!! 本库绑定了但 DLL 不存在的函数: {}".format(missing))
        print()


if __name__ == "__main__":
    sys.exit(main())
