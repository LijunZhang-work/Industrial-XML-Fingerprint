# Codex 开发行动指南：工业 PLC / 自动化 XML 标准指纹检测器与 BEH 成分分析

## 0. 本文目的

当前真实 BEH 工程 XML 可能达到几十 MB，且 Codex 无法直接访问工作环境中的真实 XML。

目前不能先假设：

- BEH 一定是 IEC 61131-10；
- BEH 一定是旧 PLCopen XML；
- BEH 只是“标准 XML + 少量私有字段”；
- 或者所有可见连线都遵循同一种 XML 结构。

因此，本阶段**不要开发连线修复算法，也不要先生成 50MB / 100MB 巨型测试工程**。

本阶段唯一目标是先开发一个：

> **Industrial XML Standard Fingerprint Detector**
>
> 工业自动化 XML 标准指纹检测器

它需要能够在**不理解业务内容、不读取整个文件到内存、不依赖 AI 运行时参与**的情况下，对超大 XML 做“成分化验”。

最终回答：

1. 这个 XML 明确声明自己是什么格式？
2. 它的 XML 序列化底座像什么？
3. 它的 PLC / 控制逻辑结构最像哪套标准？
4. 是否存在 AutomationML / CAEX 一类工程容器？
5. 是否混入电力、OPC UA、CIM 等其他已知 XML 标准？
6. 哪些部分属于标准原生结构？
7. 哪些属于标准允许的扩展？
8. 哪些明显是厂商私有结构？
9. 哪些目前完全无法识别？
10. 后续应该以哪套公开标准作为“连线查询器”的第一参照标准？

---

# 1. 最重要的设计原则：不要做“70% A + 30% B”的简单饼图

用户最终可以看到类似：

```text
IEC 61131-10 结构覆盖：72%
厂商私有/未知：28%
```

但程序内部绝不能简单地认为：

```text
所有标准分数相加 = 100%
```

原因是同一份 XML 可以同时具有不同层次的标准特征。

例如：

```text
底层序列化：
OMG XMI / Eclipse EMF

业务语义：
IEC 61131-3

PLC 工程交换：
IEC 61131-10

工程容器：
AutomationML / CAEX

厂商扩展：
BEH 自定义 relation / metadata
```

这些并不互斥。

因此必须分层检测。

---

# 2. 检测模型：五层标准指纹

整个检测器必须按照以下五层建立结果。

---

## Layer A：XML / 模型序列化底座

回答：

> 这个 XML 是用什么“模型保存机制”组织的？

重点检测：

### A1. 普通 XML Schema 风格

例如：

```text
xmlns
xsi:schemaLocation
XSD
ID / IDREF
```

### A2. OMG XMI

重点指纹：

```text
xmi:version
xmi:id
xmi:type
xsi:type
href
XMI namespace
```

OMG XMI 是 XML Metadata Interchange 标准，可以用于 MOF/UML 等模型的 XML 交换。

特别注意：

> Eclipse EMF 默认就广泛使用 XMI 作为模型序列化方式。

BEH 已知是 Java / OpenJDK 工具，因此 XMI / EMF 指纹必须进入第一版检测器。

### A3. RDF/XML

用于识别 CIM 等 RDF 风格工程数据。

### A4. 其他 XML 底座

例如：

```text
CAEX
UANodeSet
SCL
厂商自定义 XSD
完全无 Schema
```

---

# 3. Layer B：PLC / 控制逻辑核心标准

这是本项目最重要的一层。

第一版必须至少支持以下标准族。

---

## B1. IEC 61131-10:2019

**最高优先级核心 Profile。**

标准名称：

```text
IEC 61131-10:2019
Programmable controllers – Part 10:
PLC open XML exchange format
```

它定义：

> IEC 61131-3 工程的 XML 导入 / 导出格式。

标准覆盖：

- Configuration
- Data Types
- POU
- ST
- FBD
- LD
- SFC
- 图形信息
- 连接信息
- 扩展 Schema

官方还提供机器可读 Code Components / XSD。

检测器需要建立：

```text
IEC61131_10_Profile
```

包含：

- namespace 指纹；
- XSD / schemaLocation 指纹；
- 元素词汇；
- 属性词汇；
- 父子结构；
- POU / FBD / LD / SFC 结构；
- ConnectionPointIn / Connection / ConnectionPointOut 等连接结构；
- Connector / Continuation 等典型图形元素；
- 标准 extension points；
- 负向证据。

注意：

