import asyncio
import tempfile
import unittest
import uuid

import httpx
from fastapi import FastAPI

import privacy


class VisitorPrivacyTests(unittest.TestCase):
    def test_video_and_library_are_scoped_to_the_browser_session(self):
        root = tempfile.TemporaryDirectory(prefix="ashuviralcuts-privacy-test-")
        try:
            app = FastAPI()
            media_id = uuid.uuid4().hex

            @app.post("/create")
            async def create():
                return {"id": media_id}

            @app.get("/library")
            async def library():
                return [{"id": media_id, "title": "Private clip"}]

            @app.get("/media/{id}")
            async def media(id: str):
                return {"id": id, "video": "private"}

            @app.post("/export")
            async def export(body: dict):
                return {"ok": True}

            privacy.attach(app, root.name)

            async def exercise():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as owner:
                    created = await owner.post("/create")
                    self.assertEqual(created.status_code, 200)
                    self.assertEqual(created.json()["id"], media_id)
                    own_library = await owner.get("/library")
                    self.assertEqual([item["id"] for item in own_library.json()], [media_id])
                    own_media = await owner.get(f"/media/{media_id}")
                    self.assertEqual(own_media.status_code, 200)

                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as visitor:
                        other_library = await visitor.get("/library")
                        self.assertEqual(other_library.json(), [])
                        other_media = await visitor.get(f"/media/{media_id}")
                        self.assertEqual(other_media.status_code, 404)
                        cross_export = await visitor.post("/export", json={"id": media_id})
                        self.assertEqual(cross_export.status_code, 404)

            asyncio.run(exercise())
        finally:
            root.cleanup()


if __name__ == "__main__":
    unittest.main()
