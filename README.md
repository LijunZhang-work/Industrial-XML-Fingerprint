# Industrial XML Standard Fingerprint Detector v0.1

这是 BEH 项目中的独立功能目录。v0.1 用固定规则和 Python 标准库 SAX 流式扫描工业 XML，生成默认脱敏、可解释、可重复的 JSON 标准成分报告；运行时不依赖 AI，也不会构造整棵 XML DOM。

当前 P0 Profile：

- IEC 61131-10:2019（与旧 PLCopen TC6 分开评分）
- PLCopen XML TC6 2.0 / 2.01
- IEC 61131-3 语义指纹（不声称存在 IEC 61131-3 XML Schema）
- AutomationML / CAEX
- OMG XMI / Eclipse EMF 序列化

`--profiles all` 还启用辅助识别器：OPC UA UANodeSet、IEC 61850 SCL、IEC 61970 CIMXML/RDF。它们用于识别已知外来标准并抑制 PLC XML 误判，不代表图形连线格式。

## 运行

### Windows 免安装版

下载 `Industrial-XML-Fingerprint-v0.1.0-windows-x64.zip` 后完整解压。无需安装 Python：双击 `scan-xml.cmd` 后粘贴 XML 路径，或直接把 XML 文件拖到该入口上；报告写入解压目录下的 `reports`。发布包还附带中文 `快速开始.txt` 和不含官方完整样本的合成示例。`release/Industrial-XML-Fingerprint-v0.1.0-windows-x64.zip.sha256` 可用于核对下载完整性。

维护者可在 Windows PowerShell 中运行 `scripts/build_windows_portable.ps1` 重建发布包。构建脚本只下载 Python 官方 Windows embeddable runtime，并在解包前强制复验固定 SHA-256；不会下载或打包官方 XML 完整样本。

### 源码运行

项目无需安装第三方依赖。在本目录使用 Python 3.12：

```powershell
python -m xml_fingerprint scan .\project.xml --out .\output
python -m xml_fingerprint scan .\project.xml --profiles all --out .\output --explain-profile IEC61131-10
python -m xml_fingerprint explain .\output\fingerprint_report.json --profile PLCopen-TC6
```

也可执行 `python -m pip install -e .` 后使用 `xml-fingerprint` 命令，但开发和运行并不要求安装。

v0.1 只提供 sanitized 模式。未识别的 namespace、元素名和属性名会变成稳定的私有标签；属性值、文本、原文件名和完整路径不会进入报告。标准 namespace/QName 会保留，以便解释检测证据。

分类不会因为 namespace 已知就把其中任意 QName 判为标准原生：`STANDARD_EXACT` 同时要求已登记 QName；标准允许扩展只从该 Profile 的明确 `AddData`/extension 上下文传播；`STANDARD_ROLE_EQUIVALENT` 还要求父子语法、引用模型或已识别语义作用域证据。

普通 `scan` 永不联网，也不会跟随 XML 中的任何 URL。官方样本下载是独立且必须显式调用的工作流：

```powershell
# 联网动作；只下载 manifest 中已钉住 SHA-256 的官方证据
python -m xml_fingerprint samples fetch --cache .\.sample-cache

# 完全离线；先复验 artifact/member hash，再调用同一个 scanner
python -m xml_fingerprint samples validate `
  --cache .\.sample-cache `
  --out .\official_validation_report.json

# 也可只处理一个 sample_id
python -m xml_fingerprint samples fetch --cache .\.sample-cache --sample omg_xmi_official_model
```

默认缓存位于用户级缓存目录，与源码分开；项目内显式使用的 `.sample-cache/` 也已被忽略。生产 CLI 只信任随包发布的 pinned manifest，不接受自定义 manifest。下载器只接受 HTTPS，并在每次重定向创建下一跳请求前阻止降级或带凭据 URL；同时复核最终 URL、限制包大小、先写同目录临时文件并校验 hash 后原子落盘。ZIP 只流式提取 manifest 指定成员，拒绝不安全路径、加密/目录成员、不支持的压缩方法、异常展开大小和压缩比，并复验成员 hash。

离线 validate 不会“校验路径后再重开缓存路径”：它拒绝非普通文件、符号链接和 Windows reparse point，将缓存源从单个已检查的打开句柄流式复制到本次私有临时目录，并在复制时校验大小与 SHA-256；scanner 只读取通过校验的 staging bytes。缓存文件随后被替换也不会改变实际扫描内容。预期网络、ZIP 和缓存错误会转换为脱敏错误，不输出缓存路径、下载 URL query 或 traceback。

## 输出

一次扫描写出：

- `fingerprint_report.json`：完整报告及规定的顶层结构
- `standard_scores.json`：各 Profile 的独立分数和正/负证据
- `namespace_inventory.json`：namespace、元素 QName、属性 QName 统计
- `structural_regions.json`：结构路径区域分类
- `extension_inventory.json`：标准扩展、外来标准、私有和未知计数
- `unknown_families.json`：按 namespace、父子、属性、引用模式、深度聚类的未知家族
- `reference_mechanisms.json`：ID/引用机制、命中率、多引用和跨作用域候选统计
- `run_metadata.json`：时间、峰值 Python 内存、元素/属性计数、深度和文件大小