> IEC 61131-10:2019 和旧 PLCopen XML 不是同一个 Schema，不能混成一个 Profile。

---

## B2. 旧 PLCopen XML / TC6 family

第二个核心 Profile。

重点版本：

```text
PLCopen XML 2.0 / 2.01
TC6 XML
```

PLCopen 官方当前仍公开：

- Version 2.01 Technical Documentation
- Version 2.01 XSD
- 示例

已知典型 namespace 包括：

```text
http://www.plcopen.org/xml/tc6_0201
```

历史资料 / 示例中也可能见到：

```text
http://www.plcopen.org/xml/tc6_0200
```

因此不能只靠一个 namespace 字符串做判断。

建立：

```text
PLCopen_TC6_Profile
```

并支持：

```text
version_hint
```

而不是把所有 tc6_* 一律当作完全相同。

重点提取：

- project
- types
- pous
- body
- FBD / LD / SFC
- localId
- refLocalId
- connectionPointIn
- connectionPointOut
- connection
- connector
- continuation
- addData
- position / relPosition
- coordinateInfo

---

## B3. IEC 61131-3

IEC 61131-3 **不是 XML 格式标准**。

不能做：

```text
XSD Validation against IEC 61131-3
```

但是它必须作为：

> **PLC 语义指纹 Profile**

当前 IEC 61131-3:2025 定义 PLC 编程语言的 syntax / semantics，包括：

- ST
- LD
- FBD
- SFC
- Program
- Function
- Function Block
- Configuration 等概念。

因此建立：

```text
IEC61131_3_Semantic_Profile
```

它只回答：

> XML 里的业务/图形词汇是否高度符合 IEC 61131-3 世界观？

它不能回答：

> XML 文件是否符合 IEC 61131-10。

例如最终可能出现：

```text
XML格式：
厂商私有

IEC61131-10严格匹配：
35%

IEC61131-3语义匹配：
92%
```

这种结果完全合理。

---

# 4. Layer C：工业工程容器 / 跨工具标准

---

## C1. AutomationML / IEC 62714

这是第一版必须考虑的标准。

原因：

AutomationML 本身就是面向工业自动化工程工具的数据交换体系。

核心：

```text
IEC 62714-1
AutomationML
```

其顶层基础是：

```text
CAEX
IEC 62424
```

而更重要的是：

```text
IEC 62714-4:2020
AutomationML – Part 4: Logic
```

官方明确说明：

- 可包含 SFC；
- 可包含 FBD；
- AML logic XML schema 使用 IEC 61131-10 存储逻辑模型；
- 可以引用 PLCopen XML 文档。

因此可能出现：

```text
AutomationML / CAEX 外层
        ↓
引用 / 包含
        ↓
IEC 61131-10 / PLCopen Logic
```

这正说明：

> “一份工程同时有多个标准成分”是正常现象。

建立：

```text
AutomationML_CAEX_Profile
AutomationML_Logic_Profile
```

重点识别：

- CAEX namespace / schema；
- CAEXFile；
- InstanceHierarchy；
- InternalElement；
- ExternalInterface；
- InternalLink；
- RoleClass / SystemUnitClass；
- 外部文档引用；
- IEC 61131-10 / PLCopen logic references。

---

# 5. Layer D：候选控制模型 / 邻接工业 XML

这些不应该和 IEC 61131-10 平级强制竞争。

只有发现明显指纹时才进一步检测。

---

## D1. IEC 61499 family

IEC 61499 面向：

> 分布式工业控制的 Function Block 模型。

它和 IEC 61131-3 有相似的 Function Block / network 概念，但体系不同。

IEC 61499-2 定义工具支持与工具间信息交换要求。

第一版策略：

```text
IEC61499_Semantic_Candidate_Profile
```

先做：

- Function Block Network 语义指纹；
- Device / Resource / Application 等词汇；
- Event / Data connection 区分；
- Basic / Composite FB 等结构；

除非取得可靠的官方机器可读 Grammar / Schema，否则：

> 不要声称“严格符合 IEC 61499 XML”。

只报告：

```text
IEC61499 semantic resemblance
```

---

## D2. OPC UA UANodeSet XML

OPC UA Part 6 定义 UANodeSet XML Schema。

其根通常是：

```text
UANodeSet
```

里面定义：

- Nodes
- Attributes
- References
- NamespaceUris
- Models

PLCopen / OPC Foundation 还定义了：

```text
OPC UA Information Model for IEC 61131-3
```

因此如果 BEH XML 中出现 OPC UA NodeSet 内容：

