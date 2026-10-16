# 智能交通时序预测与报警后端 MVP

以明尼苏达 I‑94 西向交通流量为可复现演示场景。Python/FastAPI 提供时序写入、预测、在线异常报警、告警确认、数据分析报告与图谱事实接口；PostgreSQL 保存业务记录，Neo4j Community 保存带来源锚点的检测器原理知识。系统只产生分析与告警，不向信号控制器发送控制指令。

## 当前状态

- Docker Compose 三容器已在本机运行，API：`http://127.0.0.1:27831`；交互文档：`http://127.0.0.1:27831/docs`；Neo4j Browser：`http://127.0.0.1:7474`。
- 已下载 [UCI Metro Interstate Traffic Volume](https://archive.ics.uci.edu/dataset/492/metro+interstate+traffic+volume) 和 [NAB realTraffic](https://github.com/numenta/NAB/tree/master/data/realTraffic) 数据。源文件、转换说明、SHA256 见 `data/manifest.json`。
- 预测默认在 `TIMECHO_API_KEY` 存在时调用 Timer‑3.5，失败时明确标注降级并使用本地模型；`provider=timer` 严格要求远程成功。Timer‑3.5 已用真实密钥完成连通性与 24 点抽查，本地模型在抽查中略优；详见 `docs/evaluation.md` 与 `evidence/timer_probe.json`。
- 数据分析报告为可复算 JSON 与 Markdown；LLM 文本不参与事实、数值或接线关系判断。
- 报警采用 3 小时冷却 + 回差（滞环）重新布防；交通子集标准分 81.1，超过同子集上游 numentaTM 结果 79.0，但仍属开发集结果，精确率尚未达到生产门槛；详情见 `docs/evaluation.md`。

## 从空目录复现

需要 Docker Desktop、Python 3.12、网络连接和至少约 3 GB 可用内存。PowerShell：

```powershell
python -m pip install -r requirements.txt
python scripts/fetch_data.py
python scripts/train_forecast.py
python scripts/train_direct.py
python scripts/benchmark_horizon.py
python scripts/benchmark_anomaly.py
python scripts/score_external_baseline.py
python scripts/init_env.py
docker compose up -d --build
python scripts/smoke_test.py
```

已有 `.env` 时不要重跑 `init_env.py`。可在本地 `.env` 中设置 `TIMECHO_API_KEY`，并按需设置 `APP_PORT`；不要把 `.env` 提交或打包。用户在对话中提供过密钥，部署文件没有复制这些值；建议使用后轮换。远程 Timer‑3.5 已用真实密钥测通（连通性 + 24 点抽查），SDK 0.2.6 的模拟协议验收见 `scripts/test_timer_contract.py`。

查看服务：

```powershell
docker compose ps
docker compose logs api --tail 100
docker compose exec -T api python scripts/test_timer_contract.py
docker compose exec -T api python scripts/cleanup_qa.py  # 验收后清除脚本产生的 QA-* 资产
```

## 最短业务路径

所有 `/v1/` 路由需要请求头 `X-API-Key`，取值为本机 `.env` 的 `APP_API_KEY`。Compose 启动时自动载入 I‑94 ATR 301 的一段历史数据，以及 FHWA 通用检测器示意知识。

1. `GET /v1/assets`：查看资产及量纲。
2. `POST /v1/assets/MN-DOT-ATR-301/forecast`：请求 `{"horizon":8,"provider":"local"}` 或 `"timer"`。
3. `POST /v1/assets/{id}/telemetry`：逐点流入，返回正常、历史不足、告警、冷却抑制、回差抑制或晚到状态。时间必须带时区；同时间戳、同值重试幂等，冲突值返回 409。
4. `GET /v1/alerts`、`POST /v1/alerts/{id}/acknowledge`：查询并确认报警。
5. `POST /v1/assets/{id}/reports?days=7`：生成并持久化分析报告；`GET /v1/reports/{id}` 可再读。
6. `GET /v1/knowledge/facts`：查看图谱事实的主体、关系、客体、原始摘录、图号和来源 URL。`POST` 可录入具有来源锚点的通用或站点提供事实。

新资产先用 `POST /v1/assets` 注册，再用 `/backfill` 导入历史；回填不触发报警，之后的 `/telemetry` 流入才触发因果检测。`/backfill` 最多 10,000 点/次。

## 数据与图纸准确性

UCI 的 ATR 301 是流量站点；NAB 的传感器序列是同地区其他传感器，二者**没有被当作同一设备**。FHWA《Traffic Detector Handbook》[图 2‑1](https://www.fhwa.dot.gov/publications/research/operations/its/06108/02.cfm)是**通用示意图**，不是 ATR 301 现场图纸。图谱事实保留摘录、图号、来源和 `reference` / `site_supplied` 范围；报告单列通用参考与站点提供事实。没有现场原理图时，系统不会推断本站点的接线、元件参数或故障根因。图纸 OCR、CAD 解析与人工校核尚未实现。

## 运维

- 停止服务：`docker compose stop`；重启：`docker compose up -d`。不要使用 `down -v`，它会删除业务卷。
- PostgreSQL 和 Neo4j 均持久化到本项目命名卷；升级前停止 API 并对两个卷做一致性备份。PostgreSQL 可用 `docker compose exec -T db pg_dump -U tsfm tsfm > backup.sql` 导出。Neo4j Community 的离线 dump 需要先停止图服务，详见[官方 Docker 备份文档](https://neo4j.com/docs/operations-manual/current/docker/operations/#docker-operations-dump-load)。恢复前保留旧卷快照，在隔离 Compose 项目验证备份可读。
- 更新源代码：保留旧镜像与卷备份，`docker compose build api`，`docker compose up -d --no-build api`，检查 `/health/ready` 并重跑 `scripts/smoke_test.py`。
- `.env` 权限应限制为本机运维账户；建议轮换曾经出现在聊天中的供应商密钥。

## 文档与证据

- [架构与调研](docs/architecture.md)
- [评测协议与结果](docs/evaluation.md)
- [数据清单](data/manifest.json)
- [本机验收原始结果](evidence/smoke_test.json)
- [Timer‑3.5 真实连通性探测](evidence/timer_probe.json)、[24 点抽查对比](evidence/timer_quality_spotcheck.json)
- [异常检测外部基线对比](artifacts/anomaly_external_baseline.json)、[交通子集基准](artifacts/anomaly_benchmark_t6_c3_rearm3.json)

## 许可证

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE) 授权：**允许非商业用途的使用、修改与再分发，禁止任何商业用途**。

- 许可全文见 [LICENSE](LICENSE)；官方文本：<https://polyformproject.org/licenses/noncommercial/1.0.0>
- 版权与署名：Copyright (c) 2026 王庆福（TSFM MVP 作者）。再分发任何部分时，必须一并传递本许可条款或其 URL。
- 非商业目的涵盖个人学习、研究与实验、业余项目，以及慈善、教育、公共研究、公共安全与卫生、环境保护和政府机构的使用（无论经费来源）。科研与教学场景的引用、复现、改造均被允许。
- 商业用途需另行取得书面许可。明确不属于授权范围的行为包括：将本项目或其衍生作品作为商业产品或付费服务对外提供、嵌入商业系统对外销售、以本项目为核心承揽有偿开发。
- 该许可不是 OSI 认可的开源许可。本仓库为「源码公开、禁止商用」，而非开源项目；如上条款与许可正文冲突，以许可正文为准。

## 第三方数据与许可

本仓库包含第三方数据集，其权利与许可独立于本项目的 PolyForm Noncommercial 许可：

| 来源 | 仓库内文件 | 许可与署名要求 |
| --- | --- | --- |
| [UCI Metro Interstate Traffic Volume](https://archive.ics.uci.edu/dataset/492/metro+interstate+traffic+volume) | `data/Metro_Interstate_Traffic_Volume.csv`、`data/source_metro.zip` | CC BY 4.0，需署名。数据贡献者 John Hogue，UCI Machine Learning Repository |
| [Numenta NAB](https://github.com/numenta/NAB) 的 realTraffic 序列、combined_windows 标签与 numentaTM 结果 | `data/nab/**` | MIT；原文见 `data/nab/LICENSE.txt` 与 `third_party/NAB_LICENSE.txt` |

再分发本仓库时，请一并保留上述署名与许可文件。`data/manifest.json` 记录了每个源文件的下载 URL 与 SHA256。

## 仓库与冻结快照

`evidence/manifest.json` 记录 2026-10-16 冻结时点 85 个文件的 SHA256、三个容器镜像 ID，以及发布包 `dist/tsfm-mvp-20261016.zip` 的内容清单，用于校验该包。上传至公开仓库时 `README.md` 与 `.gitignore` 有更新（新增本许可章节与忽略规则），因此二者在仓库中的当前哈希与清单记录不同；发布包内部自带冻结版本的 `README.md`、`.gitignore` 与 `evidence/manifest.json`，包内自校验保持一致。`LICENSE` 与 `.gitattributes` 为仓库新增文件，不在该清单范围内。

