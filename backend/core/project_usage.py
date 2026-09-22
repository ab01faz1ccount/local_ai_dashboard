"""
backend/core/project_usage.py

Per-project hardware-usage estimate.

The inputs are real (request counts from `request_logs`, hardware
averages from `metrics_snapshots`) but the attribution itself is
necessarily an estimate: a llama.cpp runtime is one OS process shared by
everything that talks to it, so there is no way to ask the OS "how much
of this process's CPU/RAM was just this project's doing". This
apportions a runtime's measured average load between the projects that
used it, weighted by each project's share of *that runtime's* request
volume over the same period -- the best available proxy, and an exact
number (not an estimate) whenever a runtime turns out to have been used
by only one project.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..storage.db import Chat, MetricsSnapshot, RequestLog

USAGE_NOTE = (
    "این عدد یک تخمینه: بار CPU/RAM/GPU هر runtime بر اساس سهم هر پروژه از تعداد "
    "درخواست‌های آن runtime تقسیم شده، نه اندازه‌گیری مستقیم مصرف هر پروژه به‌تنهایی. "
    "برای runtimeهایی که فقط همین پروژه ازشون استفاده کرده، عدد دقیقه."
)


def _runtime_avg_metrics(db: Session, runtime_id: int) -> dict:
    cpu, ram, gpu, vram = (
        db.query(
            func.avg(MetricsSnapshot.cpu_percent),
            func.avg(MetricsSnapshot.ram_used_mb),
            func.avg(MetricsSnapshot.gpu_percent),
            func.avg(MetricsSnapshot.vram_used_mb),
        )
        .filter(MetricsSnapshot.runtime_id == runtime_id)
        .one()
    )
    return {
        "avg_cpu_percent": round(cpu, 1) if cpu is not None else None,
        "avg_ram_used_mb": round(ram, 1) if ram is not None else None,
        "avg_gpu_percent": round(gpu, 1) if gpu is not None else None,
        "avg_vram_used_mb": round(vram, 1) if vram is not None else None,
    }


def get_project_hardware_usage(db: Session, project_id: str) -> dict:
    runtime_ids = [
        row[0]
        for row in db.query(Chat.runtime_id).filter(Chat.project_id == project_id).distinct().all()
    ]
    if not runtime_ids:
        return {
            "runtimes": [],
            "exclusive_runtime_count": 0,
            "shared_runtime_count": 0,
            "total_estimated_ram_used_mb": 0.0,
            "total_estimated_cpu_percent": 0.0,
            "note": USAGE_NOTE,
        }

    project_chat_ids = [
        c.id
        for c in db.query(Chat.id)
        .filter(Chat.project_id == project_id, Chat.runtime_id.in_(runtime_ids))
        .all()
    ]

    breakdown = []
    for runtime_id in runtime_ids:
        total_requests = (
            db.query(func.count(RequestLog.id)).filter(RequestLog.runtime_id == runtime_id).scalar() or 0
        )
        project_requests = (
            db.query(func.count(RequestLog.id))
            .filter(RequestLog.runtime_id == runtime_id, RequestLog.chat_id.in_(project_chat_ids))
            .scalar()
            or 0
        )
        share = (project_requests / total_requests) if total_requests > 0 else None
        exclusive = total_requests > 0 and project_requests == total_requests

        avg_metrics = _runtime_avg_metrics(db, runtime_id)
        estimated_metrics = {
            key.replace("avg_", "estimated_"): (
                round(value * share, 1) if (value is not None and share is not None) else None
            )
            for key, value in avg_metrics.items()
        }

        breakdown.append(
            {
                "runtime_id": runtime_id,
                "exclusive_to_project": exclusive,
                "project_requests": project_requests,
                "total_requests_on_runtime": total_requests,
                "request_share": round(share, 3) if share is not None else None,
                "runtime_avg_metrics": avg_metrics,
                "estimated_project_metrics": estimated_metrics,
            }
        )

    total_ram = sum(
        b["estimated_project_metrics"]["estimated_ram_used_mb"] or 0
        for b in breakdown
        if b["estimated_project_metrics"]["estimated_ram_used_mb"] is not None
    )
    total_cpu = sum(
        b["estimated_project_metrics"]["estimated_cpu_percent"] or 0
        for b in breakdown
        if b["estimated_project_metrics"]["estimated_cpu_percent"] is not None
    )

    return {
        "runtimes": breakdown,
        "exclusive_runtime_count": sum(1 for b in breakdown if b["exclusive_to_project"]),
        "shared_runtime_count": sum(1 for b in breakdown if not b["exclusive_to_project"]),
        "total_estimated_ram_used_mb": round(total_ram, 1),
        "total_estimated_cpu_percent": round(total_cpu, 1),
        "note": USAGE_NOTE,
    }
