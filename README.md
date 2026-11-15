# pyzk20r

中控智慧 (ZKTeco / 熵基) **Live20R** 指纹采集器的 Python 库。

基于官方 [ZKFinger Reader SDK C API](https://www.zksps.com/productinfo/46887.html) 的纯 `ctypes` 封装:

- **零第三方依赖**, 纯标准库
- 支持 **Windows XP / Python 3.4+** (单一代码库, 语法兼容 3.4, 类型标注为 PEP 484:
  参数/返回内联标注, typing 构造以字符串形式标注, 变量用注释)
- 使用驱动安装的系统 DLL (不捆绑分发, 见 DLL 查找顺序)
- 同步阻塞 + 生成器式持续采集; 无内部线程, 单线程使用; 方法粒度小, 便于子类覆写
- **一人多指纹** 模型: `user_id` 可登记多枚指纹, 识别直接返回用户

## 安装

```bash
pip install pyzk20r
```

> 目标机器必须安装 Live20R/SLK20R 的驱动 (libusb0 USB 驱动 + SDK 运行库)。驱动安装器会把
> libzkfp.dll 及其依赖链 (ZKFPCap.dll、zkfinger12.dll、ZKFPSensors\*.dll) 装进
> System32/SysWOW64, 本库直接加载系统副本 —— libzkfp.dll 的内部依赖按其自身所在目录解析,
> 单独拷贝一个 DLL 无法工作, 因此**本库不捆绑分发 DLL**。
> 也可用环境变量 `PYZK20R_DLL` 或 `Scanner(dll_path=...)` 显式指定 `libzkfp.dll` 路径。

## 快速上手

```python
from pyzk20r import FingerprintDB, MatchError, Scanner

db = FingerprintDB()
with Scanner() as dev:                       # 构造即打开, 退出自动关闭
    print(dev.info())                        # 厂商/产品名/序列号/VID&PID

    # 登记: 采 3 次自动合并入库 (同一用户可多次调用 = 多枚指纹)
    tpl = db.enroll(dev, user_id=1001)

    # 1:N 识别 -> (user_id, 分数)
    frame = dev.acquire()                    # 阻塞等待一次按压
    uid, score = db.identify(frame.template)

    # 1:1 验证 -> 分数 (对该用户所有指纹取最优)
    score = db.verify(1001, dev.acquire().template)
```

持续采集 (生成器):

```python
with Scanner() as dev:
    for frame in dev.capture(poll_interval=0.1):   # 暂无手指时自动重试
        open("finger.bmp", "wb").write(frame.to_bmp())   # 图像存 BMP
        tpl = frame.template                             # 提取好的模板
        break
```

## API 概览

### `Scanner(index=0, dll_path=None)` — 采集器

| 成员 | 说明 |
| --- | --- |
| `acquire(poll_interval=0.1, timeout=None)` | **阻塞等待手指**并采集一帧 → `Frame`; `timeout` 秒超时抛 `CaptureError` (`None` 为一直等) |
| `capture(poll_interval=0.1)` | 持续采集生成器, 逐帧 `yield Frame` (设备关闭后下一次迭代抛 `ZKFPMError`) |
| `acquire_image()` | 只取图像字节 |
| `enroll(poll_interval)` | 连续采集 3 次 (SDK 合并固定要求) 返回模板列表, 每次回调 `on_enroll_progress(done, total)` |
| `width / height / dpi` (dpi 可写) | 图像规格; 儿童指纹建议 DPI 设 750/1000 |
| `fake_detect` (可读写) | 防假指纹开关 |
| `finger_is_live` | 最近一次采集是否真手指 (`value & 31 == 31`) |
| `info()` | `DeviceInfo(vendor, product, serial, vid_pid)` |
| `led('white'/'green'/'red', on)` | 指示灯 |
| `template_format` (`'ansi'/'iso'`, 可读写) | 模板标准 |
| `template_quality(template)` | 模板质量分 |
| `device_ok` | 设备状态 |
| `open() / close()` / `with` | 生命周期, 可重复 close |

### `Frame` — 单次采集帧

`image` (原始灰度字节) / `template` (指纹模板) / `width / height / dpi` / `to_bmp()`

### `FingerprintDB(dll_path=None)` — 内存指纹库 (一人多指纹)

| 成员 | 说明 |
| --- | --- |
| `enroll(scanner, user_id)` | 完整登记: 采 3 次 → 合并 → 存入该用户名下 |
| `add(user_id, template)` | 直接添加一枚模板 (同一 user_id 可多次) |
| `identify(template)` | 1:N → `(user_id, score)`, 无匹配抛 `MatchError` |
| `verify(user_id, template)` | 1:1 → 最优分数, 不匹配抛 `MatchError` |
| `match(a, b)` | 两枚模板比对 → 分数 |
| `merge([t1, t2, t3])` | 3 枚采集模板合并为登记模板 |
| `remove(user_id[, template])` | 删除某用户的全部/指定指纹 |
| `clear() / count / users / fingers_of(user_id)` | 库管理 |
| `dump() / load(data)` | 持久化契约: 导出/重建 JSON 兼容结构 (存储介质与时机由应用层决定) |
| `threshold_1_1 / threshold_1_n` (可读写) | 比对阈值, 默认 35 / 55 (0~100) |
| `extract_from_image(path, dpi=500)` | 从 BMP 提取模板 (仅 SDK 标准版) |

### 异常层级

```
ZKFPMError (含 .code 原始 SDK 错误码)
├── DeviceError
│   └── DeviceNotFoundError      # -3 无设备 / -6 打开失败
├── CaptureError                 # -8 取像失败等
├── MatchError                   # -20 不匹配
├── MergeError                   # -22 合并失败
├── ExtractError                 # -9/-26/-27 图像提取失败
└── LibraryError                 # -1/-2/-17 库层失败
```

### 继承点

- `Scanner.on_enroll_progress(done, total)` — 登记进度钩子
- `Scanner._try_acquire()` — 采集前后的自定义处理 (如防假判定重试)
- `Scanner` / `FingerprintDB` 所有公开方法均可覆写

### 线程模型

本库是**同步单线程**设计: 无内部线程, SDK 句柄与全局初始化计数不加锁。
请勿在多线程中并发构造/关闭设备或并发采集; 需要后台采集请自行承载线程,
线程内独占使用一个 `Scanner`。

### 持久化

内存指纹库**不自动落盘**, 进程退出即消失; 持久化由应用层用 `dump()/load()` 接入任意介质:

```python
import json
data = db.dump()                                   # {"version": 1, "templates": [[uid, "BASE64"], ...]}
json.dump(data, open("fingers.json", "w"))

db = FingerprintDB.load(json.load(open("fingers.json")))   # 启动时重建, 无需重新按手指
```

SQLite 存取完整示例见 `examples/persist.py`。

## DLL 查找顺序

1. `Scanner(dll_path=...)` / `FingerprintDB(dll_path=...)` 显式指定
2. 环境变量 `PYZK20R_DLL`
3. 系统副本 `C:\Windows\System32\libzkfp.dll` (32 位 Python 下经 WOW64 重定向即 SysWOW64;
   需先安装驱动, 完整依赖链在系统目录)
4. 兜底: 裸名 `libzkfp.dll` 交给 Windows 搜索 (PATH 中)

> 未安装许可文件 `zkfinger.lic` 时 SDK 会打印许可警告但功能可用 (算法 v12)。

## XP / Python 3.4 部署要点

- 目标机安装 **x86 版 Python 3.4** 与 Live20R/SLK20R USB 驱动
- `pip install pyzk20r-0.1.0-py3-none-any.whl` (纯 Python wheel, 无需编译)。
  注意: 当前构建产物为 Metadata-Version 2.5, XP 上的老版 pip 若拒绝安装,
  先升级 pip (`python -m pip install -U "pip<20"`), 或直接把 wheel 当 zip
  解包进 site-packages (纯 Python 包, 解包即用)
- XP 下 `libzkfp.dll` 依赖系统 VC 运行库; 若加载报错, 安装 VC++ 2010/2013 运行库 (x86)
- 本库代码不含 f-string / `typing` 导入 / 3.5+ 语法 (CI 有语法守卫测试)

## 开发

```bash
uv run pytest                     # mock 单元测试 + 真机冒烟 (无设备自动跳过)
uv run ruff check .               # lint
uv run mypy                       # 类型检查 (PEP 484 标注)
```

dev 工具由 `[dependency-groups]` 提供 (pytest / ruff / mypy), `uv run` 自动同步。

SDK 文档 (中英文 PDF + 头文件 + MFC Demo) 从 ZKTeco 官网 / "ZKFinger SDK" 安装包获取;
本库的接口注释即按其 C API 编写。

## License

MIT (见 LICENSE)。ZKFinger SDK 及 `libzkfp.dll` 版权归 ZKTeco 所有, 需随官方驱动获取, 仅供配合其设备使用。
