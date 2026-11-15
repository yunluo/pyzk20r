# Changelog

本库遵循 [语义化版本](https://semver.org/lang/zh-CN/) (0.x 阶段不保证 API 稳定)。
发版流程与版本号约定见 README「发版流程」。

## Unreleased

### 修复

- `FingerprintDB.dump` 文档示例版本号 1 → 2 (代码实际写 v2, `load` 兼容读 v1)

### 新增

- `CaptureTimeoutError` (`CaptureError` 子类): `acquire` / `enroll` 超时改抛该
  类型, 无人值守场景可按异常类型区分 "用户未按手指" 与 "取像故障";
  已有 `except CaptureError` 分支不受影响

### 变更

- `Scanner._try_acquire` 更名为公开扩展点 `Scanner.try_acquire` (0.x 窗口期
  完成; 子类覆写请改用新名, 签名保留可选 `params` 参数的约定不变)
- examples/industrial_login.py 增加 LED 结果反馈与「先注册后使用」引导:
  登记成功/登录成功亮绿灯, 登录失败/采集失败亮红灯 (采像白灯由固件自动
  点亮); 登录超时与灯效时长提为模块常量 `LOGIN_TIMEOUT` / `FLASH_SECONDS`

## v0.1.4 (2026-11-15)

### 稳定性

- `Scanner.enroll` / `FingerprintDB.enroll` 增加 `timeout` 参数: 超时抛
  `CaptureError`, 防止无人值守场景 (用户放下手指离开) 无限阻塞
- 全局 Init/Terminate 引用计数加线程锁: 多线程构造/关闭设备不再可能把
  计数减成负数或双重 Terminate (单实例操作仍要求单线程)
- 模板入参防御: `add/merge/identify/verify/match` 前置校验 bytes 类型 /
  非空 / <=2048 字节, 不再把晦涩错误透传给 SDK
- 持久化 `load()` 的 base64 解码开启 `validate`, 损坏数据显式拒绝
- DLL 加载失败改抛 `LibraryError`, 消息附排查指引
  (ZKFinger 驱动 / VC++ 运行库 / `PYZK20R_DLL`)
- 补齐 13 处遗漏的英文报错消息 (`raise_for` 静态消息)

### 变更

- 真机相关测试 (真机冒烟 + 真 DLL 加载器) 默认关闭
  (`PYZK20R_REAL_SMOKE=1` 开启): 厂商 DLL 的跨进程退出状态不稳定,
  会以随机方式带崩加载过它的测试进程
- 采集循环内缓存 capture 参数, 持续采集每轮少 2 次 USB 参数查询
  (子类如覆写 `_try_acquire`, 请为签名保留可选 `params` 参数)
- 本地测试脚本 `scripts/test_all.bat` (3.4/3.7/3.12 三版本);
  服务端 CI 不做测试, 只保留 lint/构建与版本一致性检查
- classifiers 移除 Python 3.13 (测试进程在该版本上有解释器崩溃问题,
  明确不支持; requires-python >=3.4 不变)

## v0.1.3 (2026-11-15)

- 恢复 Python 3.4+ 支持: `requires-python >=3.4`, 条件依赖
  `typing==3.10.0.0; python_version < "3.5"` 自动补齐移植包,
  ast 语法守卫 (拒绝 3.5+ 语法)
- 测试迁移到标准库 `unittest`, 移除 pytest——测试零第三方依赖,
  在目标机的 3.4 环境也能直接跑
- 报错消息与日志输出统一英文
- 新增 `scripts/version.py` 版本一致性工具与 CI 检查
- PyPI 页面 classifiers 恢复 3.4-3.13 (3.13 于下版移除)

## v0.1.2 (2026-11-15)

- Homepage 指向 GitHub 仓库

## v0.1.1 (2026-11-15)

- 改为 src 布局; requires-python 上调至 >=3.7 (v0.1.3 恢复 3.4)
- 类型标注改为经典 PEP 484 (真实 typing 导入); 字符串格式化统一 str.format

## v0.1.0 (2026-11-15)

- 首个发布: `Scanner` / `FingerprintDB` (一人多指纹) / 异常层级,
  纯 ctypes 封装 ZKFinger SDK, 支持 Windows XP / Python 3.4+
