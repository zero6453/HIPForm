# HIPForm

Predict an idealized HIP forming state and compare it with a compensated cavity.

**[View the report](https://zero6453.github.io/HIPForm/)**

HIPForm 接收包套材料实体 STEP 和已补偿型腔 STEP，输出 TC4 粉末预测终态、三维偏差图和温压历程。普通区域限值为 **10 mm**，显式指定的角度区域为 **20 mm**。材料参数、温度、压力、时间与限值均可修改。

当前版本是未经标定的小应变仿真原型。默认 HIP 工况可能超出模型适用范围，网页会显示适用性状态；采样公差通过不代表真实工件验收通过。终态仍附着包套。

## Run

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required. On Ubuntu, install `libglu1-mesa` for Gmsh first.

```bash
uv sync --locked
uv run hipform run \
  --cavity /path/to/compensated-cavity.step \
  --capsule /path/to/sealed-capsule.step \
  --config config/hip-ideal.yaml \
  --output simulation-runs/actual-001
```

包套必须封闭且与粉末域兼容；`--capsule` 接收包套材料实体，不是整个装配。先用 `hipform mesh` 获取参考面编号，再配置 `tolerances.angular_face_ids`。未分配面使用 10 mm。

生成一个完全合成的公开演示算例：

```bash
uv run python scripts/run_demo.py
```

该示例使用自行构造的 L 形型腔和合成包套，不包含原 HIP-demo 项目的 STEP 或真实工件数据。

## Publish Reports

GitHub Pages 展示静态报告，求解在本机或 GitHub Actions 中执行。网页不提供上传 STEP 或在线求解服务。

每次推送到 `main`，Actions 会运行测试，然后生成并发布演示报告。手动运行 **Publish Report** 工作流也可更新网页。

发布自己的算例时，先生成网页目录并检查内容：

```bash
uv run hipform publish --run simulation-runs/actual-001 --output site
```

`site/index.html` 包含三维叠加、偏差、温压与密度曲线，以及结果下载链接。导出时仅选择报告所需文件，并将来源文件的绝对路径改成文件名。

将确认可公开的 `site/` 内容复制进仓库的 `reports/latest/` 后提交并推送，工作流会改为部署这份报告；移除 `reports/latest/` 则恢复自动演示。**公开报告包含可下载的几何、配置和计算结果**，请仅提交可公开的数据。详见 [Pages 发布说明](docs/pages.md)。

## Documentation

- [模型、配置与公差定义](docs/simulation.md)
- [GitHub Pages 部署与更新](docs/pages.md)

```bash
uv run pytest -q
uv build
```

HIPForm 最初作为 [HIP-demo](https://github.com/YuShenLiu06/HIP-demo) 的独立仿真扩展开发。本仓库只包含该仿真模块，不包含原项目的 CAD 生成器、历史记录或私有 STEP。第三方依赖遵循各自许可证。