不要误认为：

```text
IEC61131-10图形连接
```

应识别为：

```text
OPC_UA_NodeSet_Profile
PLCopen_OPCUA_IEC61131_Profile
```

这属于“信息模型 / 通信映射”，不是 FBD 图形 XML 本身。

---

# 6. Layer E：电力领域辅助 XML 标准

由于 BEH 属于电力 / PLC 相关场景，第一版至少应该能识别下面两类。

但它们属于：

```text
Auxiliary Domain Profiles
```

不是主 PLC 图元标准。

---

## E1. IEC 61850-6 SCL

IEC 61850-6 定义：

> 电力自动化系统中 IED、通信、功能结构及关系的 XML 配置描述语言 SCL。

官方提供：

- SCL XSD；
- 示例 SCL；
- Namespace Definition 等 Code Components。

如果 XML 中出现明显 SCL 指纹：

```text
SCL
IED
AccessPoint
Server
LDevice
LN
Substation
Communication
DataTypeTemplates
```

应标记：

```text
IEC61850_SCL_Profile
```

不要误当成 PLC 图元连接。

---

## E2. IEC 61970-552 CIMXML

IEC 61970-552 定义：

> 基于 CIM / RDF 的电力系统模型 XML 交换格式。

如果出现：

```text
rdf:RDF
cim:
CIM namespace
```

或相关结构，应识别成：

```text
IEC61970_CIMXML_Profile
```

它主要描述电网模型，不是 PLC FBD 图元关系。

---

# 7. 第一版“标准库”优先级

必须按优先级实现，不要同时做成一个巨型工程。

---

## P0：第一里程碑必须有

```text
1. IEC 61131-10:2019
2. PLCopen XML TC6 v2.x / v2.01
3. IEC 61131-3 Semantic Profile
4. AutomationML / CAEX / IEC 62714-4
5. OMG XMI / Eclipse EMF serialization profile
```

---

## P1：发现特征时再深入

```text
6. IEC 61499 semantic profile
7. OPC UA UANodeSet
8. PLCopen OPC UA IEC61131-3 Information Model
```

---

## P2：电力领域辅助识别

```text
9. IEC 61850-6 SCL
10. IEC 61970-552 CIMXML
```

---

# 8. 为什么 XMI / EMF 必须进入 P0

这是本轮研究后必须新增的一项。

原因：

BEH 是 Java / OpenJDK 应用。

大量 Java / Eclipse 建模工具会使用：

```text
Eclipse Modeling Framework (EMF)
```

而 EMF 默认常使用：

```text
XMI
```

作为模型序列化格式。

常见指纹：

```text
xmlns:xmi
xmi:version
xmi:id
xmi:type
xsi:type
href
```

如果真实 BEH XML 是：

```text
IEC 61131语义
+
厂商Ecore模型
+
XMI序列化
```

而检测器只认识 IEC 61131-10：

就会错误地把大量结构都标为：

```text
UNKNOWN
```

实际上它们可能只是：

> “厂商模型通过标准 XMI/EMF 序列化”。

所以最终报告要允许：

```text
Serialization substrate:
XMI/EMF = HIGH

PLC semantic resemblance:
IEC61131-3 = HIGH

Strict IEC61131-10:
LOW

Vendor metamodel:
HIGH
```

这是非常有价值的结论。

---

# 9. 标准 Profile 数据库怎么建立

不要靠 Codex 手写一份“我记得 IEC 有这些字段”。

优先从官方机器可读资源建立。

目录：

```text
standards/
├── registry.json
│
├── iec61131_10/
│   ├── metadata.json
│   ├── schemas/
│   └── fingerprint.json
│
├── plcopen_tc6/
│   ├── metadata.json
│   ├── schemas/
│   └── fingerprint.json
│
├── automationml/
│   ├── metadata.json
│   ├── schemas/
│   └── fingerprint.json
│
├── xmi/
│   ├── metadata.json
│   ├── schemas/
│   └── fingerprint.json
│
└── auxiliary/
```

每个 `metadata.json` 至少记录：

```text
standard_name
edition
source_url
download_date
namespace
schema_files
license_note
sha256
profile_version
```

---

# 10. 不要把购买的标准全文塞进代码仓

只使用：

- 官方公开 machine-readable Code Components；
- 官方公开 XSD；
- 官方公开 sample；
- 官方公开 preview 中允许使用的信息；
- 自己整理的“指纹元数据”。

不要：