Profile similarity 互相独立，不相加为 100%。Coverage 是结构覆盖统计，不是文件按比例拼装的证明；v0.1 也不执行 XSD conformance validation。

## 流式与安全边界

- SAX 逐事件处理，元素正文不落报告，解析完成后立即释放元素事件数据。
- 高频结构统计有明确的 key 数上限；引用定义和值只以 SHA-256 摘要存入临时 SQLite，扫描结束自动删除。
- namespace、QName、路径、区域、未知家族、schemaLocation、PI target 和单元素子项/属性/引用名全部有边界；报告通过 `inventory_completeness` 明确输出 limit、overflow、complete/truncated。
- 默认禁用外部通用/参数实体，拒绝所有 `DOCTYPE`（包括内部实体），不解析或访问 `schemaLocation` URL。
- 最大 XML 嵌套深度为 2048，防止恶意深层结构耗尽栈/内存。
- `peak_memory_bytes` 是 `tracemalloc` 记录的 Python 分配峰值，不代表进程全部 native 内存。

## 测试

聚焦测试：

```powershell
python -m unittest discover -s tests -v
```

100 MiB 流式验收单独启用，避免日常测试无意义地产生大临时文件：

```powershell
$env:RUN_100MB_TEST='1'
python -m unittest tests.test_100mb_streaming -v
```

该验收在系统临时目录生成约 100 MiB 的大文本与高基数 QName 混合 XML，扫描后自动删除，用于确认文件规模、结构基数和 Python 峰值内存脱钩。日常聚焦测试另含 schemaLocation、PI、单元素属性/子项的高基数边界复现。

## 官方小样本交叉验证

机器可读的 [official_samples_manifest.json](standards/official_samples_manifest.json) 记录 `sample_id`、证据级别、官方来源页/直接 artifact URL、获取日期、包和成员 SHA-256、预期 Profile 阈值及负向 guard。仓库不提交标准 PDF、官方下载 ZIP 或其他完整官方原始样本；唯一例外是 [PLCopen TC6 page 63 excerpt](standards/fixtures/plcopen_tc6_0200_page63.xml)，它是从 PLCopen TC6 2.01 Technical Document printed page 63/80 逐字转录的短 XML 示例，并由 PDF hash、页码和 fixture hash 绑定来源。

报告明确区分三种证据，不互相冒充：

- `official_machine_readable_artifact`：官方机器可读示例或模型；
- `official_normative_file`：官方维护的规范性文件；
- `official_published_excerpt`：官方文档中刊载的短摘录，不是官方下载实例。

`official_validation_report.json` 只包含官方 provenance、hash 复验状态、脱敏 document core 和阈值断言；不包含缓存路径、XML 文本或扫描耗时，因此同一份有效缓存的核心报告稳定。所有层级都只验证指纹阈值，不声称 XSD/CAEX/NodeSet conformance。

2026-08-14 已用 manifest 钉住的真实 bytes 完成 integration 验收：IEC example `IEC61131-10=56`（830 元素）、AutomationML example `AutomationML-CAEX=100`（3923 元素）、OMG model `XMI-EMF=63`（132 元素）、OPC NodeSet `OPC-UA-NodeSet=100`（1283 元素）、TC6 published excerpt `PLCopen-TC6=52`（15 元素）。所有 manifest 负向 guard 通过。OPC artifact URL 使用可变的 `latest` 分支；其 SHA-256 冻结本次取得的 bytes，这一限制也会进入报告。

## Registry 来源边界

`standards/registry.json` 只保存公开指纹元数据，不提交 IEC/OMG 标准正文、购买的 PDF 或机器可读包。主要公开来源：

- PLCopen IEC 61131-10 与 Code Components：<https://www.plcopen.org/standards/logic/iec-61131-10/>、<https://www.plcopen.org/standards/xml-echange/code-components/>
- PLCopen TC6 downloads：<https://www.plcopen.org/downloads/>
- AutomationML：<https://www.automationml.org/about-automationml/automationml/>
- OMG XMI：<https://www.omg.org/spec/XMI>
- OPC UA Part 6：<https://reference.opcfoundation.org/Core/Part6/>
- IEC 61850 SCL：<https://iec61850.dvl.iec.ch/what-is-61850/technical-principles/scl-description-language/>

Registry 的访问/获取日期为 2026-08-14。Profile 来源元数据与官方样本证据分开维护：`registry.json` 定义固定规则，`official_samples_manifest.json` 钉住验证 bytes。原有合成 fixture 继续只承担快速规则回归，不冒充官方样本或 conformance 测试。Registry、manifest 和短 excerpt 均作为 wheel package data 分发。

## 明确非目标

本版本不包含公开真实工程 Phase 3、SCL/CIM 官方包获取、HTML、BEH `relation` 专用解释、连线取证、Mega XML 生成器、AI、写回器或 UI。`potential_role` 只是一条结构候选提示，绝不会把 `relation` 自动断言成 IEC Connection。
