# HIPForm 仿真模型

HIPForm 接在包套生成流程之后。输入为包套材料实体 STEP 与已补偿型腔 STEP；输出为理想温压条件下的 TC4 粉末预测终态和相对初始型腔的尺寸偏差。首次实现用于建立可修改、可复现的数值流程。

**模型状态：理想参数、未经材料试验标定、简化小应变模型。** 不将数值完成或采样公差满足限值等同于工程验收。默认终态仍附着包套，未模拟去包套后的应力释放。大应变或反算密度越界会在报告中明确提示。

## 安装与运行

Python 3.12，依赖全部通过 Python 包安装，不依赖 FreeCAD GUI、Claude CLI 或商业求解器。Gmsh 使用其 Python wheel 中附带的原生 OpenCASCADE/网格库；scikit-fem 负责有限元组装，SciPy 负责线性代数，Trimesh 负责距离计算，Plotly 生成不需要联网的报告。

```bash
uv sync
uv run hipform run \
  --cavity /path/to/compensated-cavity.step \
  --capsule /absolute/path/to/hip_capsule_complete.step \
  --config config/hip-ideal.yaml \
  --output simulation-runs/actual-001
```

必须使用包套材料本体文件，不能把包含型腔与包套的装配 STEP 传给 `--capsule`。输出目录必须为空，避免覆盖已有结果。`run` 返回 0 表示流程完成，返回 2 表示配置、几何或求解失败；公差状态另见 `result.json` 的 `sampled_tolerance_status`。

只准备网格和面编号，将 `run` 换成 `mesh`。先查看生成的 `mesh-info.json` 中 `reference_faces`（面类型、中心坐标和面积），再把角度区域面编号填进 `angular_face_ids`。这些是 Gmsh 布尔分割后的面编号，不是 FreeCAD 的 FaceN；输入几何或封口变化后须重新核对。

仓库不包含原项目的 STEP。无需外部输入的公开演示使用 `uv run python scripts/run_demo.py`。如果已有可用型腔，以下命令也可生成明确标注的演示包套，验证完整程序：

```bash
uv run hipform example \
  --cavity /path/to/compensated-cavity.step \
  --output simulation-runs/example-input
uv run hipform run \
  --cavity /path/to/compensated-cavity.step \
  --capsule simulation-runs/example-input/synthetic-capsule.step \
  --config config/hip-ideal.yaml \
  --output simulation-runs/example-run
```

示例包套是扩展包围盒减去型腔，最小边界余量默认 3 mm；它局部很厚，不代表 HIP-demo 生成的随形薄壁包套。报告记录合成来源和文件哈希，不可将其结果套用到实际包套。

## 可修改的默认参数

主配置：`config/hip-ideal.yaml`。单位统一为 mm、MPa、s、摄氏度；计算 Arrhenius 项时转为开尔文。STEP 声明单位由 OpenCASCADE 归一化成 mm。配置不接受未知字段、非有限值和不合理的时间顺序。

| 项目 | 默认值 | 配置位置 |
|---|---|---|
| 初始粉末相对密度 | 0.65 | `powder.initial_relative_density` |
| 自由致密化极限密度 | 0.995 | `powder.limiting_relative_density` |
| TC4 全致密材料密度 | 4.43e-6 kg/mm³ | `powder.solid_density_kg_per_mm3` |
| 峰值温度 | 920°C | `cycle` |
| 峰值压力 | 100 MPa | `cycle` |
| 升温加压 | 60 min | `cycle` |
| 保温保压 | 120 min | `cycle` |
| 降温后卸压 | 总历程 270 min | `cycle` |
| 网格目标尺寸 | 6 mm | `solver.mesh_size_mm` |
| 最大时间步 | 120 s | `solver.time_step_s` |
| 小应变提示阈值 | 5% | `solver.strain_warning_threshold` |
| 普通区域偏差绝对值 | 10 mm | `tolerances.flat_mm` |
| 指定角度区域偏差绝对值 | 20 mm | `tolerances.angular_mm` |
| 角度区域 | 初始为空 | `tolerances.angular_face_ids` |

全部温压点可以增删，分段线性插值，求解时间网格保留所有工况转折点。默认材料参数是展示用途的假设，不是可追溯材料库。高温弹性模量、热膨胀系数、致密化速率和活化能、压力指数、包套应力松弛时间都可以修改。提高提示阈值不会使小应变理论适用于大应变。

## 封口与界面

HIP-demo 生成的包套可能保留抽气通孔，进入 HIP 需要封口。本原型检测到粉末与外部连通时拒绝求解。可以输入已经封口的 STEP，或配置一个沿 +Z 的圆柱封口实体：

```yaml
seal:
  center_mm: [0.0, 0.0, 150.0]
  radius_mm: 4.0
  height_mm: 2.0
```

以上坐标仅说明格式，必须按实际抽气管修改。`center_mm` 是圆柱底面圆心。程序要求封口与包套合并为同一实体且不侵入粉末域；失败会保留错误记录。仅外包络施加压力，密闭残余空腔按真空零表压处理。

材料间隙和抽气孔开放现在优先按 CAD 拓扑检查，不依赖粗网格是否能保留细小通孔。
粉末与包套完全分离或局部不贴合返回 `material_gap`；抽气孔开放返回 `open_capsule`。
与粉末相接的残余空腔只允许识别为封口直圆管的内部空间，复杂管道几何会保守拒绝。
完整规则、错误报告及 Swagger 接口见 [API 使用说明](api.md)。