- 把受版权保护的整本 IEC 标准正文复制进项目；
- 把购买的标准 PDF 提交到代码仓；
- 让生成程序大量复制标准正文。

---

# 11. 超大 XML 的核心要求：必须流式解析

真实 BEH XML 可能：

```text
50MB
100MB
甚至更大
```

因此：

> **禁止第一版把整个 XML 构造成 DOM。**

推荐：

```text
SAX
StAX
lxml.iterparse
其他 streaming pull parser
```

目标：

```text
峰值内存
尽量与XML总大小脱钩
```

建议实现两阶段扫描。

---

# 12. Pass 1：快速格式指纹扫描

Pass 1 只做轻量统计。

收集：

```text
XML declaration
encoding
root QName
DOCTYPE
processing instructions

namespace declarations
schemaLocation
noNamespaceSchemaLocation

元素 QName 频次
属性 QName 频次
namespace 频次

父元素 → 子元素组合
深度统计
路径签名
```

例如：

```text
/project/types/pous/pou/body/FBD/block
```

不记录业务值。

---

# 13. Pass 2：结构 / 引用机制扫描

只有必要时执行第二遍。

识别：

```text
ID-like attributes
IDREF-like attributes
href
xmi:id
xmi:type
xsi:type

localId
refLocalId

source
target
from
to
ref
reference
port
pin
relation
connection
net
```

这里不能单纯看字段名字。

需要同时统计：

```text
属性出现在哪些元素
值是否指向文件内已有ID
引用命中率
引用是一对一还是一对多
父子作用域
```

注意：

> 这一阶段仍然只是“结构指纹”，不是直接认定某字段就是 BEH 可见连线。

---

# 14. 对每个标准建立五类指纹

每个 Profile 至少包含：

---

## Fingerprint 1：Identity

强证据：

```text
namespace
schemaLocation
root QName
DOCTYPE
XMI namespace
CAEX namespace
```

---

## Fingerprint 2：Vocabulary

例如：

```text
block
pou
connector
continuation
connectionPointIn
connectionPointOut
```

但单个名字只能提供弱证据。

---

## Fingerprint 3：Grammar / Structure

重点看：

```text
谁能包含谁
哪些元素常成组出现
结构路径
```

结构证据权重大于单纯名称。

---

## Fingerprint 4：Reference Model

例如：

```text
localId / refLocalId
ID / IDREF
href
xmi:id
XMI cross-reference
InternalLink
OPC UA Reference
```

这对后续 BEH 连线研究非常重要。

---

## Fingerprint 5：Semantic Roles

例如：

```text
POU
Block
Port
Input / Output
Connection
Network
Connector
Continuation
DataSource
DataSink
```

即使 BEH 把：

```text
Connection
```

重命名成：

```text
relation
```

仍可能通过结构角色识别：

```text
role_equivalent = IEC61131_10.Connection
```

---

# 15. 必须允许“名字不一样但角色很像”

不要实现成：

```text
标签名相同
=> 标准

标签名不同
=> 私有
```

例如真实 BEH：

```text
<relation ...>
```

标准里可能对应：

```text
Connection
```

如果 BEH relation：

- 连接两个端点；
- 一端是 Producer / OUT；
- 一端是 Consumer / IN；
- 位于 Network / graph scope；
- 没有独立 location；
- 引用方式与标准连接高度类似；

则报告可以：

```text
Element:
vendor:relation

Exact name match:
NO

Structural role match:
83%

Candidate mapping:
IEC61131-10.Connection

Confidence:
MEDIUM
```

不要直接把它升级成事实。

---

# 16. 标准匹配分数不能只有一个

每个标准至少输出：

```text
identity_score
vocabulary_score
structure_score
reference_model_score
semantic_role_score
conflict_score
overall_similarity
confidence
```

例如：

```json
{
  "profile": "IEC61131-10:2019",
  "identity_score": 25,
  "vocabulary_score": 81,
  "structure_score": 88,
  "reference_model_score": 74,
  "semantic_role_score": 92,
  "conflict_score": 18,
  "overall_similarity": 78,
  "confidence": "HIGH"
}
```

---

# 17. “成分占比”另外计算，不能拿 similarity 代替

为了满足用户直观理解，再额外做：

```text
Coverage Classification
```

把扫描到的结构分类为：

```text
STANDARD_EXACT
STANDARD_ALLOWED_EXTENSION
STANDARD_ROLE_EQUIVALENT
KNOWN_FOREIGN_STANDARD
VENDOR_PRIVATE
UNKNOWN
```

然后分别统计：

