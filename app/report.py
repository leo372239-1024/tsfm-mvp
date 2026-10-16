from datetime import datetime, timezone
import numpy as np


def build_report(asset, rows, alerts, graph_facts, forecast_payload):
    if not rows:
        raise ValueError("no data in report window")
    values = np.asarray([float(r["value"]) for r in rows], dtype=float)
    midpoint = len(values) // 2
    previous = float(np.mean(values[:midpoint])) if midpoint else float(values[0])
    recent = float(np.mean(values[midpoint:]))
    delta = 100 * (recent - previous) / previous if previous else None
    expected_intervals = max(1, int((rows[-1]["ts"] - rows[0]["ts"]).total_seconds() / asset["cadence_seconds"]) + 1)
    observed_unique = len({r["ts"] for r in rows})
    quality = {"observed_points": len(rows), "expected_intervals": expected_intervals,
               "completeness": round(min(1.0, observed_unique / expected_intervals), 4),
               "estimated_points": sum(r["quality"] == "estimated" for r in rows)}
    stats = {"mean": round(float(np.mean(values)), 3), "median": round(float(np.median(values)), 3),
             "minimum": round(float(np.min(values)), 3), "maximum": round(float(np.max(values)), 3),
             "p95": round(float(np.percentile(values, 95)), 3),
             "recent_half_change_pct": round(delta, 2) if delta is not None else None}
    alert_summary = {"total": len(alerts), "open": sum(a["state"] == "open" for a in alerts),
                     "acknowledged": sum(a["state"] == "acknowledged" for a in alerts)}
    linked_facts = [f for f in graph_facts if f["scope"] == "site_supplied" and
                    (f["subject_id"] == asset["id"] or f["object_id"] == asset["id"])]
    # Reference facts are kept separate from station facts to avoid implying actual wiring.
    reference_facts = [f for f in graph_facts if f["scope"] == "reference"]
    notes = ["统计只使用已落库观测值，不以预测值代替实测值。",
             "FHWA 参考拓扑仅为示意，未核实为本站点的实际接线。"]
    if quality["completeness"] < 0.95:
        notes.append("缺失时段超过 5%，预测和报警覆盖率应谨慎解读。")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
            "asset": {k: asset[k] for k in ("id", "name", "metric", "unit", "cadence_seconds", "source_uri")},
            "window": {"start": rows[0]["ts"].isoformat(), "end": rows[-1]["ts"].isoformat()},
            "data_quality": quality, "statistics": stats, "alerts": alert_summary,
            "forecast": forecast_payload, "site_supplied_facts": linked_facts,
            "reference_facts": reference_facts, "limitations": notes}
    report["markdown"] = render_markdown(report)
    return report


def render_markdown(report):
    asset = report["asset"]
    s = report["statistics"]
    q = report["data_quality"]
    alerts = report["alerts"]
    fc = report["forecast"]
    lines = [f"# {asset['name']} 数据分析报告", "",
             f"时间范围：{report['window']['start']} 至 {report['window']['end']}",
             f"指标：{asset['metric']}（{asset['unit']}）", "",
             "## 数据质量", "",
             f"实测/记录点数：{q['observed_points']}；预期时段：{q['expected_intervals']}；完整率：{q['completeness']:.1%}；估计值：{q['estimated_points']}。", "",
             "## 统计与变化", "",
             f"均值 {s['mean']:.3f}，中位数 {s['median']:.3f}，最小 {s['minimum']:.3f}，最大 {s['maximum']:.3f}，P95 {s['p95']:.3f} {asset['unit']}。",
             f"后半段相对前半段均值变化：{s['recent_half_change_pct']:.2f}%。" if s['recent_half_change_pct'] is not None else "前半段均值为零，百分比变化未定义。", "",
             "## 告警与预测", "",
             f"窗口内告警 {alerts['total']} 条，其中待确认 {alerts['open']} 条、已确认 {alerts['acknowledged']} 条。"]
    if fc:
        lines.extend([f"预测模型：{fc['model']}；未来 {len(fc['points'])} 个点。",
                      "预测值：" + "，".join(f"{p['ts']}={p['value']}" for p in fc["points"]) + "。"])
    else:
        lines.append("历史长度或质量不足，未生成预测。")
    lines.extend(["", "## 图谱来源与边界", "",
                  f"站点提供的事实：{len(report['site_supplied_facts'])} 条；FHWA 通用参考事实：{len(report['reference_facts'])} 条。",
                  "FHWA 图 2-1 是通用示意，不能据此断定本站点真实接线。",
                  f"数据来源：{asset['source_uri'] or '未提供'}。",
                  "参考来源：https://www.fhwa.dot.gov/publications/research/operations/its/06108/02.cfm#fig2-1", "",
                  "## 限制", ""])
    lines.extend(f"- {note}" for note in report["limitations"])
    return "\n".join(lines)

