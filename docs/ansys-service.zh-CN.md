# ANSYS 服务接入

服务 Swagger：<http://dev.htcmc.site/docs>。方法字段以 `GET /sim/methods` 的 `params_schema` 为准，单位为 mm、MPa、s、℃。

## 运行当前两份 STEP

先在 HIPForm 项目目录执行 `uv sync --locked`。然后在包含 `cavity.step` 和 `capsule.step` 的目录运行：

```bash
/Users/zeroduan/Documents/GitHub/HIPForm/.venv/bin/python \
  /Users/zeroduan/Documents/GitHub/HIPForm/scripts/run_ansys.py \
  --out simulation-runs/ansys-001 --wait
```

程序自动读取固定文件名，上传是后台传输过程，不需要在页面手动上传；不使用 `assembly.step`。`cavity.step` 是目标成品，`capsule.step` 是包套及抽气管材料实体。

默认连接 `http://dev.htcmc.site`，可用 `--service-url` 指定其他部署；`--input-dir` 默认为当前目录。输出目录必须是新目录。默认使用服务工艺、材料和 6 mm 网格；只有显式给 `--mesh-size` 时才覆盖网格尺寸。省略 `--wait` 时提交后查询一次，任务可在服务端继续执行。

已有任务只取回结果，不重新提交：

```bash
/Users/zeroduan/Documents/GitHub/HIPForm/.venv/bin/python \
  /Users/zeroduan/Documents/GitHub/HIPForm/scripts/run_ansys.py \
  --job-id EWzrmOc9Az0 --out simulation-runs/ansys-result-001 --wait
```

`accepted.json` 保存提交任务号；`status.json` 保存真实状态，`result.json` 为服务结果，`job.log` 为日志，`artifacts/` 保存服务提供的工件。`summary.json` 保留 `fidelity`，明确标记接触间隙未评估。当前入口取回原始结果，不把缺失的变形场伪造成既有本地报告所需的数据。

连接中断后，若已有任务号，应使用 `--job-id` 查询。POST 不会自动重试，避免重复启动计算。Sakura Frp 的 503 属于服务转发错误；已打开的 Swagger 页面不能证明新请求成功。

## 在上游 Python 程序中调用

```python
from pathlib import Path
from hipform.ansys_client import AnsysClient

with AnsysClient("http://dev.htcmc.site") as service:
    geometry = {
        "capsule_step": service.upload_step(Path("capsule.step")),
        "cavity_step": service.upload_step(Path("cavity.step")),
    }
    accepted = service.submit("full3d-hip", {"geometry": geometry})
    # 立即持久化 accepted["id"]；后续调用 get_job(id) 查询。
    print(accepted)
```

调用链为 `POST /uploads` → `POST /sim/full3d-hip` → `GET /jobs/{id}` → `GET /jobs/{id}/result` / `/artifacts`。客户端保留服务 `fidelity`，不会退回本地 surrogate 求解。现有本地 `/api/jobs` 尚未切换到该远程入口。

## 默认参数和当前能力

2026-09-10 实际服务返回的默认值：

| 参数 | 值 | 生效范围 |
|---|---|---|
| 工艺峰值 | 900℃、120 MPa | full3d-hip 仅取峰值作单个静力工况 |
| 保温保压 | 10,800 s | 默认致密化曲线；full3d-hip 不计算保温时间效应 |
| 初始相对密度 | 0.65 | 独立 densification；full3d-hip 尚无此参数 |
| 极限相对密度 | 0.995 | 独立 densification |
| TC4 室温屈服 / 抗拉 | 800 / 950 MPa | 材料库；线弹性 FEM 不使用塑性强度 |
| 20 钢室温屈服 / 抗拉 | 250 / 410 MPa | 抗拉默认与用户要求的 400 MPa 不同 |
| TC4 900℃ E / 泊松比 | 70,000 MPa / 0.34 | 本次 APDL 输入实际使用 |
| 20 钢 900℃ E / 泊松比 | 120,000 MPa / 0.30 | 本次 APDL 输入实际使用 |

阶段一 FEM 忽略 `materials.overrides`，不能声称传入强度覆盖已生效。高温材料值是服务提供的文献初值，尚未用当前粉末和包套实验标定。`fidelity: real` 是方法内核标记，不等同于经过工程验证。

当前立方体应使用 `full3d-hip`，不能改成轴对称模型以缩短计算。该方法为 `smoke`：SOLID45、线弹性、外表面均压、单静力工况。`deformed_stl` 固定为 `null`，不输出成型粉末 STEP。`densification` 和 `shrinkage-estimate` 是独立快速计算，不是三维耦合收缩；`compensate` 输出的是目标均匀放大的设计模型，也不是预测成品。

## 本次真实调用

| 方法 | 任务号 | 结果 |
|---|---|---|
| full3d-hip，默认 6 mm | EWzrmOc9Az0 | succeeded / smoke，50 秒 |
| densification | rMRJx1Yb0PQ | 0.65 → 0.9723319724，达到 0.97 |
| shrinkage-estimate | fPpUn43ylWg | 假设初始边长 302 mm，均匀收缩估算为 264.0624 mm |
| material-query，TC4 | -Cc7KeXWhGI | succeeded / real |
| material-query，20 钢 | XVGS0HOETUw | succeeded / real |
| mesh，25 mm | Nq0-5Fj5twU | failed：HXT 3D mesh failed |

3D 返回 `von_mises_max_mpa = 28.559788`，`displacement_max_mm = -0.166746`。后者是服务原始值，不能当作非负位移幅值；本次工件的 APDL 后处理对有符号分量取极值，需要服务端核对修复。25 mm 网格失败而默认 6 mm 求解成功，不能仅凭粗网格失败判定包套设计不合格。

## 达成成型验证还需服务端补齐

- 从包套主内腔推导装填粉末，目标 cavity 仅用于成品 DIFF；识别并虚拟封口直圆抽气管，允许残余管内空间。
- 三维大变形、多孔粉末致密化、包套高温塑性/蠕变、可分离接触，以及完整温度/压力/时间历程；保留质量守恒和实际生效的材料配置。
- 分别导出终态粉末 `cavity+<jobid>.step`、终态包套及内壁表面，提供位移、密度场、坐标系、单位和几何重建误差。
- 目标 DIFF 与内壁间隙分开计算。间隙正值为分离、负值为穿透；平面 0～10 mm、非平面 0～20 mm。超限时返回位置、面类型、间隙、法向及有依据的设计调整建议。

本次 `succeeded` 只证明服务计算流程完成，不表示包套设计合格。缺少实际终态模型时，成型 STEP、目标 DIFF 和真实内壁间隙均不可评估。