### 按元素出现次数

```text
element_occurrence_coverage
```

### 按唯一 QName 数

```text
unique_qname_coverage
```

### 按结构 Path Signature 数

```text
structure_signature_coverage
```

### 可选：按 XML 字节近似占比

```text
approx_byte_coverage
```

最终才允许给小白显示：

```text
约 68% 的元素实例可直接用某标准解释
约 17% 位于标准扩展区域
约 10% 为厂商私有结构
约 5% 暂时未知
```

必须标注：

> 这是“结构覆盖统计”，不是证明文件由这些标准按比例拼成。

---

# 18. 厂商扩展必须再分三类

不要所有未知都叫“私有”。

---

## Extension Type A：标准明确允许的扩展

例如标准 extension point / addData 一类位置。

报告：

```text
STANDARD_EXTENSION_POINT
```

其内部内容可能私有，但：

> 私有内容出现的位置本身是标准允许的。

---

## Extension Type B：已知其他标准

例如：

```text
AutomationML中引用PLCopen XML
OPC UA XML
IEC61850 SCL
```

报告：

```text
KNOWN_FOREIGN_STANDARD
```

---

## Extension Type C：真正未知

找不到可靠标准来源。

报告：

```text
UNKNOWN_VENDOR_CANDIDATE
```

不要强行解释。

---

# 19. 需要做“XML 区域分割”

大型 XML 不应只输出整个文件一个分数。

应该识别：

```text
Region / Structural Path Family
```

例如：

```text
/project/types/pous/*
→ 高度PLCopen/IEC

/project/addData/vendor/*
→ 厂商扩展

/project/vendorGraph/relation/*
→ 私有连接模型

/AutomationML/*
→ CAEX
```

最终 HTML 报告展示：

```text
标准区域地图
```

这样后续我们真正逆向 BEH 时，只需要盯：

> 私有岛屿

而不用继续面对整个几十 MB XML。

---

# 20. 输出中禁止泄露工作业务数据

这个工具最终要在工作环境运行。

因此默认：

```text
--sanitized
```

报告不得包含：

- 变量真实名称；
- 产品名称；
- 设备编号；
- 参数值；
- 用户业务字符串；
- 大段原始 XML；
- 完整文件路径（可配置）。

允许输出：

```text
标准 namespace
标准元素名
厂商标签名（可选脱敏）
元素统计数量
结构 Hash
路径模式（脱敏）
Profile 分数
```

对于未知厂商标签，支持：

```text
--hash-private-names
```

例如：

```text
vendor element #A17
vendor element #B09
```

---

# 21. 输出文件

建议：

```text
output/
├── fingerprint_report.json
├── fingerprint_report.html
├── standard_scores.json
├── namespace_inventory.json
├── structural_regions.json
├── extension_inventory.json
├── unknown_families.json
└── run_metadata.json
```

---

# 22. JSON 报告必须包含的顶层结构

```json
{
  "document_identity": {},
  "serialization_substrate": {},
  "standard_profile_scores": [],
  "coverage": {},
  "regions": [],
  "extensions": [],
  "unknown_families": [],
  "recommended_primary_standard": null,
  "recommended_secondary_profiles": [],
  "confidence": {},
  "next_action": {}
}
```

---

# 23. HTML 报告顶部必须让小白一眼看懂

示例：

```text
BEH XML 标准成分检测

文件大小：
67.3 MB

明确声明：
厂商自定义 XML

序列化底座：
XMI / EMF 相似度：HIGH

PLC语义：
IEC 61131-3：VERY HIGH

PLC交换格式：
IEC 61131-10：MEDIUM-HIGH
PLCopen TC6 2.x：MEDIUM

工程容器：
AutomationML：LOW

电力辅助格式：
IEC 61850 SCL：NOT DETECTED
CIMXML：NOT DETECTED

结构覆盖：
标准直接解释：64%
标准允许扩展：14%
厂商私有候选：17%
未知：5%

最值得后续研究：
1. vendor relation family #3
2. vendor graph reference family #7
3. unknown reference structure #12

推荐下一步：
以 IEC 61131-10 为主要参照，
同时保留 XMI/EMF 序列化适配层。
```

注意：

这些只是示例，不得写死。

---

# 24. 相似度算法的原则

不要用 AI 做主要打分。

检测器运行时必须固定、可重复。

建议：

```text
规则评分
+
Schema证据
+
统计指纹
```

AI以后可以读报告帮助解释，但：

> 同一个 XML 连续运行两次，应得到同样的核心检测结果。

