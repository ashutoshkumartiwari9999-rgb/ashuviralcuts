"""Password-protected owner controls; public clipping routes stay open."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import storage

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".3gp", ".wmv", ".flv", ".ts"}
DEFAULT_SETTINGS = {"maintenance_mode": False, "site_notice": "", "upload_limit_mb": None, "storage_budget_mb": None, "storage_reserve_mb": None}


def attach(app: FastAPI, data: Path, state: dict) -> None:
    data = Path(data).resolve()
    settings_file = data / "owner-settings.json"
    lock = threading.RLock()

    def read_settings() -> dict:
        settings = dict(DEFAULT_SETTINGS)
        try:
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings.update({key: saved[key] for key in DEFAULT_SETTINGS if key in saved})
        except (OSError, ValueError):
            pass
        return settings

    current = read_settings()
    storage.set_admin_limits(current["upload_limit_mb"], current["storage_budget_mb"], current["storage_reserve_mb"])

    def save_settings(settings: dict) -> None:
        with lock:
            temp = settings_file.with_suffix(".tmp")
            temp.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            temp.replace(settings_file)
            current.clear()
            current.update(settings)
            storage.set_admin_limits(settings["upload_limit_mb"], settings["storage_budget_mb"], settings["storage_reserve_mb"])

    @app.middleware("http")
    async def maintenance_gate(request: Request, call_next):
        if current["maintenance_mode"] and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            path = request.url.path
            allowed = path.startswith(("/admin/", "/auth/")) or path in {"/admin", "/healthz", "/public-settings", "/youtube-cookies"}
            if not allowed:
                return JSONResponse({"detail": "AshuViralCuts is temporarily in maintenance mode. Please try again soon."}, status_code=503, headers={"Retry-After": "300"})
        return await call_next(request)

    @app.get("/public-settings")
    def public_settings():
        return {"maintenance_mode": bool(current["maintenance_mode"]), "site_notice": str(current["site_notice"] or "")}

    @app.get("/admin", response_class=HTMLResponse)
    def owner_panel():
        return HTMLResponse((Path(__file__).resolve().parent / "admin.html").read_text(encoding="utf-8"))

    @app.get("/admin/api/overview")
    def overview():
        queue = getattr(app, "processing_queue", None)
        queued = queue.rows() if queue else []
        jobs = [{key: row.get(key) for key in ("id", "kind", "status", "created", "updated", "attempts", "child")} | {"error": row.get("result", {}).get("error", ""), "queue_job": True} for row in queued]
        errors = [{"id": row["id"], "kind": row.get("kind"), "updated": row.get("updated"), "error": row.get("error", "")} for row in jobs if row.get("status") == "error"]
        known = {job["id"] for job in jobs}
        for job_id, row in list(state.items()):
            item = {"id": str(job_id), "kind": "processing", "status": row.get("status", "unknown"), "updated": row.get("updated", 0), "stage": row.get("stage") or row.get("message", ""), "progress": row.get("progress")}
            if item["id"] not in known and item["status"] in {"running", "queued", "starting", "recording", "error", "done"}:
                jobs.append(item)
            if row.get("status") == "error":
                errors.append({"id": str(job_id), "kind": "processing", "updated": row.get("updated", 0), "error": str(row.get("error", ""))[:1000]})
        jobs.sort(key=lambda item: float(item.get("updated") or item.get("created") or 0), reverse=True)
        uploads = []
        for file in data.iterdir():
            if not file.is_file() or file.suffix.lower() not in VIDEO_SUFFIXES or not re.fullmatch(r"[a-fA-F0-9]{16,64}", file.stem):
                continue
            try:
                stat = file.stat()
                uploads.append({"id": file.stem, "name": file.name, "size_bytes": stat.st_size, "modified": stat.st_mtime})
            except OSError:
                continue
        uploads.sort(key=lambda item: item["modified"], reverse=True)
        settings = dict(current)
        settings["upload_limit_mb"] = storage.upload_limit_bytes() // storage.MIB
        settings["storage_budget_mb"] = storage.budget_bytes() // storage.MIB
        settings["storage_reserve_mb"] = storage.reserve_bytes() // storage.MIB
        return {
            "jobs": jobs[:100], "uploads": uploads[:200], "errors": sorted(errors, key=lambda item: item.get("updated") or 0, reverse=True)[:100],
            "storage": storage.summary(data), "settings": settings,
            "queue_paused": queue.paused() if queue else False,
            "rate_limits": {"writes_per_ip_per_minute": 180, "upload_import_analysis_make_per_10_minutes": 8, "exports_per_10_minutes": 16, "transcriptions_per_hour": 6},
        }

    @app.post("/admin/api/settings")
    def update_settings(body: dict):
        def bounded(name: str, minimum: int, maximum: int) -> int:
            try:
                value = int(body.get(name))
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(400, f"{name} must be a whole number.")
            if not minimum <= value <= maximum:
                raise HTTPException(400, f"{name} must be between {minimum} and {maximum}.")
            return value

        upload_mb = bounded("upload_limit_mb", 10, 300)
        budget_mb = bounded("storage_budget_mb", 128, 800)
        reserve_mb = bounded("storage_reserve_mb", 32, 512)
        if reserve_mb + upload_mb > budget_mb:
            raise HTTPException(400, "Keep the upload limit plus storage reserve within the storage budget.")
        settings = {
            "maintenance_mode": bool(body.get("maintenance_mode")),
            "site_notice": str(body.get("site_notice", "")).strip()[:240],
            "upload_limit_mb": upload_mb,
            "storage_budget_mb": budget_mb,
            "storage_reserve_mb": reserve_mb,
        }
        save_settings(settings)
        return {"ok": True, "settings": settings}

    @app.post("/admin/api/queue/pause")
    def pause_queue(body: dict):
        queue = getattr(app, "processing_queue", None)
        if queue is None:
            raise HTTPException(503, "The processing queue is not available.")
        queue.pause(bool(body.get("paused")))
        return {"paused": queue.paused()}

    @app.post("/admin/api/jobs/{job}/{action}")
    def change_job(job: str, action: str):
        queue = getattr(app, "processing_queue", None)
        if queue is None:
            raise HTTPException(503, "The processing queue is not available.")
        return {key: value for key, value in queue.change(job, action).items() if key != "payload"}

    @app.post("/admin/api/storage/cleanup")
    def cleanup_temporary():
        return storage.cleanup_temporary(data, older_than_hours=24)

    @app.delete("/admin/api/uploads/{media_id}")
    def remove_upload(media_id: str):
        if not re.fullmatch(r"[a-fA-F0-9]{16,64}", media_id):
            raise HTTPException(400, "Choose a valid media item.")
        target = data / (media_id + ".mp4")
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "Media item not found.")
        target.unlink()
        for suffix in (".request.json", ".json", ".srt", ".ass", ".vtt", ".words.json", ".state.json"):
            (data / (media_id + suffix)).unlink(missing_ok=True)
        preview = data / (media_id + "-preview")
        if preview.is_dir() and not preview.is_symlink():
            shutil.rmtree(preview)
        return {"ok": True}

