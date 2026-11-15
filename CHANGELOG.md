# Changelog

本库遵循 [语义化版本](https://semver.org/lang/zh-CN/) (0.x 阶段不保证 API 稳定)。
发版流程与版本号约定见 README「发版流程」。

## Unreleased

### 修复

- `FingerprintDB.dump` 文档示例版本号 1 → 2 (代码实际写 v2, `load` 兼容读 v1)
- **设备参数码全面纠正 (SDK 附录 1 / PDF 6.1 逐码核对)**:
  `Param` 原先把 101/102/103 (只写灯效) 与 1101/1102/1103 (只读字符串)、
  10001/10002 (模板格式/模板质量) 三组参数码相互错位。后果:
  `led("green"/"red")` 实际在写蜂鸣/模板格式/模板质量——**灯永远不亮**,
  且 `led("green")` 会**把设备模板格式切成 ISO** (ANSI/ISO 混用, 库里旧
  模板无法匹配), `led("red")` 因写只读参数而抛 `DeviceError`;
  `info()` 的厂商/产品/序列号读的是灯效参数, 得不到真实字符串;
  `template_format` 读的是产品名参数, 恒为 `'ansi'` 且写入无效。
  修正后: 灯效 101/102/103、字符串 1101/1102/1103、模板格式 10001、
  模板质量 10002; 真机复验: 三色灯均正常亮灭, `vendor='ZKTeco Inc.'` /
  `product='Live20R'` / `serial='1972261501196'` / `template_format='ansi'`;
  并加测试锁定参数码表
- 工业登录示例的灯效反馈不再阻塞登录主流程: 灯效写入失败时静默降级为
  纯文字反馈; `Scanner.led` 文档注明灯效参数为只写 (读取返回 -5 属正常)
- 比对阈值文档与范围按 SDK 文档 5.2.23 修正 (范围 0~1000, 越界值直接
  拒绝而不透传给 SDK); 默认值依机型而定 (文档标称 ZKFinger10.0 为 50/70,
  实测 Live20R 为 45/60)
- `ZKFPM_GetDeviceCount` 返回负值 (接口调用失败) 时不再误报为"无设备连接"
- 诊断脚本 `scripts/diag_led.py` / `scripts/diag_params.py` /
  `scripts/diag_beep.py` / `scripts/diag_exports.py` / `scripts/diag_restore.py`:
  换机型/固件时逐项核对灯效、蜂鸣、参数码与导出表

### 新增

- **终端会话层 `FingerprintSession` (可选高阶封装)**: 把「等待手指 → 采集 →
  比对 → 灯效反馈」编排成一步到位, 面向工业终端场景。灯效规则: 等待手指
  白灯常亮 → 成功绿灯 → 失败/超时/异常红灯 (绿/红亮 1.5 秒后自动熄灭回到
  等待态); 灯效写入失败静默降级, 绝不阻断验证主流程。提供
  `wait_login()` (1:N 识别 / 1:1 验证) 与 `wait_enroll()` (登记), 失败不抛
  异常而返回 `SessionResult(ok, reason, user_id, score)`; 另提供
  `on_event` 事件钩子 (`wait_start` / `captured` / `success` / `failed` /
  `enroll_start` / `enroll_progress` / `enroll_done`)。不拥有 db/scanner
  (只借用, `close()` 只熄灯), 不做业务动作、不落盘、不含线程
- `Scanner.beep(on=True)`: 蜂鸣器控制 (参数码 104, 只写)。SDK 文档标注该
  参数 "not supported by the LIVE20R", 实测 Live20R 写入返回 -5 (固件不认
  该参数)——此类机型调用会抛 `DeviceError` 并附明确说明, 不再是难以理解
  的原始错误码
- `CaptureTimeoutError` (`CaptureError` 子类): `acquire` / `enroll` 超时改抛该
  类型, 无人值守场景可按异常类型区分 "用户未按手指" 与 "取像故障";
  已有 `except CaptureError` 分支不受影响
- 诊断脚本 `scripts/diag_led.py` / `scripts/diag_params.py` /
  `scripts/diag_beep.py` / `scripts/diag_exports.py` / `scripts/diag_restore.py`:
  换机型/固件时逐项核对灯效、蜂鸣、参数码与导出表

### 变更

- `Scanner._try_acquire` 更名为公开扩展点 `Scanner.try_acquire` (0.x 窗口期
  完成; 子类覆写请改用新名, 签名保留可选 `params` 参数的约定不变)
- examples/industrial_login.py 改用库层 `FingerprintSession` 编排采集/比对/
  灯效 (示例只保留业务与存储: SQLite 落盘 + `do_login` 对接点), 并演示
  `on_event` 事件回调; 菜单序号调整为 [1] 登记员工指纹 / [2] 指纹登录 /
  [3] 删除员工

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