---

# 25. 推荐评分权重初版

可先使用：

```text
Identity / Namespace      25%
Grammar / Structure       25%
Reference Model           20%
Semantic Roles            15%
Vocabulary                10%
Schema Validation          5%
```

但：

- 如果 identity 是厂商 namespace，不意味着其他分数必须归零；
- exact schema validation 只在 schema identity 高可信时执行；
- 存在严重冲突时必须扣 conflict score。

后续通过测试集再调整权重。

---

# 26. 负向证据必须存在

否则检测器容易“什么都像 IEC”。

例如：

```text
出现 block
```

不能获得很高 IEC 分。

因为很多标准都有 block。

真正高权重的是：

```text
多个特征组合
+
正确父子结构
+
正确引用模型
+
正确作用域
```

Profile 中必须存：

```text
incompatible_patterns
```

---

# 27. 第一阶段验证：先拿官方小样本验证检测器

**这一步必须在 Mega XML 之前。**

顺序：

```text
官方 IEC 61131-10 XSD / example
        ↓
Detector
        ↓
应识别为 IEC61131-10

PLCopen TC6官方example
        ↓
Detector
        ↓
应识别为 PLCopen TC6

AutomationML官方example
        ↓
Detector
        ↓
应识别为 AutomationML/CAEX

XMI官方/EMF sample
        ↓
Detector
        ↓
应识别为 XMI
```

同时做交叉误判：

```text
UANodeSet
不应被识别成 IEC61131-10 图形 XML

SCL
不应被识别成 PLCopen

CIMXML
不应被识别成 FBD XML
```

---

# 28. 第二阶段：公开真实工程验证

官方 example 通常太干净。

检测器通过第一阶段后，再找：

```text
公开真实 PLCopen / IEC 项目
公开 AutomationML 工程
公开 4diac / IEC61499 项目
```

目的：

> 验证工具不是只会官方最小样例。

这一阶段仍然不要自己生成巨型 XML 作为唯一证明。

---

# 29. 第三阶段：才生成巨型标准压力 XML

只有阶段 1、2 都通过以后，才开发：

```text
Mega XML Generator
```

生成：

```text
10MB
50MB
100MB
```

目标不是验证“标准理解是否正确”。

目标是验证：

```text
超大文件情况下
streaming是否稳定
索引是否稳定
内存是否稳定
性能是否稳定
误判率是否随规模上升
```

必须同时生成：

```text
Ground Truth
```

但注意：

> Mega XML Generator 和 Detector 不得共享核心分类逻辑，否则会形成自证循环。

---

# 30. 第四阶段：才开发标准连线取证查询器

当检测器已经告诉我们：

```text
BEH最接近哪套标准
```

再以那套标准为基线，开发：

```text
Connection Forensics Query Tool
```

先在：

```text
官方小样本
→ 公开真实工程
→ Mega XML
```

上验证。

然后才带回工作环境。

---

# 31. 第五阶段：真实 BEH 只做 Adapter

进入公司环境后：

```text
成熟的通用索引器
成熟的引用查询器
成熟的大文件处理
        ↓
BEH Adapter
```

这时真正需要研究的只剩：

```text
标准无法解释的 vendor/private 区域
```

而不是重新从几十 MB XML 全文找规律。

---

# 32. 最终完整路线图

严格按顺序：

```text
PHASE 0
标准资料库 / Profile Registry
        ↓

PHASE 1
XML Standard Fingerprint Detector
        ↓

PHASE 2
官方小样本交叉验证
        ↓

PHASE 3
公开真实工程验证
        ↓

PHASE 4
在工作环境扫描真实 BEH XML
只输出脱敏成分报告
        ↓

PHASE 5
确定 BEH 主标准 / 序列化底座 / 私有岛屿
        ↓

PHASE 6
Mega Standard XML Generator
做规模压力测试
        ↓

PHASE 7
Standard Connection Forensics Tool
        ↓

PHASE 8
BEH Adapter
        ↓

PHASE 9
重新接回当前 HTML / 后续图形工作台
        ↓

PHASE 10
AI解释图元
```

注意：

实际 PHASE 4 和 PHASE 6 的先后可根据“真实 BEH 环境何时能运行 Detector”调整；

但是永远不允许：

> 一上来先造巨大 XML，再用自己生成的数据证明自己的标准理解正确。

---

# 33. Milestone 1：Codex 当前只做什么

Codex 当前只完成：

