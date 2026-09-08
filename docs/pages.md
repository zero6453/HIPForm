# GitHub Pages Reports

报告网址：https://zero6453.github.io/HIPForm/

网页展示三维叠加、采样偏差、公差区域、温压与密度历程、模型适用性和结果下载。GitHub Pages 托管静态结果；计算由 Python 完成，不在浏览器中运行求解器。

## 自动发布

仓库 Settings → Pages → Build and deployment 选择 **GitHub Actions**。

`.github/workflows/pages.yml` 在推送到 `main` 或手动触发 **Publish Report** 后运行：安装固定依赖、测试、构建 Python 包、生成报告、检查文件完整性，然后发布到 Pages。拉取请求执行构建验证，不部署。

当 `reports/latest/` 不存在时，工作流用完全合成的 L 形型腔与包围盒包套执行理想参数算例。修改 `config/hip-ideal.yaml` 并推送即可更新演示报告。普通面限值 10 mm，指定角度面限值 20 mm；演示默认未指定角度面。

这个演示不使用 HIP-demo 原始 STEP，也不是实际包套成形结果。默认参数可能产生大应变或密度越界，必须结合页面上的模型适用性提示读取数值。

## 发布真实算例

1. 使用封闭包套材料 STEP 和已补偿型腔 STEP 完成本机求解。
2. 运行 `uv run hipform publish --run simulation-runs/actual-001 --output site`。
3. 检查 `site/index.html`、几何文件和 JSON，确认数据可公开。
4. 将整份 `site/` 复制为 `reports/latest/`，提交并推送到 `main`。
5. 在 Actions 查看 **Publish Report** 成功后，访问报告网址。

更新已有报告时，用新导出的完整目录替换 `reports/latest/`，避免保留旧文件。`publication.json` 记录每个文件的 SHA-256；手动修改文件后校验会失败，应重新导出。若要恢复自动演示，从仓库移除 `reports/latest/` 后推送。

导出器只接受成功完成的算例，拒绝覆盖已有目标目录及符号链接文件。它重建 HTML 并仅导出固定的结果文件，不复制原始 STEP、数值缓存或其他目录内容。来源记录保留文件名及哈希，删除绝对路径。公开网页及下载内容仍能表达工件形状，因此不能将其当作几何脱敏工具。

## 结果文件

- `index.html`：可交互的报告，内嵌 Plotly，也可离线打开。
- `predicted-powder.stl`、`reference-powder.stl`：预测与参考表面，导入时选择 mm。
- `predicted-assembly.vtu`：带位移与密度的终态体网格。
- `history.csv`：温度、压力、致密化和数值历程。
- `config.resolved.json`：完整参数快照。
- `comparison.json`、`result.json`、`mesh-info.json`、`solver.json`：可追溯的计算记录。
- `publication.json`：部署文件完整性清单。

执行完成、采样公差状态和工程验收是三个独立结果。当前模型的工程验收固定为未评估，不会因为网页成功上线而改变。