粉末与包套共用界面节点，采用完全贴合、不允许滑移的理想化条件。没有摩擦滑移、界面分离、气体泄漏、焊缝强度、热传导梯度、屈曲和破裂模型。小应变线性四面体在薄壁弯曲和近不可压缩状态下也会产生额外离散误差。

## 数学模型

令 D 为规定的自由相对密度，温度 T 为 K，p 为外部表压：

```text
k(T,p) = k_ref * exp[-Q/R * (1/T - 1/T_ref)] * (p/p_ref)^n
dD/dt = k(T,p) * (D_limit - D)
epsilon_free = (D_initial / D)^(1/3) - 1
```

每个时间段在中点温压下积分动力学，得到粉末各向同性收缩本征应变。热膨胀与收缩的自由长度比相乘，以保持自由状态下密度与体积的一致性；通过三维有限元平衡求解实际位移。外压施加在密封包套外表面，使用六个自由度消除刚体运动，不固定整个底面。最终以一致有限元体积积分去掉无穷小刚体位移，避免参考位置随局部节点加密漂移；保留源 CAD 坐标，不执行几何缩放或表面最佳拟合。

粉末弹性模量随温度及规定密度变化；包套体积响应为弹性，偏应力采用隐式 Maxwell 松弛。该模型没有以局部静水应力反馈致密化速率，也不等价于经标定的多孔塑性或高温塑性本构。

程序分别保存“规定自由收缩密度”和由 `det(I + grad(u))` 反算的实际相对密度，后者考虑热膨胀后的全致密参考密度。两者不必相同，不能把动力学达到 0.995 误报为实际构件处处达到 0.995。质量指标是从同一体积关系重构的内部一致性检查，不能独立证明物理模型正确。反算值不裁剪到 1，便于暴露模型越界。负 Jacobian 直接导致失败。

## 公差与输出

比较的是 **预测 TC4 终态与初始已补偿型腔**，因此包括预期 HIP 收缩。不是相对未补偿最终设计零件的成品尺寸验收。

参考面未显式归入角度区时一律使用 10 mm，角度区使用 20 mm。斜面也可能是几何平面，所以不凭“是否平面”自动分组。使用双向采样点到三角面距离，输出各区最大值、P95、RMS 和最大偏差坐标；反向区域归属由最近参考面确定。采样间距可配置，超过采样预算会失败，不会偷偷降低精度。

| 文件 | 用途 |
|---|---|
| `report.html` | 离线可旋转三维叠加、偏差云图、温压与密度历程 |
| `predicted-powder.stl` | 预测粉末成形表面，单位 mm |
| `predicted-assembly.vtu` | 包套和粉末终态体网格、位移和相对密度 |
| `reference-powder.stl` | 从初始 CAD 离散得到的参考粉末表面 |
| `history.csv` | 温度、压力、密度、体积、位移及数值残差 |
| `comparison.json` | 双向表面采样偏差、分区限值与判定 |
| `mesh-info.json` | 材料、面编号、输入哈希、CAD/网格体积误差 |
| `config.resolved.json` | 本次完整配置快照 |
| `solver.json` / `result.json` | 模型适用范围、执行状态和工程验收未评估声明 |

STL 没有内置单位属性，导入其他软件时选择 mm。当前不输出重建 STEP，避免把三角面拟合引入的误差与成形误差混淆。采样最大值不是连续 CAD 表面最大值；先检查 CAD/网格体积误差，再逐步细化网格、时间步和表面采样以评估变化。

## 验证

```bash
uv run pytest -q
```

新增测试覆盖自由收缩的质量关系、零载荷、均匀本征应变、静水压力解析解、Maxwell 系数、时间节点、错误配置、密封检查、材料分区、表面双向距离与 10/20 mm 阈值。它们验证代码和离散计算，不替代材料试验或真实 HIP 工艺验证。

分别改变网格和时间步的数值敏感性检查：

```bash
uv run python scripts/check_hip_sensitivity.py \
  --cavity /path/to/compensated-cavity.step \
  --capsule simulation-runs/example-input/synthetic-capsule.step \
  --config config/hip-ideal.yaml \
  --output simulation-runs/study-001
```

该脚本分别运行配置值、较粗网格、较大时间步三组算例，输出 `sensitivity.json`。这是敏感性记录，不自动宣称网格收敛。默认理想参数下示例产生较大局部应变和部分相对密度超过 1，报告明确标为超出适用范围；即使采样偏差小于 10 mm，也不能据此验收实际 HIP 成形。

直接从数值数组生成静态预览（额外安装 Matplotlib）：

```bash
uv run --with matplotlib python scripts/render_hip_preview.py simulation-runs/example-run
```

默认比较采用距离绝对值，因此向内、向外偏差都允许落在上限以内；需要单侧公差时须改用明确的有符号距离和区域法线约定。

## 网页报告

运行 `uv run hipform publish --run simulation-runs/public-demo/run --output site` 可将完整结果导出为 GitHub Pages 网站。发布器保留所有模型适用性和工程验收未评估提示。部署与更新方法见 [Pages 说明](pages.md)。