```text
Standard Registry v0.1
+
Streaming Fingerprint Scanner v0.1
+
Report Generator v0.1
```

第一轮支持：

```text
IEC61131-10
PLCopen TC6
IEC61131-3 semantic profile
AutomationML/CAEX
XMI/EMF
```

并预留辅助 Profile 接口。

暂时不要开发：

- BEH relation parser；
- HTML renderer；
- AI解释；
- Mega XML；
- BEH Writer；
- UI 自动化；
- 图元生成；
- 完整 Connection Forensics。

---

# 34. Milestone 1 验收标准

必须满足：

### Functional

1. 能扫描至少 100MB XML，不构造全量 DOM。
2. 能输出 namespace / schema / root / QName 统计。
3. 能输出标准 Profile 独立相似度。
4. 能识别 XMI/EMF 底座。
5. 能识别 IEC61131-10 与 PLCopen TC6，不混为同一个 Profile。
6. 能识别 AutomationML/CAEX。
7. 能输出 vendor/private / unknown 家族。
8. 能按 XML structural region 进行分类。
9. 默认生成 sanitized report。
10. 同一个文件重复运行结果稳定。

### Negative Tests

必须证明：

```text
OPC UA UANodeSet
不会因为含PLCopen IEC61131词汇
就被判成IEC61131-10图形工程。

IEC61850 SCL
不会被判成PLCopen。

XMI文件
不会仅因为有Node/Relation
就被判成PLC XML。
```

---

# 35. 性能要求

初版目标：

```text
100MB XML
在普通开发机可完成扫描
内存使用不能随文件大小线性无限增长
```

报告中必须记录：

```text
scan_time
peak_memory
element_count
attribute_count
max_depth
file_size
```

不要现在制定不现实的毫秒级指标。

先建立基准。

---

# 36. 安全要求

XML 解析默认：

- 禁止外部实体；
- 禁止 XXE；
- 不自动访问 schemaLocation 网络地址；
- 不执行任何 XML 中的脚本 / URI；
- 不加载未知外部资源；
- schema 文件由本地标准 Registry 控制。

---

# 37. 调试模式

增加：

```text
--explain-profile IEC61131-10
```

输出：

```text
为什么给了78分？

+ namespace resemblance
+ 17个高权重结构命中
+ connection role structure匹配
- root不符合
- 发现XMI序列化
- 21%结构位于vendor namespace
```

这样以后用户和 AI可以讨论“为什么判断像 IEC”，而不是只看到一个神秘百分比。

---

# 38. 未知结构聚类

对于无法识别的元素，不要一股脑输出几万个 tag。

按：

```text
namespace
父节点类型
子节点集合
属性集合
引用模式
深度
```

聚成：

```text
Unknown Family #1
Unknown Family #2
...
```

例如：

```text
Unknown Family #7
出现：18,221次
特点：
- 无location
- 两个ID-like引用
- 常位于某graph parent下
- 与known block产生引用

Potential role:
relation-like

不是最终结论。
```

这一步以后会直接帮助 BEH 连线逆向。

---

# 39. 一个重要的输出：Reference Mechanism Inventory

单独输出：

```text
reference_mechanisms.json
```

统计文件里的引用方式：

```text
xmi:id / href
localId / refLocalId
ID / IDREF
source / target
from / to
自定义 xxxRef
文本形式的UUID引用
相对路径引用
```

并统计：

```text
引用命中率
一对一 / 一对多
跨parent比例
跨Network候选比例
```

注意：

> 现在不解释它是不是“可见连线”。

只把引用机制地图建立起来。

这会成为后续 Connection Forensics 的直接底座。

---

# 40. Codex 不得做的错误假设

禁止：

1. `relation` 一定等于 IEC Connection。
2. `continuation` 名字一样就必然完全遵循 PLCopen。
3. 有 `_location` 才是图元。
4. 没 `_location` 就是连线。
5. 只要 namespace 自定义，就认为100%私有。
6. 只要出现 IEC 61131 词汇，就认为IEC61131-10。
7. 标准 Profile 分数必须相加等于100%。
8. Unknown 一定是 vendor proprietary。
9. 标准相似度高就等于通过 XSD。
10. 一个 XML 只能属于一种标准。
11. 生成器和检测器共享同一套判断逻辑做自证。
12. 因为 BEH 是 Java 就断言一定使用 EMF/XMI。

---

# 41. 推荐 CLI

```text
xml-fingerprint scan <file.xml>
```

默认：

```text
--sanitized
--profiles core
```

完整：

```text
xml-fingerprint scan project.xml \
  --profiles all \
  --out output/
```

查看未知结构：

```text
xml-fingerprint unknowns output/fingerprint_report.json
```

解释评分：

```text
xml-fingerprint explain output/fingerprint_report.json \
  --profile IEC61131-10
```

查看引用机制：

```text
xml-fingerprint refs output/reference_mechanisms.json
```

---

# 42. 推荐实现结构

```text
xml-standard-detector/
├── README.md
├── standards/
│   ├── registry.json
│   ├── iec61131_10/
│   ├── plcopen_tc6/
│   ├── automationml/
│   ├── xmi/
│   └── auxiliary/
│
├── src/
│   ├── scanner/
│   ├── namespace_probe/
│   ├── vocabulary_probe/
│   ├── structure_probe/
│   ├── reference_probe/
│   ├── profile_engine/
│   ├── region_classifier/
│   ├── privacy/
│   └── report/
│
├── tests/
│   ├── official_samples/
│   ├── cross_profile/
│   └── synthetic/
│
└── output/
```

---

# 43. 技术栈建议

第一版目标是：

> 可靠 + 容易调试 + 能跑大 XML。

可以优先：

```text
Python 3
+
lxml.iterparse / SAX
+
SQLite
```

其中：

- streaming parser：负责 XML 扫描；
- SQLite：用于需要落盘的统计/索引；
- HTML：只用于结果报告，不用于重画 BEH。

如果后续性能不足，再将核心 scanner 换成：

```text
Rust / Java StAX
```

不要第一轮为了追求性能提前复杂化。

---

# 44. 标准官方来源（Codex 构建 Registry 时优先使用）

## IEC 61131-10 / PLCopen

PLCopen IEC 61131-10：
https://www.plcopen.org/standards/logic/iec-61131-10/

IEC 61131-10 Code Components：
https://www.plcopen.org/standards/xml-echange/code-components/

PLCopen XML Exchange：
https://www.plcopen.org/standards/xml-echange/

PLCopen Downloads / TC6 v2.01 XSD：
https://www.plcopen.org/downloads/

IEC 61131-3：
https://webstore.iec.ch/

---

## AutomationML / CAEX

AutomationML：
https://www.automationml.org/about-automationml/automationml/

AutomationML Specifications：
https://www.automationml.org/about-automationml/specifications/

IEC 62714-4：
https://webstore.iec.ch/en/publication/28979

---

## XMI / EMF

OMG XMI：
https://www.omg.org/spec/XMI

Eclipse EMF XMI：
https://download.eclipse.org/modeling/emf/emf/javadoc/

---

## IEC 61499 / 4diac

IEC：
https://webstore.iec.ch/

Eclipse 4diac：
https://eclipse.dev/4diac/

---

## OPC UA NodeSet

OPC Foundation Part 6：
https://reference.opcfoundation.org/Core/Part6/

PLCopen OPC UA IEC61131-3：
https://reference.opcfoundation.org/PLCopen/

---

## IEC 61850

IEC 61850 Code Components：
https://iec61850.dvl.iec.ch/61850-resources/61850-code-components/

SCL：
https://iec61850.dvl.iec.ch/what-is-61850/technical-principles/scl-description-language/

---

## CIMXML

IEC 61970-552：
https://webstore.iec.ch/en/publication/25939

---

# 45. 最终完成标志

本轮不是在真实 BEH 上“找出 relation 规律”才算完成。

本轮完成标志是：

> 给任意一个几十 MB / 100MB 级 XML，固定程序能够在不依赖 AI运行时参与的情况下，快速、可解释、可重复地回答：
>
> **它的格式底座是什么、最像哪些自动化标准、哪些部分是标准、哪些是标准扩展、哪些是其他已知标准、哪些是厂商私有候选、哪些完全未知。**

并且能够生成：

```text
下一步最值得研究的“私有岛屿清单”
```

只有做到这一步，才进入后续：

```text
官方标准小样本
→ 真实公开工程
→ 大规模压力测试
→ Connection Forensics
→ BEH Adapter
```

---

# 46. 一句话总纲

> **先不要在几十 MB BEH XML 中直接寻找“线到底是什么”。先把整份 XML 做成一张标准成分地图：识别它使用的 XML 底座、PLC 标准语义、工程容器、辅助领域标准和厂商私有区域；把已经能被公开标准解释的部分排除掉，只对剩下的私有岛屿做逆向。这样后续 Codex 不再面对一片海，而只面对少数真正需要破解的岛。**
